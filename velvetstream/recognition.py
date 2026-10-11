"""Four-second sample handoff and asynchronous, account-free Shazam tagging."""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass
from io import BytesIO

import numpy as np
import soundfile as sf

from .core import FlacSink, TrackMetadata
from .services import check_cancel


def metadata_from_shazam(answer: dict) -> TrackMetadata | None:
    if not isinstance(answer, dict) or ("matches" in answer and not answer["matches"]):
        return None
    track = answer.get("track") or {}
    title, artist = str(track.get("title") or "").strip(), str(track.get("subtitle") or "").strip()
    if not title or not artist:
        return None
    fields = {}
    for section in track.get("sections") or []:
        if section.get("type") == "METADATA":
            for row in section.get("metadata") or []:
                fields[str(row.get("title") or "").strip().casefold()] = str(
                    row.get("text") or ""
                ).strip()
    released = fields.get("released") or fields.get("release date") or fields.get("year") or ""
    year = re.search(r"\b(?:18|19|20|21)\d{2}\b", released)
    images = track.get("images") or {}
    return TrackMetadata(
        title=title,
        artist=artist,
        album=fields.get("album", ""),
        year=year.group() if year else "",
        art_url=images.get("coverarthq") or images.get("coverart") or "",
        source_url=track.get("url") or "",
        source_id=str(track.get("key") or ""),
        comment="System output capture; metadata identified with Shazam from first four seconds",
    )


class FourSecondSample:
    """Capture-side memory collector. Exact four seconds, one handoff per segment."""

    def __init__(self, rate: int):
        if rate <= 0:
            raise ValueError("Sample rate must be positive")
        self.limit = rate * 4
        self.buffered_frames = 0
        self.blocks = []
        self.sent = False

    def add(self, block: np.ndarray) -> np.ndarray | None:
        if self.sent:
            return None
        count = min(len(block), self.limit - self.buffered_frames)
        if count:
            self.blocks.append(np.asarray(block[:count], dtype=np.float32).copy())
            self.buffered_frames += count
        if self.buffered_frames < self.limit:
            return None
        pcm = np.concatenate(self.blocks)
        self.blocks.clear()
        self.buffered_frames = 0
        self.sent = True
        return pcm


class RecognitionTicket:
    """Per-recording result mailbox. Sealing prevents late results altering output."""

    def __init__(self, fallback: TrackMetadata):
        self.fallback = fallback
        self._metadata = fallback
        self._lock = threading.Lock()
        self.done = threading.Event()
        self._sealed = False
        self.window_ready = threading.Event()
        self.audio_ended = threading.Event()
        self._window = None
        self.duration = 0.0

    def offer_window(self, blocks: tuple[np.ndarray, ...]):
        with self._lock:
            if not self.done.is_set():
                self._window = blocks
                self.window_ready.set()

    def take_window(self):
        with self._lock:
            blocks, self._window = self._window, None
            return blocks

    def end_audio(self, duration: float = 0.0):
        self.duration = duration
        self.audio_ended.set()

    def resolve(self, metadata: TrackMetadata | None) -> bool:
        with self._lock:
            if self._sealed or self.done.is_set():
                return False
            self._metadata = metadata or self.fallback
            self._window = None
            self.done.set()
            return True

    def seal(self, timeout: float = 20.0) -> TrackMetadata:
        self.done.wait(timeout)
        with self._lock:
            self._sealed = True
            self._window = None
            self.done.set()
            return self._metadata


class ThirtySecondWindow:
    """Bounded block collector; concatenation/fingerprinting happens off capture."""

    def __init__(self, rate: int):
        if rate <= 0:
            raise ValueError("Sample rate must be positive")
        self.limit, self.frames = 30 * rate, 0
        self.blocks = []
        self.sent = False

    def add(self, block: np.ndarray) -> tuple[np.ndarray, ...] | None:
        if self.sent:
            return None
        count = min(len(block), self.limit - self.frames)
        if count:
            copied = np.asarray(block[:count], dtype=np.float32).copy()
            copied.setflags(write=False)
            self.blocks.append(copied)
            self.frames += count
        if self.frames < self.limit:
            return None
        blocks = tuple(self.blocks)
        self.blocks.clear()
        self.sent = True
        return blocks


@dataclass(frozen=True)
class WindowRequest:
    ticket: RecognitionTicket
    blocks: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class RecognitionRequest:
    ticket: RecognitionTicket
    pcm: np.ndarray
    rate: int


@dataclass(frozen=True)
class CompletedRecording:
    sink: FlacSink
    ticket: RecognitionTicket


def _shazam_client():
    # 0.8.1 closes its RetryClient per request; no GUI-side event loop is needed.
    from aiohttp_retry import ExponentialRetry
    from shazamio import HTTPClient, Shazam

    return Shazam(
        http_client=HTTPClient(
            retry_options=ExponentialRetry(
                attempts=2,
                max_timeout=2,
                statuses={429, 500, 502, 503, 504},
            )
        ),
        segment_duration_seconds=4,
    )


class ShazamRecognizer:
    def __init__(self, factory=_shazam_client, timeout: float = 15.0):
        self.factory, self.timeout = factory, timeout

    async def recognize(
        self, pcm: np.ndarray, rate: int, stop: threading.Event
    ) -> TrackMetadata | None:
        check_cancel(stop)
        if pcm.ndim != 2 or not np.isfinite(pcm).all():
            raise ValueError("Invalid recognition audio")
        memory = BytesIO()
        # Shazamio accepts encoded audio bytes, not a float32 ndarray's raw memory.
        # Only the fingerprint sample uses PCM_16; the recorded FLAC stays PCM_24.
        sf.write(memory, pcm, rate, format="WAV", subtype="PCM_16")
        client = self.factory()
        task = asyncio.create_task(client.recognize(memory.getvalue()))
        try:
            async with asyncio.timeout(self.timeout):
                while not task.done():
                    check_cancel(stop)
                    await asyncio.wait({task}, timeout=0.1)
                answer = await task
            return metadata_from_shazam(answer)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            close = getattr(client, "close", None)
            if close is not None:
                await close()
