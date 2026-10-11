from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QSettings
from PySide6.QtMultimedia import QMediaPlayer

from velvetstream.ui import MainWindow


def test_player_opens_flac_seeks_and_stops(qtbot, tmp_path):
    path = tmp_path / "Artist - Song.flac"
    sf.write(path, np.zeros((48000 * 3, 2)), 48000, subtype="PCM_24")
    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat), False)
    qtbot.addWidget(w)
    w.show()
    assert hasattr(w, "player_panel")
    panel = w.player_panel
    panel.open_path(path)
    qtbot.waitUntil(lambda: panel.player.duration() >= 2900, timeout=5000)
    assert Path(panel.player.source().toLocalFile()) == path
    panel.seek.setValue(1000)
    panel.seek.sliderReleased.emit()
    qtbot.waitUntil(lambda: panel.player.position() >= 950, timeout=2000)
    panel.play.click()
    qtbot.waitUntil(lambda: panel.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
    panel.play.click()
    qtbot.waitUntil(lambda: panel.player.playbackState() == QMediaPlayer.PlaybackState.PausedState)
    panel.stop.click()
    assert panel.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState
    w.close()


def test_live_capture_stops_player_to_prevent_loopback_bleed(qtbot, tmp_path):
    path = tmp_path / "Song.flac"
    sf.write(path, np.zeros((48000 * 3, 2)), 48000)
    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat), False)
    qtbot.addWidget(w)
    w.player_panel.open_path(path)
    w.player_panel.player.play()
    w.operation = "capture"
    w.set_busy(True)
    assert not w.player_panel.play.isEnabled()
    assert w.player_panel.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
    w.set_busy(False)
    assert w.player_panel.play.isEnabled()
    w.close()


def test_completed_recording_is_available_without_autoplay(qtbot, tmp_path):
    path = tmp_path / "Finished.flac"
    sf.write(path, np.zeros((1000, 2)), 44100)
    w = MainWindow(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat), False)
    qtbot.addWidget(w)
    assert hasattr(w, "on_recording_saved")
    w.on_recording_saved(path)
    assert w.player_panel.tracks.currentData() == str(path)
    assert w.player_panel.player.source().isEmpty()
    assert w.player_panel.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
    w.set_busy(True)
    assert w.player_panel.open_button.isEnabled()
    w.set_busy(False)
    w.close()
