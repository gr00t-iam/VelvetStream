"""Interruptible yt-dlp tasks. The owning QThread supervises real child processes."""

from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import re
import signal
import threading
import time
from pathlib import Path

from .core import youtube_target


class Cancelled(Exception):
    """A user-requested cancellation, distinct from a failure."""


class SkipTrack(Exception):
    """Skip this entry without cancelling the playlist."""


def safe_download_error(value) -> str:
    text = re.sub(r"https?://\S+", "[URL]", str(value))
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:600]


class DownloadLogger:
    def __init__(self, log):
        self.log = log

    def debug(self, message):
        pass

    def warning(self, message):
        self.log("yt-dlp: " + safe_download_error(message))

    error = warning


def youtube_options(template, stop, log, progress):
    last_update = [0.0]

    def hook(data):
        if stop.is_set():
            raise Cancelled("Download cancelled")
        now = time.monotonic()
        if now - last_update[0] < 0.1 and data.get("status") != "finished":
            return
        last_update[0] = now
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        progress(min(100, int(100 * (data.get("downloaded_bytes") or 0) / total)) if total else -1)

    return {
        "format": "bestaudio/best",
        "format_sort": ["abr", "asr"],
        "outtmpl": template,
        "noplaylist": True,
        "quiet": True,
        "logger": DownloadLogger(log),
        "progress_hooks": [hook],
        "socket_timeout": 10,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 2,
        "concurrent_fragment_downloads": 1,
        "nopart": False,
        "js_runtimes": {"deno": {}, "node": {}},
    }


class _WindowsProcessJob:
    """Kill-on-close job owns yt-dlp's JS/FFmpeg descendants as well as Python."""

    def __init__(self, process):
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Counters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "read_ops",
                    "write_ops",
                    "other_ops",
                    "read_bytes",
                    "write_bytes",
                    "other_bytes",
                )
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", Counters),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        if not self.api.AssignProcessToJobObject(self.handle, process.sentinel):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _isolated_entry(connection, gate, target, arguments):
    try:
        if os.name != "nt":
            os.setsid()
        # No network or grandchildren until the parent has attached its OS job.
        gate.wait()
        connection.send(("ready", None))
        result = target(connection, *arguments)
        connection.send(("result", result))
    except BaseException as exc:
        try:
            connection.send(("error", f"{type(exc).__name__}: {safe_download_error(exc)}"))
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


def run_isolated(
    target,
    arguments,
    stop: threading.Event,
    *,
    skip=None,
    log=lambda value: None,
    progress=lambda value: None,
    inactivity=90.0,
    hard_timeout=14400.0,
):
    """Never block the GUI or rely on third-party cooperative cancellation.

    Child startup has its own 30-second bound. Only genuine transfer activity
    extends the idle deadline, so repeated warnings cannot mask a stalled task.
    """
    if stop.is_set():
        raise Cancelled("Download cancelled")
    context = multiprocessing.get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    gate = context.Event()
    process = context.Process(target=_isolated_entry, args=(sending, gate, target, arguments))
    job = None
    start = last_activity = time.monotonic()
    ready = False
    try:
        process.start()
        sending.close()
        if os.name == "nt":
            job = _WindowsProcessJob(process)
        gate.set()
        while True:
            if stop.is_set():
                raise Cancelled("Download cancelled")
            if skip is not None and skip.is_set():
                raise SkipTrack("Entry skipped by user")
            now = time.monotonic()
            if now - start > hard_timeout:
                raise TimeoutError("Extraction exceeded its maximum runtime")
            if (ready and now - last_activity > inactivity) or (not ready and now - start > 30):
                raise TimeoutError("No download activity; stalled entry released")
            if receiving.poll(0.05):
                try:
                    kind, value = receiving.recv()
                except EOFError:
                    raise RuntimeError("Extraction process exited without a result") from None
                if kind in {"ready", "activity"}:
                    ready = True
                    last_activity = now
                elif kind == "log":
                    log(value)
                elif kind == "progress":
                    progress(value)
                elif kind == "error":
                    raise RuntimeError(value)
                elif kind == "result":
                    return value
            elif not process.is_alive():
                raise RuntimeError(f"Extraction process exited (code {process.exitcode})")
    finally:
        if job is not None:
            job.close()  # Closes all descendants, even if the direct child has exited.
        elif process.pid and os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.pid:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            if not process.is_alive():
                process.close()
        receiving.close()
        sending.close()


def _youtube_task(connection, url, template, manifest, playlist):
    import yt_dlp

    url, explicit_playlist = youtube_target(url)
    allow_playlist = playlist and explicit_playlist

    class PipeLogger:
        def debug(self, message):
            pass

        def warning(self, message):
            connection.send(("log", "yt-dlp: " + safe_download_error(message)))

        error = warning

    last_time, last_bytes = 0.0, -1

    def hook(data):
        nonlocal last_time, last_bytes
        now = time.monotonic()
        amount = data.get("downloaded_bytes") or 0
        if now - last_time < 0.1 and data.get("status") != "finished":
            return
        last_time = now
        if amount > last_bytes or data.get("status") == "finished":
            connection.send(("activity", None))
            last_bytes = amount
        total = data.get("total_bytes") or data.get("total_bytes_estimate")
        connection.send(("progress", min(100, int(100 * amount / total)) if total else -1))

    options = youtube_options(template, threading.Event(), lambda value: None, lambda value: None)
    options.update(
        noplaylist=not allow_playlist,
        logger=PipeLogger(),
        progress_hooks=[hook],
        match_filter=lambda info, *, incomplete=False: (
            "Live broadcasts are not supported; use Live Capture" if info.get("is_live") else None
        ),
    )
    if allow_playlist:
        options.update(extract_flat="in_playlist", lazy_playlist=False, playlistend=200)
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=not playlist)
        if not info:
            raise RuntimeError("YouTube returned no extractable media")
        if playlist and info.get("entries") is not None:
            # Never leave a lazy network iterator in the supervising thread.
            info["entries"] = [
                {key: entry.get(key) for key in ("id", "url", "webpage_url", "title")}
                if entry
                else None
                for entry in info["entries"]
            ][:200]
        source = "" if playlist else downloader.prepare_filename(info)
        if not playlist and not Path(source).is_file():
            downloads = info.get("requested_downloads") or []
            if downloads:
                source = downloads[0].get("filepath", source)
        payload = {"info": downloader.sanitize_info(info), "filename": source}
        Path(manifest).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(manifest)


def extract_youtube(url, template, directory, stop, log, progress, *, playlist=False, skip=None):
    manifest = Path(directory) / "extraction.json"
    run_isolated(
        _youtube_task,
        (url, str(template), str(manifest), playlist),
        stop,
        skip=skip,
        log=log,
        progress=progress,
        hard_timeout=180 if playlist else 14400,
    )
    if manifest.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("YouTube metadata exceeds the safety limit")
    return json.loads(manifest.read_text(encoding="utf-8"))
