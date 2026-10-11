import numpy as np
from mutagen.flac import FLAC
from PySide6.QtCore import QSettings, Qt

from velvetstream.core import FlacSink, TrackMetadata
from velvetstream.ui import MainWindow


def test_manual_editor_remains_available_during_capture(qtbot, tmp_path):
    w = MainWindow(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat), discover=False
    )
    qtbot.addWidget(w)
    w.show()
    sink = FlacSink(tmp_path, 48000, 2, TrackMetadata.recording())
    sink.write(np.full((100, 2), 0.25))
    path = sink.finish()
    w.on_manual_required(path)
    w.set_busy(True)
    assert w.edit_tags.isEnabled() and not w.tabs.isEnabled()
    w.open_tag_editor()
    dialog = w.tag_dialog
    assert dialog.isVisible() and dialog.path == path
    dialog.title.setText("Manual Song")
    dialog.artist.setText("Manual Artist")
    dialog.album.setText("Manual Album")
    dialog.year.setText("2025")
    dialog.save.click()
    qtbot.waitUntil(lambda: not w.tag_jobs.busy, timeout=4000)
    target = tmp_path / "Manual Artist - Manual Song.flac"
    assert target.is_file() and FLAC(target)["album"] == ["Manual Album"]
    assert w.pending_tags.count() == 0 and not path.exists()
    w.set_busy(False)
    w.close()


def test_console_click_on_fallback_opens_editor(qtbot, tmp_path):
    w = MainWindow(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat), discover=False
    )
    qtbot.addWidget(w)
    w.show()
    path = tmp_path / "Recording_20261009_120000.flac"
    w.on_manual_required(path)
    w.log_toggle.setChecked(True)
    cursor = w.console.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    location = w.console.cursorRect(cursor).center()
    qtbot.mouseClick(w.console.viewport(), Qt.MouseButton.LeftButton, pos=location)
    assert w.tag_dialog is not None and w.tag_dialog.path == path
    w.tag_dialog.reject()
    w.close()


def test_approved_logo_is_present(qtbot, tmp_path):
    w = MainWindow(
        QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat), discover=False
    )
    qtbot.addWidget(w)
    assert not w.windowIcon().isNull() and not w.logo.pixmap().isNull()
