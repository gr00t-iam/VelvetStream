"""Device, artwork and download services, called only by workers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import ffmpeg
import requests

from .core import (
    embed_tags,
    metadata_from_youtube,
    prepare_output,
    publish_flac,
    validate_youtube_url,
    verify_flac,
    youtube_target,
)
from .download_tasks import (  # noqa: F401
    Cancelled,
    SkipTrack,
    extract_youtube,
    safe_download_error,
    youtube_options,
)


def check_cancel(stop: threading.Event):
    if stop.is_set():
        raise Cancelled("Cancelled")


@dataclass(frozen=True)
class CaptureDevice:
    id: str
    name: str


def _loopback_inputs(backend=None, platform: str | None = None) -> list:
    if backend is None:
        import soundcard as backend
    platform = platform or sys.platform
    candidates = backend.all_microphones(include_loopback=True)
    result = []
    for device in candidates:
        if platform == "darwin":
            allowed = any(
                name in device.name.lower() for name in ("blackhole", "loopback", "soundflower")
            )
        else:
            allowed = bool(getattr(device, "isloopback", False))
        if allowed:
            result.append(device)
    return result


def loopback_devices(backend=None, platform: str | None = None) -> list[CaptureDevice]:
    return [
        CaptureDevice(str(device.id), device.name) for device in _loopback_inputs(backend, platform)
    ]


def resolve_loopback(device_id: str, backend=None, platform: str | None = None):
    # CoreAudio IDs are integers; preserve the actual device object rather than
    # passing a QSettings string through SoundCard's fuzzy name matching.
    for device in _loopback_inputs(backend, platform):
        if str(device.id) == str(device_id):
            return device
    raise RuntimeError(
        "Selected loopback device is unavailable. Refresh devices or configure BlackHole."
    )


def artwork_origin_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    allowed = host == "i.scdn.co" or any(
        host == suffix or host.endswith("." + suffix)
        for suffix in (
            "ytimg.com",
            "ggpht.com",
            "mzstatic.com",
            "archive.org",
            "coverartarchive.org",
        )
    )
    return parsed.scheme == "https" and allowed and not parsed.username and not parsed.password


def download_art(url: str, stop: threading.Event) -> bytes | None:
    if not url:
        return None
    if not artwork_origin_allowed(url):
        raise ValueError("Unsupported artwork origin")
    check_cancel(stop)
    chunks, size = [], 0
    deadline = time.monotonic() + 20
    origin = urlparse(url).hostname or ""
    archive = (
        origin == "coverartarchive.org"
        or origin == "archive.org"
        or origin.endswith(".archive.org")
    )
    for _ in range(5):
        check_cancel(stop)
        if time.monotonic() > deadline:
            raise TimeoutError("Artwork download deadline exceeded")
        with requests.get(url, stream=True, timeout=(3.05, 10), allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code in {301, 302, 303, 307, 308} and archive:
                target = urljoin(url, response.headers.get("Location", ""))
                target_host = urlparse(target).hostname or ""
                if not artwork_origin_allowed(target) or not (
                    target_host == "archive.org"
                    or target_host.endswith(".archive.org")
                    or target_host == "coverartarchive.org"
                ):
                    raise ValueError("Unsupported cover archive redirect")
                url = target
                continue
            if response.status_code != 200:
                raise ValueError("Artwork server returned a redirect or empty response")
            for chunk in response.iter_content(65536):
                check_cancel(stop)
                if time.monotonic() > deadline:
                    raise TimeoutError("Artwork download deadline exceeded")
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise ValueError("Artwork exceeds 20 MiB")
                chunks.append(chunk)
            return b"".join(chunks)
    raise ValueError("Too many artwork redirects")


def convert_to_flac(source: Path, destination: Path, stop: threading.Event):
    check_cancel(stop)
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is not installed or is not on PATH")
    graph = (
        ffmpeg.input(str(source))
        .output(
            str(destination),
            format="flac",
            acodec="flac",
            sample_fmt="s32",
            compression_level=8,
            vn=None,
            map_metadata=-1,
        )
        .global_args("-nostdin", "-hide_banner", "-loglevel", "error")
        .overwrite_output()
    )
    args = ffmpeg.compile(graph)
    # A file avoids pipe-buffer deadlocks while the cancellation loop polls.
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            while process.poll() is None:
                if stop.wait(0.05):
                    raise Cancelled("Conversion cancelled")
            check_cancel(stop)
            if process.returncode:
                errors.seek(0)
                detail = errors.read(2048).decode("utf-8", errors="replace")
                raise RuntimeError("FFmpeg conversion failed: " + detail.strip())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


class YouTubeDownloader:
    def __init__(self, stop: threading.Event, log, status, progress, *, skip=None, saved=None):
        self.stop, self.log, self.status, self.progress = stop, log, status, progress
        self.skip = skip if skip is not None else threading.Event()
        self.saved = saved or (lambda path: None)

    def read_journal(self, output):
        try:
            path = output / ".velvetstream-youtube.json"
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Journal too large")
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return self.import_existing_files(output)
        except (OSError, ValueError):
            self.log("Resume journal unavailable; existing FLAC files remain safe")
            return {}

    def import_existing_files(self, output):
        from itertools import chain, islice

        from mutagen import MutagenError
        from mutagen.flac import FLAC

        files = chain(
            output.glob("*.flac"),
            *(folder.glob("*.flac") for folder in output.glob("Duplicate *") if folder.is_dir()),
        )
        journal = {}
        for path in islice(files, 5000):
            check_cancel(self.stop)
            try:
                tags = FLAC(path)
                identity = tags.get("sourceid", [""])[0]
                source = tags.get("source", [""])[0]
                validate_youtube_url(source)
                if identity:
                    journal[str(identity)] = str(path.relative_to(output))
            except (OSError, ValueError, MutagenError):
                continue
        if journal:
            self.log(f"Resume: found {len(journal)} previously saved YouTube files")
        return journal

    def journal_path(self, output, value):
        if not isinstance(value, str):
            return None
        path = (output / value).resolve()
        return (
            path
            if path.is_relative_to(output.resolve())
            and path.suffix.lower() == ".flac"
            and path.is_file()
            else None
        )

    def write_journal(self, output, journal):
        handle, name = tempfile.mkstemp(prefix=".velvetstream-journal-", dir=output)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(journal, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, output / ".velvetstream-youtube.json")
        finally:
            Path(name).unlink(missing_ok=True)

    def run(self, url: str, output: Path) -> tuple[int, int]:
        url, explicit_playlist = youtube_target(url)
        output = prepare_output(output)
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("Install system FFmpeg and ffprobe and add them to PATH")
        self.status("Downloading")
        self.log("Reading YouTube URL / playlist…")
        with tempfile.TemporaryDirectory(prefix=".velvetstream-list-", dir=output) as listing:
            info = extract_youtube(
                url,
                str(Path(listing) / "unused.%(ext)s"),
                listing,
                self.stop,
                self.log,
                self.progress,
                playlist=True,
                skip=self.skip,
            )["info"]
        check_cancel(self.stop)
        if not info:
            raise RuntimeError("YouTube returned no extractable media")
        if not explicit_playlist and (
            info.get("entries") is not None or info.get("_type") in ("playlist", "multi_video")
        ):
            raise RuntimeError("Expected one video; refusing an unexpected playlist response")
        if not explicit_playlist:
            expected_id = urlparse(url).query.removeprefix("v=")
            if str(info.get("id", "")) != expected_id:
                raise RuntimeError("YouTube returned a different video; extraction stopped")
            self.log("Single video selected; attached playlist / radio context is ignored")
        entries = (
            info.get("entries") if info.get("_type") in ("playlist", "multi_video") else [info]
        )
        if info.get("entries") is not None:
            self.log(
                "Playlist / radio mix: finite batch of up to 200 entries; retry skips completed files"
            )
        journal = self.read_journal(output)
        successes = failures = 0
        for index, entry in enumerate(entries or [], 1):
            check_cancel(self.stop)
            self.skip.clear()
            if not entry:
                failures += 1
                self.log(f"Entry {index}: unavailable; skipped")
                continue
            media_url = entry.get("webpage_url") or entry.get("url")
            if not explicit_playlist:
                media_url = url
            if not media_url or not media_url.startswith("https://"):
                media_url = "https://www.youtube.com/watch?v=" + str(entry.get("id", ""))
            identity = str(entry.get("id") or media_url)
            existing = self.journal_path(output, journal.get(identity))
            if existing:
                successes += 1
                self.log(f"Entry {index}: already saved; skipped ({existing.name})")
                self.saved(existing)
                continue
            directory = Path(tempfile.mkdtemp(prefix=".velvetstream-download-", dir=output))
            preserve = False
            entry_stop = EntryCancellation(self.stop, self.skip)
            try:
                validate_youtube_url(media_url)
                self.status("Downloading")
                self.progress(0)
                self.log(f"Entry {index}: downloading best available audio")
                extracted = extract_youtube(
                    media_url,
                    str(directory / "source.%(ext)s"),
                    directory,
                    self.stop,
                    self.log,
                    self.progress,
                    skip=self.skip,
                )
                item, source = extracted["info"], Path(extracted["filename"])
                if str(item.get("id", "")) != str(entry.get("id", "")):
                    raise RuntimeError("Downloaded video differs from the selected entry")
                check_cancel(self.stop)
                if not source.is_file() or source.parent.resolve() != directory.resolve():
                    raise RuntimeError("Downloaded audio file could not be located")
                self.log(
                    f"Source: {item.get('acodec', 'unknown codec')}, {item.get('abr') or '?'} kb/s"
                )
                item["playlist_index"] = index if info.get("entries") is not None else None
                metadata = metadata_from_youtube(item)
                self.status("Converting")
                self.progress(-1)
                target = directory / "audio.flac"
                convert_to_flac(source, target, entry_stop)
                # A complete encode is recoverable if tagging or publication fails.
                preserve = True
                art = None
                try:
                    art = download_art(metadata.art_url, entry_stop)
                except Cancelled:
                    raise
                except Exception:
                    self.log("Cover art unavailable; preserving audio and text tags")
                try:
                    embed_tags(target, metadata, art)
                except (ValueError, OSError):
                    if not art:
                        raise
                    self.log("Cover art rejected; saving text tags")
                    embed_tags(target, metadata)
                verify_flac(target, lambda: check_cancel(entry_stop))
                check_cancel(entry_stop)
                with target.open("r+b") as handle:
                    os.fsync(handle.fileno())
                published = publish_flac(target, output, metadata)
                preserve = False
                successes += 1
                self.log(f"Saved: {published}")
                self.saved(published)
                journal[identity] = str(published.relative_to(output))
                try:
                    self.write_journal(output, journal)
                except OSError:
                    self.log("Saved audio is safe, but the resume journal could not be updated")
            except SkipTrack:
                failures += 1
                self.log(f"Entry {index}: skipped by user; continuing playlist")
            except Cancelled:
                if self.skip.is_set() and not self.stop.is_set():
                    failures += 1
                    self.log(f"Entry {index}: skipped by user; continuing playlist")
                    continue
                self.log(f"Cancelled after {successes} saved, {failures} failed")
                raise
            except Exception as exc:
                if self.stop.is_set():
                    raise Cancelled("Download cancelled") from exc
                failures += 1
                self.log(f"Entry {index} failed: {safe_download_error(exc)}; continuing playlist")
            finally:
                if preserve:
                    self.log(f"Recoverable files retained: {directory}")
                else:
                    shutil.rmtree(directory, ignore_errors=True)
        self.progress(100)
        self.log(f"Finished: {successes} saved, {failures} failed")
        return successes, failures


class EntryCancellation:
    """Cancellation view shared by conversion, verification and cover downloads."""

    def __init__(self, stop, skip):
        self.stop, self.skip = stop, skip

    def is_set(self):
        return self.stop.is_set() or self.skip.is_set()

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while not self.is_set() and time.monotonic() < deadline:
            self.stop.wait(min(0.01, max(0, deadline - time.monotonic())))
        return self.is_set()
