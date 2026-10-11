import asyncio
import queue
import threading
from datetime import datetime

import numpy as np
import pytest
from mutagen.flac import FLAC

from velvetstream.core import FlacSink, TrackMetadata
from velvetstream.recognition import RecognitionRequest, RecognitionTicket
from velvetstream.workers import MetadataWorker


class Primary:
    def __init__(self, answer=None, error=False):
        self.answer, self.error, self.calls = answer, error, 0

    async def recognize(self, pcm, rate, stop):
        self.calls += 1
        if self.error:
            raise TimeoutError()
        return self.answer


class Secondary:
    available = True
    unavailable_reason = ""

    def __init__(self, answer=None, error=False):
        self.answer, self.error, self.calls = answer, error, 0

    async def recognize(self, blocks, rate, stop):
        self.calls += 1
        assert sum(len(b) for b in blocks) == 30 * rate
        if self.error:
            raise OSError()
        return self.answer


def request(large=True):
    ticket = RecognitionTicket(TrackMetadata.recording(datetime(2026, 10, 9, 12, 30, 0)))
    if large:
        ticket.offer_window((np.full((300, 2), 0.25),))
    ticket.end_audio()
    return RecognitionRequest(ticket, np.full((40, 2), 0.25), 10)


def run_chain(primary, secondary, req):
    jobs = queue.Queue()
    jobs.put(req)
    jobs.put(None)
    worker = MetadataWorker(threading.Event(), jobs, engine=primary, secondary=secondary)
    logs = []
    worker.log.connect(logs.append)
    worker.execute()
    assert jobs.unfinished_tasks == 0
    return req.ticket.seal(0), logs


def test_primary_match_stops_secondary(qtbot):
    secondary = Secondary(TrackMetadata("Wrong", "Wrong"))
    result, logs = run_chain(Primary(TrackMetadata("Song", "Artist")), secondary, request())
    assert result.title == "Song" and secondary.calls == 0
    assert any("Shazam" in line for line in logs)


@pytest.mark.parametrize("primary_error", [False, True])
def test_primary_failure_uses_secondary(qtbot, primary_error):
    secondary = Secondary(TrackMetadata("Official", "Artist", "Album", "2020"))
    result, logs = run_chain(Primary(error=primary_error), secondary, request())
    assert result.title == "Official" and secondary.calls == 1
    assert any("AcoustID Match Found" in line for line in logs)


def test_both_failures_fall_back_without_more_requests(qtbot):
    result, logs = run_chain(Primary(error=True), Secondary(error=True), request())
    assert result.recording_filename == "Recording_20261009_123000.flac"
    assert any("manual" in line.lower() for line in logs)


def test_short_track_never_queries_acoustid(qtbot):
    secondary = Secondary()
    result, _ = run_chain(Primary(), secondary, request(large=False))
    assert secondary.calls == 0 and result.recording_filename


def test_secondary_waits_for_its_own_thirty_second_window(qtbot):
    ticket = RecognitionTicket(TrackMetadata.recording())
    secondary = Secondary(TrackMetadata("Ready", "Artist"))
    worker = MetadataWorker(threading.Event(), queue.Queue(), engine=Primary(), secondary=secondary)

    async def probe():
        task = asyncio.create_task(worker.lookup(RecognitionRequest(ticket, np.ones((40, 2)), 10)))
        await asyncio.sleep(0.08)
        assert secondary.calls == 0 and not ticket.done.is_set()
        ticket.offer_window((np.ones((300, 2)),))
        await asyncio.wait_for(task, timeout=1)

    asyncio.run(probe())
    assert ticket.seal(0).title == "Ready"


def test_thirty_second_collector_bounds_memory_without_capture_concatenation():
    from velvetstream.recognition import ThirtySecondWindow

    collector = ThirtySecondWindow(10)
    assert collector.add(np.ones((123, 2))) is None
    blocks = collector.add(np.full((200, 2), 0.5))
    assert isinstance(blocks, tuple) and sum(len(b) for b in blocks) == 300
    assert collector.blocks == [] and collector.add(np.ones((10, 2))) is None


def test_manual_retag_preserves_audio_and_handles_filename_collision(tmp_path):
    from velvetstream.manual import retag_recording

    sink = FlacSink(tmp_path, 48000, 2, TrackMetadata.recording())
    sink.write(np.full((100, 2), 0.25))
    original = sink.finish()
    metadata = TrackMetadata("Song", "Singer", "Album", "2023")
    collision = tmp_path / "Singer - Song.flac"
    collision.write_bytes(b"Keep this file")
    target = retag_recording(original, metadata, threading.Event())
    assert target.parent.name == "Duplicate 2" and not original.exists()
    assert collision.read_bytes() == b"Keep this file"
    assert FLAC(target)["album"] == ["Album"]
    import soundfile as sf

    audio, rate = sf.read(target)
    assert rate == 48000 and np.all(audio == 0.25)


def test_slow_lookup_cannot_block_capture_or_gui(qtbot, tmp_path, monkeypatch):
    import time

    import soundcard
    import soundfile as sf
    from PySide6.QtCore import Qt, QTimer

    from velvetstream.ui import JobController
    from velvetstream.workers import CaptureWorker, FinalizeWorker

    stop, lookup_started = threading.Event(), threading.Event()
    reads_while_waiting, pulses = [], []

    class SlowPrimary:
        async def recognize(self, pcm, rate, stop):
            lookup_started.set()
            await asyncio.sleep(10)
            return None

    class Recorder:
        count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def record(self, numframes):
            time.sleep(0.001)
            self.count += 1
            if lookup_started.is_set():
                reads_while_waiting.append(self.count)
            if self.count == 360:
                stop.set()
            return np.full((numframes, 2), 0.25, dtype=np.float32)

    class Device:
        id, name, isloopback = "output", "Loopback", True

        def recorder(self, **kwargs):
            return Recorder()

    monkeypatch.setattr(soundcard, "all_microphones", lambda **kwargs: [Device()])
    jobs, requests = queue.Queue(maxsize=8), queue.Queue(maxsize=8)
    metadata = MetadataWorker(stop, requests, engine=SlowPrimary(), secondary=Secondary())
    capture = CaptureWorker(stop, "output", tmp_path, 48000, -45, 1.0, jobs, None)
    capture.recognition_requested.connect(metadata.submit, Qt.ConnectionType.DirectConnection)
    controller = JobController()
    controller.start("finalize", FinalizeWorker(stop, jobs))
    controller.start("metadata", metadata)
    controller.start("capture", capture)
    timer = QTimer()
    timer.timeout.connect(lambda: pulses.append(lookup_started.is_set()))
    timer.start(5)
    qtbot.waitUntil(lambda: not controller.busy, timeout=4000)
    timer.stop()
    assert len(reads_while_waiting) > 100 and sum(pulses) > 10
    saved = list(tmp_path.glob("Recording_*.flac"))
    assert len(saved) == 1 and sf.info(saved[0]).frames == 360 * 2400
