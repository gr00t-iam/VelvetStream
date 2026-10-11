"""Brief native GUI smoke test; enumerates devices, does not capture audio."""

from pathlib import Path

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from velvetstream.ui import MainWindow, configure_fonts


def main():
    directory = Path(__file__).resolve().parents[1] / "docs" / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyle("Fusion")
    configure_fonts(app)
    settings = QSettings(str(directory / "native-settings.ini"), QSettings.Format.IniFormat)
    settings.clear()
    window = MainWindow(settings=settings)
    window.show()
    attempts = [0]

    def inspect():
        attempts[0] += 1
        if window.devices_job.busy and attempts[0] < 40:
            QTimer.singleShot(250, inspect)
            return
        window.grab().save(str(directory / "windows-native.png"))
        print(f"Loopback choices: {window.devices.count()}")
        print(
            f"Device selector height: {window.devices.height()}; mode: {window.tabs.currentIndex()}"
        )
        print(window.console.toPlainText())
        window.close()

    QTimer.singleShot(750, inspect)
    app.exec()
    settings.clear()
    settings.sync()
    (directory / "native-settings.ini").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
