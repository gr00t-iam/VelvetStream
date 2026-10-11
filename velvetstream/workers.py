"""QObject workers. All blocking services execute in owned QThreads."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot

from .core import FlacSink, Segmenter, TrackMetadata, dbfs, prepare_output
from .fallback import AcoustIDRecognizer
from .manual import retag_recording
from .recognition import (
    CompletedRecording,
    FourSecondSample,
    RecognitionRequest,
    RecognitionTicket,
    ShazamRecognizer,
    ThirtySecondWindow,
    WindowRequest,
)
from .services import (
    Cancelled,
    SkipTrack,
    YouTubeDownloader,
    check_cancel,
    download_art,
    loopback_devices,
    resolve_loopback,
)


class Worker(QObject):
    finished = Signal()
    error = Signal(str)
    log = Signal(str)
    result = Signal(object)
    state = Signal(str)
    progress = Signal(int)
    level = Signal(float)
    manual_required = Signal(object)
    recording_saved = Signal(object)

    def __init__(self, stop: threading.Event):
        super().__init__()
        self.stop = stop

    @Slot()
    def run(self):
        try:
            result = self.execute()
            if result is not None:
                self.result.emit(result)
        except Cancelled:
            self.log.emit("Operation cancelled")
        except Exception as exc:
            # Report failure while always completing the Qt worker lifecycle.
            self.error.emit(str(exc) or type(exc).__name__)
        finally:
            self.finished.emit()

    def execute(self):
        raise NotImplementedError("Worker subclasses implement execute")


class DeviceWorker(Worker):
    def execute(self):
        return loopback_devices()


class FinalizeWorker(Worker):
    def __init__(self, stop, jobs: queue.Queue):
        super().__init__(stop)
        self.jobs = jobs

    def execute(self):
        saved = failed = 0
        # Stop ends acquisition, but completed audio still must be tagged/published.
        # The capture producer sends the sentinel after closing its final sink.
        while True:
            item = self.jobs.get()
            try:
                if item is None:
                    break
                if isinstance(item, CompletedRecording):
                    sink = item.sink
                    sink.metadata = item.ticket.seal(timeout=50.0)
                    if sink.metadata.recording_filename:
                        self.log.emit(f"Timestamp fallback: {sink.metadata.recording_filename}")
                else:
                    sink = item
                art = None
                if sink.metadata.art_url:
                    try:
                        # Completed recordings retain artwork even after acquisition stops.
                        art = download_art(sink.metadata.art_url, threading.Event())
                    except Exception:
                        self.log.emit("Cover art unavailable; saving audio and text tags")
                try:
                    try:
                        path = sink.finish(art)
                    except (ValueError, OSError):
                        if not art:
                            raise
                        self.log.emit("Cover art could not be embedded; retrying text tags")
                        path = sink.finish()
                    saved += 1
                    self.log.emit(f"Saved: {path}")
                    self.recording_saved.emit(path)
                    if sink.metadata.recording_filename:
                        self.manual_required.emit(path)
                except Exception as exc:
                    failed += 1
                    self.error.emit(
                        f"FLAC finalization failed ({type(exc).__name__}). Recoverable audio: {sink.path}"
                    )
            finally:
                self.jobs.task_done()
        self.log.emit(f"Capture finished: {saved} saved, {failed} failed")
        return {"saved": saved, "failed": failed}


class YouTubeWorker(Worker):
    def __init__(self, stop, url: str, output: Path):
        super().__init__(stop)
        self.url, self.output = url, output
        self.skip = threading.Event()

    def execute(self):
        try:
            saved, failed = YouTubeDownloader(
                self.stop,
                self.log.emit,
                self.state.emit,
                self.progress.emit,
                skip=self.skip,
                saved=self.recording_saved.emit,
            ).run(self.url, self.output)
        except SkipTrack:
            self.log.emit("Playlist loading skipped; use Retry / resume when ready")
            return {"saved": 0, "failed": 0}
        except (Cancelled, ValueError, OSError):
            raise
        except Exception as exc:
            raise RuntimeError(
                f"YouTube extraction failed ({type(exc).__name__}). Check URL, network, yt-dlp/EJS runtime, and FFmpeg setup."
            ) from exc
        if failed or saved == 0:
            self.error.emit(f"Extraction completed with {saved} saved and {failed} failed entries")
        return {"saved": saved, "failed": failed}


class MetadataWorker(Worker):
    """Thread C: a dedicated asyncio loop consumes per-segment PCM requests."""

    def __init__(self, stop, requests: queue.Queue, engine=None, secondary=None):
        super().__init__(stop)
        self.requests, self.engine = requests, engine
        self.secondary = secondary
        self.input_closed = threading.Event()

    @Slot(object)
    def submit(self, item):
        """Direct-connection mailbox slot: no awaits, disk I/O or network calls.

        QObject's Qt event loop is occupied by its asyncio loop; a queued Qt
        slot would never drain. This explicitly thread-safe slot does nothing
        except publish bounded data, then returns to the audio producer.
        """
        if item is None:
            self.input_closed.set()
        elif isinstance(item, WindowRequest):
            item.ticket.offer_window(item.blocks)
        else:
            try:
                self.requests.put_nowait(item)
            except queue.Full:
                item.ticket.resolve(None)
                self.log.emit("Metadata queue busy; timestamp and manual tagging used")

    def execute(self):
        return asyncio.run(self.process_requests())

    async def process_requests(self):
        self.engine = self.engine or ShazamRecognizer()
        self.secondary = self.secondary or AcoustIDRecognizer()
        tasks = set()

        async def dispatch(request):
            try:
                await self.lookup(request)
            finally:
                self.requests.task_done()

        try:
            while True:
                finished = {task for task in tasks if task.done()}
                for task in finished:
                    task.result()
                tasks -= finished
                while len(tasks) < 8:
                    try:
                        request = self.requests.get_nowait()
                    except queue.Empty:
                        break
                    if request is None:
                        self.input_closed.set()
                        self.requests.task_done()
                    else:
                        tasks.add(asyncio.create_task(dispatch(request)))
                if self.input_closed.is_set() and self.requests.empty() and not tasks:
                    break
                await asyncio.sleep(0.02)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def lookup(self, request):
        ticket = request.ticket
        self.engine = self.engine or ShazamRecognizer()
        self.secondary = self.secondary or AcoustIDRecognizer()
        if ticket.done.is_set() or self.stop.is_set():
            ticket.resolve(None)
            return

        async def chain():
            self.log.emit(f"Tier 1 · Shazam: {ticket.fallback.title}")
            try:
                async with asyncio.timeout(15):
                    metadata = await self.engine.recognize(request.pcm, request.rate, self.stop)
                if metadata:
                    return metadata, "Shazam Match Found"
            except Exception as exc:
                self.log.emit(f"Shazam unavailable ({type(exc).__name__}); moving to Tier 2")
            check_cancel(self.stop)
            if not self.secondary.available:
                self.log.emit(f"Tier 2 skipped: {self.secondary.unavailable_reason}")
                return None, ""
            self.log.emit("Tier 2 · waiting for this track's 30-second audio window")
            deadline = time.monotonic() + 32
            while not ticket.window_ready.is_set():
                check_cancel(self.stop)
                if (
                    ticket.audio_ended.is_set()
                    or ticket.done.is_set()
                    or time.monotonic() > deadline
                ):
                    return None, ""
                await asyncio.sleep(0.05)
            blocks = ticket.take_window()
            if not blocks:
                return None, ""
            if getattr(self.secondary, "needs_track_duration", False):
                self.log.emit(
                    "Generating Chromaprint; AcoustID lookup waits for full track duration"
                )
                secondary_task = asyncio.create_task(
                    self.secondary.recognize(blocks, request.rate, self.stop, ticket=ticket)
                )
                del blocks
                metadata = await secondary_task
            else:
                async with asyncio.timeout(40):
                    metadata = await self.secondary.recognize(blocks, request.rate, self.stop)
            return metadata, "AcoustID Match Found"

        task = asyncio.create_task(chain())
        try:
            while not task.done():
                check_cancel(self.stop)
                if ticket.done.is_set():
                    return
                await asyncio.wait({task}, timeout=0.05)
            metadata, source = await task
            if ticket.resolve(metadata):
                if metadata:
                    self.log.emit(
                        f"{source}: Title={metadata.title} | Artist={metadata.artist} | "
                        f"Album={metadata.album or 'Unavailable'} | Year={metadata.year or 'Unavailable'} | "
                        f"Cover={metadata.art_url or 'Unavailable'}"
                    )
                else:
                    self.log.emit(
                        f"Tier 3 · {ticket.fallback.recording_filename}; manual tags available after save"
                    )
        except Exception as exc:
            ticket.resolve(None)
            self.log.emit(
                f"Tier 3 · lookup ended ({type(exc).__name__}); "
                f"{ticket.fallback.recording_filename}; manual tags available after save"
            )
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if not ticket.done.is_set():
                ticket.resolve(None)


class ManualTagWorker(Worker):
    def __init__(self, stop, path: Path, metadata: TrackMetadata):
        super().__init__(stop)
        self.path, self.metadata = path, metadata

    def execute(self):
        target = retag_recording(self.path, self.metadata, self.stop)
        self.log.emit(f"Manual tags saved: {target}")
        self.recording_saved.emit(target)
        return {"original": self.path, "path": target}


class CaptureWorker(Worker):
    """Thread B: audio writes and bounded PCM copies; no network work."""

    recognition_requested = Signal(object)

    def __init__(
        self,
        stop,
        device_id: str,
        output: Path,
        rate: int,
        threshold: float,
        duration: float,
        jobs: queue.Queue,
        recognition_jobs: queue.Queue | None,
    ):
        super().__init__(stop)
        self.device_id, self.output, self.rate = device_id, output, rate
        self.threshold, self.duration = threshold, duration
        self.jobs, self.recognition_jobs = jobs, recognition_jobs
        self.sink = None
        self.ticket = None
        self.sample = None
        self.long_sample = None
        self.submitted = False
        if recognition_jobs is not None:
            self.recognition_requested.connect(
                self._queue_handoff, Qt.ConnectionType.DirectConnection
            )

    @Slot(object)
    def _queue_handoff(self, item):
        # Headless/test producer adapter; the GUI connects directly to Thread C.
        if isinstance(item, WindowRequest):
            item.ticket.offer_window(item.blocks)
        elif item is None:
            self.recognition_jobs.put(None)  # Acquisition has already ended.
        else:
            try:
                self.recognition_jobs.put_nowait(item)
            except queue.Full:
                item.ticket.resolve(None)
                self.log.emit(
                    "Recognition queue busy; timestamp fallback used without pausing capture"
                )

    def begin_segment(self):
        fallback = TrackMetadata.recording()
        self.ticket = RecognitionTicket(fallback)
        self.sample = FourSecondSample(self.rate)
        self.long_sample = ThirtySecondWindow(self.rate)
        self.submitted = False
        self.sink = FlacSink(self.output, self.rate, 2, fallback)
        self.log.emit(
            f"Segment started: {fallback.title}; collecting four-second recognition sample"
        )

    def write_segment(self, block):
        self.sink.write(block)
        if self.ticket.done.is_set():
            self.sample = self.long_sample = None
            return
        if not self.submitted:
            pcm = self.sample.add(block)
            if pcm is not None:
                self.submitted = True
                self.recognition_requested.emit(RecognitionRequest(self.ticket, pcm, self.rate))
                self.sample = None
        if self.long_sample is not None and not self.ticket.done.is_set():
            blocks = self.long_sample.add(block)
            if blocks is not None:
                self.recognition_requested.emit(WindowRequest(self.ticket, blocks))
                self.long_sample = None

    def end_segment(self):
        if self.sink is None:
            return
        self.sink.close()
        self.ticket.end_audio(self.sink.frames / self.rate)
        if not self.submitted:
            self.ticket.resolve(None)
            self.log.emit("Segment shorter than four seconds; saving timestamped recording")
        try:
            self.jobs.put_nowait(CompletedRecording(self.sink, self.ticket))
        except queue.Full as exc:
            raise RuntimeError(
                f"Finalization backlog is full. Capture stopped to preserve audio: {self.sink.path}"
            ) from exc
        self.sink = self.ticket = self.sample = self.long_sample = None

    def execute(self):
        try:
            import soundcard as sc

            segmenter = Segmenter(
                self.rate,
                self.threshold,
                self.duration,
                self.begin_segment,
                self.write_segment,
                self.end_segment,
            )
            self.output = prepare_output(self.output)
            check_cancel(self.stop)
            mic = resolve_loopback(self.device_id, sc)
            block_frames = round(self.rate * 0.05)
            last_clip = 0.0
            with mic.recorder(samplerate=self.rate, channels=2, blocksize=block_frames) as recorder:
                self.state.emit("Recording")
                self.log.emit(f"Capturing stereo system output at {self.rate} Hz / PCM_24 FLAC")
                self.log.emit(
                    "Automatic tags: Shazam (4 seconds), then AcoustID (30 seconds), then manual tags"
                )
                while not self.stop.is_set():
                    block = recorder.record(numframes=block_frames)
                    peak = dbfs(block, peak=True)
                    self.level.emit(peak)
                    if peak >= 0 and time.monotonic() - last_clip > 5:
                        last_clip = time.monotonic()
                        self.log.emit("Clipping detected. Reduce system/application volume.")
                    segmenter.feed(block)
            segmenter.finish()
        finally:
            self.stop.set()
            try:
                if self.ticket is not None:
                    self.ticket.resolve(None)
                if self.sink is not None:
                    self.log.emit(f"Interrupted audio retained for recovery: {self.sink.path}")
                    self.sink.close()
            finally:
                self.level.emit(-120.0)
                try:
                    self.recognition_requested.emit(None)
                finally:
                    self.jobs.put(None)
