"""Opt-in installed-app diagnostic command; writes a report without changing settings."""

import json
import multiprocessing
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path


def wait_for_cancel(connection, started):
    started.set()
    while True:
        time.sleep(0.05)


def run_diagnostics(report_path, youtube_url=None):
    import numpy as np
    import soundfile as sf
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtWidgets import QApplication, QScrollArea

    from velvetstream.core import TrackMetadata, embed_tags
    from velvetstream.download_tasks import Cancelled, run_isolated
    from velvetstream.fallback import load_application_key
    from velvetstream.runtime import application_root
    from velvetstream.services import convert_to_flac, loopback_devices
    from velvetstream.ui import MainWindow

    report = {"frozen": bool(getattr(__import__("sys"), "frozen", False)), "checks": {}}
    checks = report["checks"]
    try:
        checks["config_present"] = bool(load_application_key())
        checks["app_root"] = str(application_root())
        for name in ("ffmpeg", "ffprobe", "node", "fpcalc"):
            path = shutil.which(name)
            checks[name + "_path"] = path
            if not path:
                raise RuntimeError("Missing " + name)
            flag = "-version" if name in ("ffmpeg", "ffprobe", "fpcalc") else "--version"
            result = subprocess.run(
                [path, flag],
                capture_output=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            checks[name + "_runs"] = result.returncode == 0
        checks["loopback_count"] = len(loopback_devices())
        started = multiprocessing.get_context("spawn").Event()
        stop = threading.Event()
        timer = threading.Thread(target=lambda: (started.wait(20), stop.set()), daemon=True)
        timer.start()
        before = time.monotonic()
        try:
            run_isolated(wait_for_cancel, (started,), stop)
        except Cancelled:
            checks["no_hook_cancellation"] = True
            checks["cancel_total_seconds"] = round(time.monotonic() - before, 3)
        timer.join(2)
        checks["child_processes_remaining"] = len(multiprocessing.active_children())
        app = QApplication.instance() or QApplication([])
        app.setStyle("Fusion")
        with tempfile.TemporaryDirectory(prefix="velvetstream-diagnostics-") as directory:
            directory = Path(directory)
            wave = directory / "test.wav"
            flac = directory / "Diagnostic - Silence.flac"
            sf.write(wave, np.zeros((48000 * 3, 2)), 48000, subtype="PCM_24")
            convert_to_flac(wave, flac, threading.Event())
            embed_tags(flac, TrackMetadata("Silence", "Diagnostic"))
            checks["flac_subtype"] = sf.info(flac).subtype
            if youtube_url:
                from mutagen.flac import FLAC

                from velvetstream.services import YouTubeDownloader

                logs = []
                result = YouTubeDownloader(
                    threading.Event(), logs.append, lambda v: None, lambda v: None
                ).run(youtube_url, directory / "youtube")
                checks["youtube_saved"], checks["youtube_failed"] = result
                checks["youtube_log"] = logs[-20:]
                files = list((directory / "youtube").glob("*.flac"))
                checks["youtube_flac"] = bool(files)
                if files:
                    media = sf.info(files[0])
                    checks["youtube_duration"] = round(media.duration, 2)
                    checks["youtube_rate"] = media.samplerate
                    checks["youtube_tags"] = sorted(FLAC(files[0]).keys())
                    checks["youtube_covers"] = len(FLAC(files[0]).pictures)
            window = MainWindow(QSettings(str(directory / "test.ini"), QSettings.Format.IniFormat))
            window.resize(820, 700)
            window.tabs.setCurrentIndex(1)
            window.player_panel.audio.setMuted(True)
            window.player_panel.open_path(flac)
            window.show()
            deadline = time.monotonic() + 15
            phase = [0]

            def inspect():
                panel = window.player_panel
                if time.monotonic() > deadline:
                    checks["player_error"] = (
                        panel.player.errorString() or "Media diagnostic timed out"
                    )
                    window.close()
                    return
                if phase[0] == 0 and panel.player.duration() > 2900:
                    checks["player_duration_ms"] = panel.player.duration()
                    panel.player.setPosition(1000)
                    panel.play.click()
                    phase[0] = 1
                elif (
                    phase[0] == 1
                    and panel.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                ):
                    checks["player_play"] = True
                    checks["player_seek_ms"] = panel.player.position()
                    panel.play.click()
                    phase[0] = 2
                elif (
                    phase[0] == 2
                    and panel.player.playbackState() == QMediaPlayer.PlaybackState.PausedState
                ):
                    checks["player_pause"] = True
                    panel.stop.click()
                    checks["player_stop"] = (
                        panel.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
                    )
                    if window.devices_job.busy:
                        return
                    screenshot = Path(report_path).with_suffix(".png")
                    window.grab().save(str(screenshot))
                    checks["screenshot"] = str(screenshot)
                    checks["window_size"] = [window.width(), window.height()]
                    checks["page_scroll_area"] = bool(window.findChildren(QScrollArea))
                    window.close()
                QTimer.singleShot(50, inspect)

            QTimer.singleShot(100, inspect)
            app.exec()
        required = [
            "ffmpeg_runs",
            "ffprobe_runs",
            "node_runs",
            "fpcalc_runs",
            "no_hook_cancellation",
            "player_play",
            "player_pause",
            "player_stop",
        ]
        report["passed"] = (
            all(checks.get(name) is True for name in required)
            and checks["child_processes_remaining"] == 0
        )
        if youtube_url:
            report["passed"] = (
                report["passed"]
                and checks.get("youtube_saved") == 1
                and checks.get("youtube_failed") == 0
            )
    except Exception as exc:
        report["passed"] = False
        report["error"] = type(exc).__name__
    Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report.get("passed") else 1
