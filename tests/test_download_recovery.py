"""Real child processes reproduce a downloader that never calls a progress hook."""

import os
import subprocess
import sys
import threading
import time

import pytest

from velvetstream.services import Cancelled


def stalled_operation(connection, marker):
    marker.set()
    while True:
        time.sleep(0.05)


def stalled_operation_with_child(connection, marker, pid_file):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    from pathlib import Path

    Path(pid_file).write_text(str(child.pid))
    marker.set()
    while True:
        time.sleep(0.05)


@pytest.mark.skipif(os.name != "nt", reason="Windows job ownership")
def test_cancel_closes_grandchild_process_as_well_as_extractor(tmp_path):
    import ctypes
    import multiprocessing
    from ctypes import wintypes

    from velvetstream.download_tasks import run_isolated

    marker = multiprocessing.get_context("spawn").Event()
    stop = threading.Event()
    pid_file = tmp_path / "child.pid"
    timer = threading.Thread(target=lambda: (marker.wait(10), stop.set()))
    timer.start()
    try:
        with pytest.raises(Cancelled):
            run_isolated(stalled_operation_with_child, (marker, str(pid_file)), stop)
        pid = int(pid_file.read_text())
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x100000, False, pid)
        if handle:
            try:
                assert api.WaitForSingleObject(handle, 2000) == 0
            finally:
                api.CloseHandle(handle)
    finally:
        timer.join(10)


def test_stop_releases_an_extractor_that_never_reports_progress():
    import multiprocessing

    from velvetstream.download_tasks import run_isolated

    started = multiprocessing.get_context("spawn").Event()
    stop = threading.Event()
    timer = threading.Thread(target=lambda: (started.wait(10), stop.set()))
    timer.start()
    before = time.monotonic()
    try:
        with pytest.raises(Cancelled):
            run_isolated(stalled_operation, (started,), stop, inactivity=10)
        assert time.monotonic() - before < 5
        assert not multiprocessing.active_children()
    finally:
        stop.set()
        timer.join(10)


def test_stalled_extractor_times_out_and_next_operation_can_run():
    import multiprocessing

    from velvetstream.download_tasks import run_isolated

    marker = multiprocessing.get_context("spawn").Event()
    with pytest.raises(TimeoutError):
        run_isolated(stalled_operation, (marker,), threading.Event(), inactivity=0.3)
    assert not multiprocessing.active_children()


def test_skip_releases_only_the_current_track():
    import multiprocessing

    from velvetstream.download_tasks import SkipTrack, run_isolated

    started = multiprocessing.get_context("spawn").Event()
    stop, skip = threading.Event(), threading.Event()
    timer = threading.Thread(target=lambda: (started.wait(10), skip.set()))
    timer.start()
    try:
        with pytest.raises(SkipTrack):
            run_isolated(stalled_operation, (started,), stop, skip=skip)
        assert not stop.is_set()
    finally:
        timer.join(10)


def test_retry_resumes_after_failed_entry_without_duplicate_flac(tmp_path, monkeypatch):
    from pathlib import Path

    import numpy as np
    import soundfile as sf
    from mutagen.flac import FLAC

    import velvetstream.services as services

    attempts = []
    fail = [True]

    def extraction(url, template, directory, stop, log, progress, *, playlist=False, skip=None):
        if playlist:
            return {"info": {"_type": "playlist", "entries": [{"id": "first"}, {"id": "second"}]}}
        identity = url.rsplit("=", 1)[1]
        attempts.append(identity)
        if identity == "second" and fail[0]:
            raise TimeoutError("Stalled video")
        path = Path(directory) / "source.wav"
        sf.write(path, np.full((1000, 2), 0.25), 44100)
        return {
            "info": {"id": identity, "title": identity, "channel": "Channel"},
            "filename": str(path),
        }

    monkeypatch.setattr(services, "extract_youtube", extraction)
    logs, saved = [], []
    downloader = services.YouTubeDownloader(
        threading.Event(), logs.append, lambda v: None, lambda v: None, saved=saved.append
    )
    assert downloader.run("https://youtube.com/playlist?list=testing", tmp_path) == (1, 1)
    first = tmp_path / "Channel - first.flac"
    original = first.read_bytes()
    fail[0] = False
    assert downloader.run("https://youtube.com/playlist?list=testing", tmp_path) == (2, 0)
    assert attempts == ["first", "second", "second"]
    assert first.read_bytes() == original
    assert len(list(tmp_path.rglob("*.flac"))) == 2
    assert FLAC(tmp_path / "Channel - second.flac")["title"] == ["second"]
    assert not list(tmp_path.glob(".velvetstream-download-*"))


def test_resume_imports_files_saved_by_previous_app_version(tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf

    import velvetstream.services as services
    from velvetstream.core import TrackMetadata, embed_tags

    path = tmp_path / "Artist - First.flac"
    sf.write(path, np.zeros((1000, 2)), 44100)
    embed_tags(
        path,
        TrackMetadata(
            "First",
            "Artist",
            source_id="abcdefghijk",
            source_url="https://www.youtube.com/watch?v=abcdefghijk",
        ),
    )
    original = path.read_bytes()

    def extraction(url, template, directory, stop, log, progress, *, playlist=False, skip=None):
        if not playlist:
            raise AssertionError("Already completed video must not be fetched again")
        return {"info": {"_type": "playlist", "entries": [{"id": "abcdefghijk"}]}}

    monkeypatch.setattr(services, "extract_youtube", extraction)
    downloader = services.YouTubeDownloader(
        threading.Event(), lambda v: None, lambda v: None, lambda v: None
    )
    assert downloader.run("https://www.youtube.com/playlist?list=test", tmp_path) == (1, 0)
    assert path.read_bytes() == original and len(list(tmp_path.rglob("*.flac"))) == 1


def test_blocked_extractor_stop_returns_gui_to_idle_and_allows_restart(qtbot, tmp_path):
    import multiprocessing

    from PySide6.QtCore import QSettings, QTimer

    from velvetstream.download_tasks import run_isolated
    from velvetstream.ui import MainWindow
    from velvetstream.workers import Worker

    marker = multiprocessing.get_context("spawn").Event()

    class StalledWorker(Worker):
        def execute(self):
            run_isolated(stalled_operation, (marker,), self.stop)

    w = MainWindow(QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat), False)
    qtbot.addWidget(w)
    w.tabs.setCurrentIndex(1)
    w.operation = "youtube"
    w.retry.setProperty("available", True)
    ticks = []
    timer = QTimer(w)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(10)
    w.jobs.start("youtube", StalledWorker(threading.Event()))
    qtbot.waitUntil(marker.is_set, timeout=5000)
    w.action.click()
    qtbot.waitUntil(lambda: not w.jobs.busy, timeout=3000)
    assert len(ticks) > 5
    assert w.action.isEnabled() and w.tabs.isEnabled() and w.retry.isVisibleTo(w)
    marker.clear()
    w.operation = "youtube"
    w.jobs.start("youtube", StalledWorker(threading.Event()))
    qtbot.waitUntil(marker.is_set, timeout=5000)
    w.action.click()
    qtbot.waitUntil(lambda: not w.jobs.busy, timeout=3000)
    w.close()
