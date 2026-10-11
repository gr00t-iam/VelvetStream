import importlib.util
import sys
from pathlib import Path

from PySide6.QtCore import QSettings


def test_complete_standalone_imports_and_switches_modes(qtbot, tmp_path):
    source = Path(__file__).resolve().parents[1] / "VelvetStreamStandalone.py"
    spec = importlib.util.spec_from_file_location("velvetstream_standalone_test", source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    settings = QSettings(str(tmp_path / "single.ini"), QSettings.Format.IniFormat)
    window = module.MainWindow(settings=settings, discover=False)
    qtbot.addWidget(window)
    window.show()
    assert window.devices.isVisible() and not hasattr(window, "client_id")
    window.tabs.setCurrentIndex(1)
    assert window.url.isVisible() and not window.devices.isVisible()
    window.theme.click()
    assert window.settings.value("theme") == "light"
    assert module.filename(module.TrackMetadata("Song", "Artist")) == "Artist - Song.flac"
    window.close()
