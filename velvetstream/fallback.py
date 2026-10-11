"""Chromaprint/AcoustID and MusicBrainz services; called only from Thread C."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urljoin, urlparse
from uuid import UUID

import acoustid
import aiohttp
import numpy as np
import soundfile as sf

from .core import TrackMetadata
from .runtime import application_root
from .services import check_cancel


def load_application_key() -> str:
    value = os.environ.get("VELVETSTREAM_ACOUSTID_KEY", "").strip()
    if value:
        return value
    # A local publisher/application configuration, never a GUI API field.
    try:
        lines = (application_root() / ".env").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    for line in lines:
        name, separator, value = line.partition("=")
        if separator and name.strip() == "VELVETSTREAM_ACOUSTID_KEY":
            return value.strip().strip("\"'")
    return ""


def find_fpcalc() -> str | None:
    command = os.environ.get("FPCALC", "")
    if command:
        return shutil.which(command)
    local = application_root() / "bin" / ("fpcalc.exe" if os.name == "nt" else "fpcalc")
    return str(local) if local.is_file() else shutil.which("fpcalc")


def fingerprint_window(blocks, rate: int, stop: threading.Event, command: str):
    """Encode fingerprint-only PCM to a private WAV, run fpcalc with a deadline."""
    check_cancel(stop)
    if sum(len(block) for block in blocks) != rate * 30:
        raise ValueError("AcoustID requires this recording's full 30-second window")
    with tempfile.TemporaryDirectory(prefix="velvetstream-fingerprint-") as directory:
        path = Path(directory) / "sample.wav"
        with sf.SoundFile(
            path, "w", samplerate=rate, channels=2, format="WAV", subtype="PCM_16"
        ) as out:
            for block in blocks:
                check_cancel(stop)
                if block.ndim != 2 or block.shape[1] != 2 or not np.isfinite(block).all():
                    raise ValueError("Invalid fingerprint PCM")
                out.write(block)
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(
                [command, "-json", "-length", "30", str(path)],
                stdout=stdout,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + 10
            try:
                while process.poll() is None:
                    check_cancel(stop)
                    if time.monotonic() > deadline:
                        raise TimeoutError("Chromaprint exceeded its deadline")
                    stop.wait(0.02)
                check_cancel(stop)
                stdout.seek(0)
                raw = stdout.read(128 * 1024)
                # fpcalc can return EOF code 3 while still producing valid output.
                answer = json.loads(raw)
                fingerprint = answer.get("fingerprint")
                duration = float(answer.get("duration", 0))
                if process.returncode not in (0, 3) or not fingerprint or duration < 29.5:
                    raise ValueError("Chromaprint produced no usable fingerprint")
                return duration, fingerprint
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def recording_id_from_acoustid(answer: dict, minimum_score=0.8) -> str | None:
    if answer.get("status") != "ok":
        raise ValueError("AcoustID rejected the lookup")
    candidates = {}
    for result in answer.get("results") or []:
        score = float(result.get("score") or 0)
        for recording in result.get("recordings") or []:
            try:
                identifier = str(UUID(recording.get("id", "")))
            except (ValueError, TypeError, AttributeError):
                continue
            candidates[identifier] = max(score, candidates.get(identifier, 0))
    ranked = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
    if not ranked or ranked[0][1] < minimum_score:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.02:
        return None  # Ambiguous identities are safer to tag manually.
    return ranked[0][0]


def metadata_from_musicbrainz(answer: dict, recording_id: str) -> tuple[TrackMetadata | None, str]:
    title = str(answer.get("title") or "").strip()
    artist = "".join(
        str(credit.get("name") or (credit.get("artist") or {}).get("name") or "")
        + str(credit.get("joinphrase") or "")
        for credit in answer.get("artist-credit") or []
        if isinstance(credit, dict)
    ).strip()
    if not title or not artist:
        return None, ""
    releases = [r for r in answer.get("releases") or [] if r.get("status") == "Official"]
    # A recording can occur on many editions. Prefer a dated official album,
    # then earliest dated official release; do not invent an album when absent.
    releases.sort(
        key=lambda r: (
            (r.get("release-group") or {}).get("primary-type") != "Album",
            not bool(r.get("date")),
            r.get("date") or "9999",
            r.get("id") or "",
        )
    )
    release = releases[0] if releases else {}
    date = str(release.get("date") or answer.get("first-release-date") or "")
    metadata = TrackMetadata(
        title,
        artist,
        str(release.get("title") or ""),
        date[:4] if re.match(r"^\d{4}", date) else "",
        source_url=f"https://musicbrainz.org/recording/{recording_id}",
        source_id=recording_id,
        comment="AcoustID/Chromaprint match; MusicBrainz official release selection may differ from playback edition",
    )
    try:
        release_id = str(UUID(release.get("id", "")))
    except (ValueError, TypeError, AttributeError):
        release_id = ""
    return metadata, release_id


def cover_url_from_archive(answer: dict) -> str:
    for image in answer.get("images") or []:
        if image.get("front"):
            url = str(image.get("image") or "")
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if parsed.scheme in {"http", "https"} and (
                host == "archive.org"
                or host.endswith(".archive.org")
                or host == "coverartarchive.org"
            ):
                return parsed._replace(scheme="https").geturl()
    return ""


class AcoustIDRecognizer:
    needs_track_duration = True

    def __init__(self, key: str | None = None, command: str | None = None):
        self.key = load_application_key() if key is None else key
        self.command = command or find_fpcalc()
        self.available = bool(self.key and self.command)
        self.unavailable_reason = (
            (
                "application key not configured"
                if not self.key
                else "Chromaprint fpcalc not installed"
            )
            if not self.available
            else ""
        )
        self._gate = None
        self._last_musicbrainz = 0.0

    async def _json(self, url: str, params=None):
        timeout = aiohttp.ClientTimeout(total=8, connect=3)
        headers = {
            "User-Agent": "VelvetStream/1.0 (desktop audio tagging)",
            "Accept": "application/json",
        }
        is_cover = urlparse(url).hostname == "coverartarchive.org"
        is_musicbrainz = urlparse(url).hostname == "musicbrainz.org"
        deadline = time.monotonic() + 8
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            for _ in range(5):
                if time.monotonic() > deadline:
                    raise TimeoutError("Metadata deadline exceeded")
                if is_musicbrainz:
                    delay = max(0, self._last_musicbrainz + 1.1 - time.monotonic())
                    await asyncio.sleep(delay)
                    self._last_musicbrainz = time.monotonic()
                async with session.get(url, params=params, allow_redirects=False) as response:
                    if response.status == 404:
                        return {}
                    if response.status in {301, 302, 303, 307, 308} and (
                        is_cover or is_musicbrainz
                    ):
                        target = urljoin(url, response.headers.get("Location", ""))
                        parsed = urlparse(target)
                        host = parsed.hostname or ""
                        allowed_host = (is_musicbrainz and host == "musicbrainz.org") or (
                            is_cover
                            and (
                                host == "coverartarchive.org"
                                or host == "archive.org"
                                or host.endswith(".archive.org")
                            )
                        )
                        if (
                            parsed.scheme != "https"
                            or not allowed_host
                            or parsed.username
                            or parsed.password
                        ):
                            raise ValueError("Unsupported metadata redirect")
                        url, params = target, None
                        continue
                    if response.status != 200:
                        response.raise_for_status()
                        raise ValueError("Unexpected metadata response")
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            raise ValueError("Metadata response too large")
                        if time.monotonic() > deadline:
                            raise TimeoutError("Metadata deadline exceeded")
                        chunks.append(chunk)
                    value = json.loads(b"".join(chunks))
                    if not isinstance(value, dict):
                        raise ValueError("Unexpected metadata format")
                    return value
        raise ValueError("Too many metadata redirects")

    async def recognize(
        self, blocks, rate: int, stop: threading.Event, ticket=None
    ) -> TrackMetadata | None:
        if not self.available:
            return None
        if self._gate is None:
            self._gate = asyncio.Semaphore(1)
        _, fingerprint = await asyncio.to_thread(
            fingerprint_window, blocks, rate, stop, self.command
        )
        # AcoustID's public service expects full recording duration and rejects
        # durations more than seven seconds from a candidate by default. Retain
        # only the tiny fingerprint while acquisition continues; no guessed length.
        del blocks
        if ticket is None:
            raise ValueError("Full recording duration is required for AcoustID lookup")
        while not ticket.audio_ended.is_set():
            check_cancel(stop)
            if ticket.done.is_set():
                return None
            await asyncio.sleep(0.05)
        duration = ticket.duration
        if duration < 30:
            return None
        # Serial Tier 2 requests obey provider rate limits. Finalization seals
        # unanswered tickets after 50 seconds from track completion.
        async with self._gate:
            check_cancel(stop)
            if not self.available or ticket.done.is_set():
                return None
            async with asyncio.timeout(35):
                check_cancel(stop)
                acoustid.set_base_url("https://api.acoustid.org/v2/")
                answer = await asyncio.to_thread(
                    acoustid.lookup,
                    self.key,
                    fingerprint,
                    duration,
                    meta=["recordings"],
                    timeout=(3.05, 8),
                )
                check_cancel(stop)
                if (answer.get("error") or {}).get("code") == 4:
                    self.available = False
                    self.unavailable_reason = "invalid application key; update local configuration"
                recording_id = recording_id_from_acoustid(answer)
                if not recording_id:
                    return None
                check_cancel(stop)
                official = await self._json(
                    f"https://musicbrainz.org/ws/2/recording/{recording_id}",
                    {"fmt": "json", "inc": "artist-credits+releases+release-groups"},
                )
                metadata, release_id = metadata_from_musicbrainz(official, recording_id)
                if metadata and release_id:
                    try:
                        cover = await self._json(
                            f"https://coverartarchive.org/release/{release_id}"
                        )
                        metadata = replace(metadata, art_url=cover_url_from_archive(cover))
                    except (aiohttp.ClientError, TimeoutError, ValueError):
                        pass  # Official text tags remain useful without artwork.
                return metadata
