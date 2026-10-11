"""Streaming segmentation, metadata, and transactional FLAC output.

This module has no Qt or device dependencies; audio tests use actual encoders.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import numpy as np
import soundfile as sf
from mutagen.flac import FLAC, Picture
from PIL import Image


@dataclass(frozen=True)
class TrackMetadata:
    title: str
    artist: str
    album: str = ""
    year: str = ""
    source_url: str = ""
    source_id: str = ""
    track_number: str = ""
    art_url: str = ""
    comment: str = ""
    recording_filename: str = ""

    @classmethod
    def unknown(cls) -> TrackMetadata:
        return cls.recording()

    @classmethod
    def recording(cls, at: datetime | None = None) -> TrackMetadata:
        stamp = (at or datetime.now()).strftime("Recording_%Y%m%d_%H%M%S")
        return cls(
            stamp,
            "Unknown Artist",
            comment="Audio recognition unavailable or unmatched",
            recording_filename=stamp + ".flac",
        )


def safe_component(value: str) -> str:
    value = unicodedata.normalize("NFC", str(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value).strip(" .") or "Unknown"
    if value.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
    }:
        value = "_" + value
    value = value.encode("utf-8")[:100].decode("utf-8", errors="ignore").rstrip(" .")
    return value or "Unknown"


def filename(metadata: TrackMetadata) -> str:
    if metadata.recording_filename:
        return safe_component(metadata.recording_filename.removesuffix(".flac")) + ".flac"
    return f"{safe_component(metadata.artist)} - {safe_component(metadata.title)}.flac"


def validate_youtube_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
        }
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or not parsed.path
        or len(url) > 4096
        or any(ord(c) < 32 for c in url)
    ):
        raise ValueError("Enter an HTTPS YouTube video or playlist URL.")
    return url


def youtube_target(url: str) -> tuple[str, bool]:
    """A selected video takes precedence over attached playlist/radio context."""
    parsed = urlparse(validate_youtube_url(url))
    query = parse_qs(parsed.query)
    parts = parsed.path.strip("/").split("/")
    video = (query.get("v") or [""])[0]
    if parsed.hostname == "youtu.be":
        video = parts[0]
    elif len(parts) == 2 and parts[0] in {"shorts", "embed", "live"}:
        video = parts[1]
    if video and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", video):
        return "https://www.youtube.com/watch?" + urlencode({"v": video}), False
    playlist = (query.get("list") or [""])[0]
    if parsed.path.rstrip("/") == "/playlist" and playlist:
        return "https://www.youtube.com/playlist?" + urlencode({"list": playlist}), True
    raise ValueError("Paste a specific YouTube video link or an explicit /playlist link.")


def metadata_from_youtube(info: dict) -> TrackMetadata:
    artist = info.get("artist") or info.get("channel") or info.get("uploader") or "Unknown Artist"
    title = info.get("track") or info.get("title") or f"Video {info.get('id', 'unknown')}"
    date = str(info.get("release_year") or info.get("upload_date") or "")
    thumbs = sorted(
        info.get("thumbnails") or [],
        key=lambda t: (
            (t.get("width") or 0) * (t.get("height") or 0),
            t.get("preference") or 0,
        ),
        reverse=True,
    )
    art = next((t.get("url") for t in thumbs if t.get("url")), info.get("thumbnail", ""))
    return TrackMetadata(
        title=str(title),
        artist=str(artist),
        album=str(info.get("album") or ""),
        year=date[:4] if re.match(r"^\d{4}", date) else "",
        source_url=info.get("webpage_url") or "",
        source_id=info.get("id") or "",
        track_number=str(info.get("playlist_index") or ""),
        art_url=art or "",
        comment="Best available source decoded to FLAC; source may be lossy",
    )


def dbfs(block: np.ndarray, *, peak: bool = False) -> float:
    if not block.size:
        return -120.0
    amplitude = (
        float(np.max(np.abs(block)))
        if peak
        else float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    )
    return max(-120.0, 20 * math.log10(max(amplitude, 1e-6)))


class Segmenter:
    """Bounded block-RMS silence splitter. Caller supplies ordered sink callbacks.

    Long silence is removed; short silence is preserved exactly. Pre-roll includes
    quiet samples just before an onset. Boundaries have capture-block resolution.
    """

    def __init__(
        self,
        sample_rate: int,
        threshold_db: float,
        duration: float,
        on_start: Callable,
        on_data: Callable,
        on_end: Callable,
        pre_roll: float = 0.15,
    ):
        if sample_rate <= 0 or not -100 <= threshold_db <= 0 or not 0.1 <= duration <= 30:
            raise ValueError("Invalid segmentation parameters")
        self.limit = max(1, round(sample_rate * duration))
        self.pre_limit = max(0, round(sample_rate * pre_roll))
        self.threshold = threshold_db
        self.start, self.data, self.end = on_start, on_data, on_end
        self.active = False
        self.pending: list[np.ndarray] = []
        self.pending_frames = 0
        self.pre: np.ndarray | None = None

    def _remember(self, block: np.ndarray):
        if self.pre_limit:
            tail = block[-self.pre_limit :]
            self.pre = (
                tail.copy()
                if self.pre is None
                else np.concatenate((self.pre, tail))[-self.pre_limit :]
            )

    def feed(self, block: np.ndarray):
        block = np.asarray(block)
        if block.ndim != 2 or not np.isfinite(block).all():
            raise ValueError("Capture returned invalid audio samples")
        if not len(block):
            return
        quiet = dbfs(block) < self.threshold
        if quiet:
            self._remember(block)
            if self.active:
                remaining = self.limit - self.pending_frames
                self.pending.append(block[:remaining].copy())
                self.pending_frames += min(len(block), remaining)
                if self.pending_frames >= self.limit:
                    self.pending.clear()
                    self.pending_frames = 0
                    self.active = False
                    self.end()
            return
        if not self.active:
            self.start()
            self.active = True
            if self.pre is not None:
                self.data(self.pre)
            self.pre = None
        for buffered in self.pending:
            self.data(buffered)
        self.pending.clear()
        self.pending_frames = 0
        self.data(block)

    def finish(self):
        if self.active:
            for buffered in self.pending:
                self.data(buffered)
            self.active = False
            self.end()
        self.pending.clear()
        self.pending_frames = 0
        self.pre = None


def prepare_output(directory: Path) -> Path:
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(directory).free < 128 * 1024 * 1024:
        raise OSError("Output disk has less than 128 MiB free")
    with tempfile.TemporaryFile(dir=directory) as probe:
        probe.write(b"write-check")
    return directory


def embed_tags(path: Path, metadata: TrackMetadata, artwork: bytes | None = None):
    flac = FLAC(path)
    flac.clear()
    for key, value in {
        "title": metadata.title,
        "artist": metadata.artist,
        "album": metadata.album,
        "date": metadata.year,
        "tracknumber": metadata.track_number,
        "source": metadata.source_url,
        "sourceid": metadata.source_id,
        "comment": metadata.comment,
    }.items():
        if value:
            flac[key] = str(value)
    if artwork:
        if len(artwork) > 20 * 1024 * 1024:
            raise ValueError("Artwork exceeds 20 MiB")
        with Image.open(BytesIO(artwork)) as image:
            if image.width * image.height > 25_000_000:
                raise ValueError("Artwork exceeds 25 megapixels")
            image.load()
            image = image.convert("RGB")
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=95)
            picture = Picture()
            picture.type, picture.mime, picture.desc = 3, "image/jpeg", "Front cover"
            picture.width, picture.height, picture.depth = image.width, image.height, 24
            picture.data = buffer.getvalue()
        flac.clear_pictures()
        flac.add_picture(picture)
    flac.save()


def verify_flac(path: Path, cancellation_check: Callable | None = None):
    with sf.SoundFile(path) as stream:
        if stream.format != "FLAC" or stream.frames <= 0:
            raise ValueError("Encoder produced an empty or invalid FLAC")
        count = 0
        while True:
            if cancellation_check is not None:
                cancellation_check()
            block = stream.read(65536, dtype="float32", always_2d=True)
            if not len(block):
                break
            count += len(block)
        if count != stream.frames:
            raise ValueError("FLAC verification found a truncated stream")


def publish_flac(source: Path, output: Path, metadata: TrackMetadata) -> Path:
    """Atomic no-clobber hard-link publication on the same filesystem.

    Failure (including unsupported filesystems) leaves the verified source intact.
    """
    target = output / filename(metadata)
    counter = 2
    while True:
        try:
            os.link(source, target)
            break
        except FileExistsError:
            directory = output / f"Duplicate {counter}"
            counter += 1
            directory.mkdir(exist_ok=True)
            target = directory / filename(metadata)
    source.unlink()
    return target


class FlacSink:
    def __init__(self, output: Path, rate: int, channels: int, metadata: TrackMetadata):
        self.output, self.metadata = output, metadata
        self.temp_dir = Path(tempfile.mkdtemp(prefix=".velvetstream-", dir=output))
        self.path = self.temp_dir / "audio.flac"
        self.stream = sf.SoundFile(
            self.path, mode="w", samplerate=rate, channels=channels, format="FLAC", subtype="PCM_24"
        )
        self.frames = 0

    def write(self, block: np.ndarray):
        self.stream.write(block)
        self.frames += len(block)

    def close(self):
        if not self.stream.closed:
            self.stream.flush()
            self.stream.close()

    def finish(self, artwork: bytes | None = None) -> Path:
        self.close()
        embed_tags(self.path, self.metadata, artwork)
        verify_flac(self.path)
        with self.path.open("r+b") as handle:
            os.fsync(handle.fileno())
        target = publish_flac(self.path, self.output, self.metadata)
        self.temp_dir.rmdir()
        return target
