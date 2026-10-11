"""Render the real Qt widgets offscreen without discovery/authentication."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from velvetstream.ui import MainWindow, configure_fonts


def main():
    output = Path(__file__).resolve().parents[1] / "docs" / "previews"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    configure_fonts(app)
    settings = QSettings(str(output / "preview-settings.ini"), QSettings.Format.IniFormat)
    settings.clear()
    window = MainWindow(settings=settings, discover=False)
    window.devices.clear()
    window.devices.addItem("Speakers · WASAPI loopback", "preview")
    window.output.setText(str(Path.home() / "Music" / "VelvetStream"))
    window.resize(980, 1100)
    window.show()
    for dark, mode, name in [
        (True, 0, "dark-live"),
        (True, 1, "dark-youtube"),
        (False, 1, "light-youtube"),
    ]:
        window.dark = dark
        window.apply_theme()
        window.tabs.setCurrentIndex(mode)
        window.fade.stop()
        effect = window.stack.currentWidget().graphicsEffect()
        if effect:
            effect.setOpacity(1.0)
        app.processEvents()
        window.grab().save(str(output / (name + ".png")))
    window.resize(820, 700)
    for dark, mode, name in [
        (True, 0, "dark-live-compact"),
        (True, 1, "dark-youtube-compact"),
        (False, 1, "light-youtube-compact"),
    ]:
        window.dark = dark
        window.apply_theme()
        window.tabs.setCurrentIndex(mode)
        window.fade.stop()
        effect = window.stack.currentWidget().graphicsEffect()
        if effect:
            effect.setOpacity(1.0)
        app.processEvents()
        window.grab().save(str(output / (name + ".png")))
    window.resize(980, 1100)
    window.dark = True
    window.apply_theme()
    window.tabs.setCurrentIndex(0)
    window.fade.stop()
    effect = window.stack.currentWidget().graphicsEffect()
    if effect:
        effect.setOpacity(1.0)
    window.on_manual_required(Path("Recording_20261009_120000.flac"))
    window.log_toggle.setChecked(True)
    app.processEvents()
    window.grab().save(str(output / "dark-manual.png"))
    window.open_tag_editor()
    window.tag_dialog.title.setText("Track title")
    window.tag_dialog.artist.setText("Artist name")
    window.tag_dialog.album.setText("Album name")
    window.tag_dialog.year.setText("2026")
    app.processEvents()
    window.tag_dialog.grab().save(str(output / "tag-editor.png"))
    window.tag_dialog.reject()
    window.close()
    settings.clear()
    settings.sync()
    (output / "preview-settings.ini").unlink(missing_ok=True)
    print(output)


if __name__ == "__main__":
    main()
