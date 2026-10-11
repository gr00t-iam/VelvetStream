import threading

from PySide6.QtCore import QSettings, QThread

from velvetstream.ui import JobController, MainWindow, configure_fonts
from velvetstream.workers import Worker


def window(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings=settings, discover=False)
    qtbot.addWidget(w)
    w.show()
    return w


def test_mode_switch_exposes_only_corresponding_fields(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    assert w.devices.isVisible() and not w.url.isVisible()
    assert not hasattr(w, "client_id") and not hasattr(w, "authorize")
    assert w.tabs.tabText(0) == "Live Capture"
    w.tabs.setCurrentIndex(1)
    assert w.url.isVisible() and not w.devices.isVisible()
    assert w.action.text() == "Extract to FLAC"
    w.tabs.setCurrentIndex(0)
    assert w.action.text() == "Start capture"


def test_theme_switch_persists_and_keeps_mode(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    w.tabs.setCurrentIndex(1)
    w.theme.click()
    assert w.settings.value("theme") == "light"
    assert w.tabs.currentIndex() == 1 and w.url.isVisible()
    assert "#f3f5f8" in w.styleSheet()


def test_busy_state_locks_configuration_and_exposes_stop(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    w.set_busy(True)
    assert not w.tabs.isEnabled() and not w.output.isEnabled()
    assert not w.devices.isEnabled() and w.action.text() == "Stop"
    w.set_busy(False)
    assert w.tabs.isEnabled() and w.action.text() == "Start capture"


def test_qthread_runs_worker_off_gui_and_cancellation_drains(qtbot):
    class Blocking(Worker):
        def execute(self):
            self.result.emit(QThread.currentThread())
            self.stop.wait(3)

    jobs = JobController()
    worker = Blocking(threading.Event())
    results = []
    jobs.result.connect(lambda role, value: results.append(value))
    jobs.start("test", worker)
    qtbot.waitUntil(lambda: bool(results), timeout=2000)
    assert results[0] is not QThread.currentThread()
    jobs.stop_all()
    qtbot.waitUntil(lambda: not jobs.busy, timeout=2000)


def test_close_active_window_requests_stop_and_waits(qtbot, tmp_path):
    class Blocking(Worker):
        def execute(self):
            self.stop.wait(3)

    w = window(qtbot, tmp_path)
    stop = threading.Event()
    w.jobs.start("test", Blocking(stop))
    w.close()
    assert stop.is_set()
    qtbot.waitUntil(lambda: not w.isVisible(), timeout=2000)
    assert not w.jobs.busy


def test_preview_font_can_render_ascii(qapp):
    from PySide6.QtGui import QFontMetrics

    configure_fonts(qapp)
    assert QFontMetrics(qapp.font()).inFont("A")


def test_live_controls_remain_readable_at_small_window_size(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    w.resize(800, 810)
    qtbot.wait(30)
    assert w.devices.height() >= 32
    assert w.duration.height() >= 16


def test_live_capture_starts_without_oauth(qtbot, tmp_path, monkeypatch):
    w = window(qtbot, tmp_path)
    w.devices.clear()
    w.devices.addItem("Output monitor", "id")
    roles = []
    monkeypatch.setattr(w.jobs, "start", lambda role, worker: roles.append(role))
    w.perform_action()
    assert roles == ["finalize", "metadata", "capture"]


def test_legacy_api_settings_are_removed(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    for key in ("client_id", "client_secret", "redirect_uri"):
        settings.setValue(key, "old value")
    w = MainWindow(settings=settings, discover=False)
    qtbot.addWidget(w)
    assert not any(settings.contains(key) for key in ("client_id", "client_secret", "redirect_uri"))


def test_small_window_keeps_actions_and_player_visible_without_page_scroll(qtbot, tmp_path):
    from PySide6.QtWidgets import QScrollArea

    w = window(qtbot, tmp_path)
    w.resize(820, 700)
    for mode in (0, 1):
        w.tabs.setCurrentIndex(mode)
        qtbot.wait(200)
        assert w.height() <= 700
        assert not w.findChildren(QScrollArea)
        for control in (w.action, w.player_panel.open_button, w.player_panel.play, w.log_toggle):
            point = control.mapTo(w.centralWidget(), control.rect().bottomRight())
            assert w.centralWidget().rect().contains(point)
    assert not w.console.isVisible()
    w.log_toggle.click()
    assert w.console.isVisible()
    w.log_toggle.click()
    assert not w.console.isVisible()


def test_velvet_buttons_have_white_text_in_both_themes(qtbot, tmp_path):
    w = window(qtbot, tmp_path)
    for dark in (True, False):
        w.dark = dark
        w.apply_theme()
        qtbot.wait(30)
        image = w.action.grab().toImage()
        assert image.pixelColor(15, w.action.height() // 2).name() == "#3b1e54"
