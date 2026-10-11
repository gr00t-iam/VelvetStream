"""Offline manual tags: copy, verify and publish without modifying original audio."""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path

from .core import TrackMetadata, embed_tags, publish_flac, verify_flac
from .services import check_cancel


def retag_recording(path: Path, metadata: TrackMetadata, stop: threading.Event) -> Path:
    if not metadata.title.strip() or not metadata.artist.strip():
        raise ValueError("Enter a title and artist")
    if metadata.year and not re.fullmatch(r"\d{4}", metadata.year):
        raise ValueError("Year must be four digits or empty")
    path = Path(path)
    if path.suffix.casefold() != ".flac":
        raise ValueError("Manual tagging requires a saved FLAC")
    original = path.stat()
    identity = (original.st_ino, original.st_size, original.st_mtime_ns)
    with tempfile.TemporaryDirectory(prefix=".velvetstream-retag-", dir=path.parent) as directory:
        copy = Path(directory) / "audio.flac"
        with path.open("rb") as source, copy.open("wb") as out:
            while block := source.read(1024 * 1024):
                check_cancel(stop)
                out.write(block)
        embed_tags(copy, metadata)
        verify_flac(copy)
        check_cancel(stop)
        with copy.open("r+b") as out:
            os.fsync(out.fileno())
        current = path.stat()
        if (current.st_ino, current.st_size, current.st_mtime_ns) != identity:
            raise RuntimeError("The original recording changed during tagging; left untouched")
        target = publish_flac(copy, path.parent, metadata)
        # The new tagged file is durable before removing the timestamp original.
        try:
            path.unlink()
        except OSError:
            pass  # Keep both if an external player/permission prevents deletion.
        return target
