"""PySide6 interface, theme system, meter, and asynchronous job ownership."""

from __future__ import annotations

import math
import os
import queue
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QSettings,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QLinearGradient,
    QPainter,
    QTextBlockUserData,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .branding import application_icon, logo_pixmap
from .core import TrackMetadata
from .player import FlacPlayer
from .services import validate_youtube_url
from .workers import (
    CaptureWorker,
    DeviceWorker,
    FinalizeWorker,
    ManualTagWorker,
    MetadataWorker,
    Worker,
    YouTubeWorker,
)


def configure_fonts(app: QApplication):
    """Native system font, with an explicit fallback for offscreen Qt previews."""
    if not QFontDatabase.families():
        for path in (
            Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf",
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        ):
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
                break
    families = QFontDatabase.families()
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if font.family() not in families and families:
        font.setFamily(families[0])
    font.setPointSize(10)
    app.setFont(font)


def stylesheet(dark: bool) -> str:
    bg, panel, field = (
        ("#15171e", "#1e222c", "#171b24") if dark else ("#f3f5f8", "#ffffff", "#eef2f6")
    )
    text, muted, line = (
        ("#f4f7fc", "#a7b1c5", "#353e50") if dark else ("#172330", "#516174", "#c8d3de")
    )
    accent, hover, ink = "#3b1e54", "#532c73", "#ffffff"
    highlight = "#c9ace5" if dark else "#6c4389"
    return f"""
    QMainWindow, QDialog, QScrollArea, QWidget#MainSurface {{ background: {bg}; color: {text}; }}
    QScrollArea {{ border: none; }}
    QWidget {{ color: {text}; font-size: 13px; }}
    QWidget#MainSurface {{ border-radius: 12px; }}
    QFrame#Card {{ background: {panel}; border: 1px solid {line}; border-radius: 12px; }}
    QLabel {{ background: transparent; border: none; }}
    QLabel#Title {{ font-size: 26px; font-weight: 700; letter-spacing: 1px; }}
    QLabel#Heading {{ font-size: 15px; font-weight: 600; }}
    QLabel#Muted {{ color: {muted}; font-size: 12px; }}
    QLabel#Eyebrow {{ color: {highlight}; font-size: 10px; font-weight: 700; letter-spacing: 2px; }}
    QLabel#Badge {{ color: {highlight}; background: {field}; padding: 5px 8px; border-radius: 8px; }}
    QLabel#Status {{ color: {highlight}; font-weight: 600; }}
    QLineEdit, QComboBox, QPlainTextEdit {{ background: {field}; border: 1px solid {line};
        border-radius: 8px; padding: 7px; selection-background-color: {accent}; selection-color: {ink}; }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {highlight}; }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {muted}; }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox QAbstractItemView {{ background: {panel}; color: {text}; selection-background-color: {accent};
        selection-color: {ink}; border: 1px solid {line}; padding: 6px; }}
    QPushButton, QToolButton {{ background: {accent}; color: {ink}; border: 1px solid #684384; border-radius: 8px; padding: 7px 12px; font-weight: 600; }}
    QPushButton:hover, QToolButton:hover {{ background: {hover}; border-color: {highlight}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: #2b153e; border-color: {highlight}; }}
    QPushButton:focus, QToolButton:focus {{ border: 1px solid {highlight}; }}
    QPushButton:disabled, QToolButton:disabled {{ background: {field}; color: {muted}; border-color: {line}; }}
    QPushButton#Primary {{ background: {accent}; color: {ink}; border: 1px solid #684384; padding: 7px 18px; }}
    QPushButton#Primary:hover {{ background: {hover}; }}
    QPushButton#Primary:pressed {{ background: #2b153e; }}
    QPushButton#Primary:disabled {{ background: {line}; color: {muted}; }}
    QTabBar::tab {{ background: {field}; color: {muted}; border-radius: 8px; padding: 8px 18px; margin: 2px; font-weight: 600; }}
    QTabBar::tab:selected {{ background: {accent}; color: {ink}; }}
    QTabBar::tab:hover:!selected {{ color: {text}; background: {line}; }}
    QSlider::groove:horizontal {{ height: 5px; background: {line}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {highlight}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {highlight}; border: 2px solid {panel}; width: 16px; height: 16px;
        margin: -7px 0; border-radius: 9px; }}
    QSlider::handle:horizontal:hover {{ background: {hover}; }}
    QProgressBar {{ background: {field}; border: none; border-radius: 5px; min-height: 7px; max-height: 7px; }}
    QProgressBar::chunk {{ background: {highlight}; border-radius: 5px; }}
    QScrollBar:vertical {{ background: {field}; width: 10px; margin: 0; border-radius: 5px; }}
    QScrollBar::handle:vertical {{ background: {line}; min-height: 24px; border-radius: 5px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QToolTip {{ background: {panel}; color: {text}; border: 1px solid {line}; padding: 6px; }}
    """


class LevelMeter(QWidget):
    """60 Hz GUI-side ballistic meter; capture only sends scalar peak values."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setAccessibleName("System output peak level meter in dBFS")
        self.target = self.display = -60.0
        self.dark = True
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.animate)

    @Slot(float)
    def set_level(self, value):
        self.target = max(-60.0, min(0.0, value)) if math.isfinite(value) else -60.0

    def set_active(self, active: bool):
        if active:
            self.timer.start()
        else:
            self.timer.stop()
            self.target = self.display = -60.0
            self.update()

    @Slot()
    def animate(self):
        factor = 0.55 if self.target > self.display else 0.07
        self.display += (self.target - self.display) * factor
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(1, self.width() - 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#121620" if self.dark else "#e4eaf0"))
        painter.drawRoundedRect(0, 3, width, 16, 5, 5)
        fill = max(0.0, (self.display + 60) / 60) * width
        gradient = QLinearGradient(0, 0, width, 0)
        gradient.setColorAt(0, QColor("#3b1e54"))
        gradient.setColorAt(0.75, QColor("#c9ace5"))
        gradient.setColorAt(0.9, QColor("#ffcf70"))
        gradient.setColorAt(1, QColor("#ff6b85"))
        painter.setBrush(gradient)
        painter.drawRoundedRect(0.0, 3.0, fill, 16.0, 5.0, 5.0)
        painter.setPen(QColor("#a7b1c5" if self.dark else "#516174"))
        painter.setFont(QFont(self.font().family(), 9))
        for value in (-60, -36, -24, -12, 0):
            x = int((value + 60) / 60 * width)
            label = str(value)
            x = max(0, min(width - 20, x - 10))
            painter.drawText(x, 35, label)


class RecordingBlockData(QTextBlockUserData):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path


class ActivityConsole(QPlainTextEdit):
    recording_clicked = Signal(object)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            data = self.cursorForPosition(event.position().toPoint()).block().userData()
            if isinstance(data, RecordingBlockData):
                self.recording_clicked.emit(data.path)


class TagEditor(QDialog):
    submitted = Signal(object, object)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("VelvetStream · Edit recording tags")
        self.setMinimumWidth(480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        hint = QLabel(path.name)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        for field, caption in (
            ("title", "Title"),
            ("artist", "Artist"),
            ("album", "Album"),
            ("year", "Year"),
        ):
            widget = QLineEdit()
            widget.setAccessibleName(caption)
            setattr(self, field, widget)
            form.addRow(caption, widget)
        self.year.setMaxLength(4)
        layout.addLayout(form)
        self.validation = QLabel("Title and artist are required. Audio is preserved.")
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.save = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save.setObjectName("Primary")
        buttons.accepted.connect(self.submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @Slot()
    def submit(self):
        title, artist, album, year = (
            getattr(self, name).text().strip() for name in ("title", "artist", "album", "year")
        )
        if (
            not title
            or not artist
            or (year and (len(year) != 4 or not year.isascii() or not year.isdigit()))
        ):
            self.validation.setText("Enter a title and artist; year must be four digits or empty.")
            return
        self.submitted.emit(
            self.path,
            TrackMetadata(title, artist, album, year, comment="Manually tagged in VelvetStream"),
        )
        self.accept()


class ResultRelay(QObject):
    def __init__(self, role, controller):
        super().__init__(controller)
        self.role, self.controller = role, controller

    @Slot(object)
    def forward(self, value):
        self.controller.result.emit(self.role, value)


class JobController(QObject):
    """Retains both QObject and QThread until native thread completion."""

    busy_changed = Signal(bool)
    idle = Signal()
    log = Signal(str)
    error = Signal(str)
    state = Signal(str)
    progress = Signal(int)
    level = Signal(float)
    result = Signal(str, object)
    manual_required = Signal(object)
    recording_saved = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._threads: dict[QThread, Worker] = {}
        self._relays: dict[QThread, ResultRelay] = {}

    @property
    def busy(self):
        return bool(self._threads)

    def start(self, role: str, worker: Worker):
        thread = QThread(self)
        thread.setObjectName(f"VelvetStream-{role}")
        worker.setProperty("role", role)
        worker.moveToThread(thread)
        worker.log.connect(self.log)
        worker.error.connect(self.error)
        worker.state.connect(self.state)
        worker.progress.connect(self.progress)
        worker.level.connect(self.level)
        worker.manual_required.connect(self.manual_required)
        worker.recording_saved.connect(self.recording_saved)
        relay = ResultRelay(role, self)
        self._relays[thread] = relay
        worker.result.connect(relay.forward)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._finished)
        was_busy = self.busy
        self._threads[thread] = worker
        if not was_busy:
            self.busy_changed.emit(True)
        thread.start()

    @Slot()
    def _finished(self):
        thread = self.sender()
        self._threads.pop(thread, None)
        self._relays.pop(thread).deleteLater()
        thread.deleteLater()
        if not self.busy:
            self.busy_changed.emit(False)
            self.idle.emit()

    def stop_all(self):
        # Events are thread-safe; queued stop slots cannot interrupt a blocking loop.
        for worker in self._threads.values():
            worker.stop.set()

    def skip_youtube(self):
        for worker in self._threads.values():
            if isinstance(worker, YouTubeWorker):
                worker.skip.set()


def label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(8)
    return frame, layout


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, discover: bool = True):
        super().__init__()
        configure_fonts(QApplication.instance())
        self.setWindowTitle("VelvetStream · Audio workspace")
        self.setWindowIcon(application_icon())
        self.resize(920, 760)
        self.setMinimumSize(760, 640)
        self.compact = None
        self.silence_user_changed = False
        self.settings = (
            settings if settings is not None else QSettings("VelvetStream", "VelvetStream")
        )
        self.closing = False
        self.had_error = False
        self.stopping = False
        self.operation = ""
        self.jobs = JobController(self)
        self.devices_job = JobController(self)
        self.tag_jobs = JobController(self)
        self.tag_dialog = None
        self.jobs.busy_changed.connect(self.set_busy)
        self.jobs.log.connect(self.append_log)
        self.jobs.error.connect(self.on_error)
        self.jobs.state.connect(self.on_state)
        self.jobs.progress.connect(self.on_progress)
        self.jobs.level.connect(self.on_level)
        self.jobs.result.connect(self.on_result)
        self.jobs.idle.connect(self.on_idle)
        self.jobs.manual_required.connect(self.on_manual_required)
        self.jobs.recording_saved.connect(self.on_recording_saved)
        self.tag_jobs.log.connect(self.append_log)
        self.tag_jobs.error.connect(self.append_log)
        self.tag_jobs.result.connect(self.on_result)
        self.tag_jobs.busy_changed.connect(self.on_tags_busy)
        self.tag_jobs.idle.connect(self.maybe_close_if_requested)
        self.devices_job.log.connect(self.append_log)
        self.devices_job.error.connect(self.on_device_error)
        self.devices_job.result.connect(self.on_result)
        self.devices_job.idle.connect(self.on_devices_idle)
        self._build()
        self.restore_settings()
        self.apply_theme()
        self.append_log("Ready. Choose an output folder and a source mode.")
        if discover:
            QTimer.singleShot(0, self.refresh_devices)

    def _build(self):
        surface = QWidget()
        surface.setObjectName("MainSurface")
        self.setCentralWidget(surface)
        root = QVBoxLayout(surface)
        self.root_layout = root
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.logo = QLabel()
        self.logo.setPixmap(logo_pixmap(56))
        self.logo.setAccessibleName("VelvetStream logo")
        header.addWidget(self.logo)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.eyebrow = label("YOUR AUDIO. BEAUTIFULLY PRESERVED.", "Eyebrow")
        titles.addWidget(self.eyebrow)
        titles.addWidget(label("VelvetStream", "Title"))
        self.tagline = label("Capture the moment. Keep the detail.", "Muted")
        titles.addWidget(self.tagline)
        header.addLayout(titles)
        header.addStretch()
        header.addWidget(label("FLAC  /  24 BIT", "Badge"))
        self.theme = QPushButton("Light mode")
        self.theme.setAccessibleName("Switch between dark and light theme")
        self.theme.clicked.connect(self.toggle_theme)
        header.addWidget(self.theme)
        root.addLayout(header)

        self.tabs = QTabBar()
        self.tabs.addTab("Live Capture")
        self.tabs.addTab("YouTube Extraction")
        self.tabs.setExpanding(True)
        self.tabs.setDrawBase(False)
        self.tabs.setAccessibleName("Audio source mode")
        self.tabs.currentChanged.connect(self.change_mode)
        root.addWidget(self.tabs)

        self.stack = QStackedWidget()
        self.stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.live_page = QWidget()
        self.youtube_page = QWidget()
        self.stack.addWidget(self.live_page)
        self.stack.addWidget(self.youtube_page)
        self.build_live()
        self.build_youtube()
        root.addWidget(self.stack)

        folder, folder_layout = card()
        row = QHBoxLayout()
        row.addWidget(label("Save to", "Muted"))
        self.output = QLineEdit()
        self.output.setAccessibleName("Output directory")
        self.browse = QPushButton("Choose folder")
        self.browse.clicked.connect(self.choose_folder)
        row.addWidget(self.output, 1)
        row.addWidget(self.browse)
        folder_layout.addLayout(row)
        root.addWidget(folder)

        activity, layout = card()
        row = QHBoxLayout()
        row.addWidget(label("Session", "Heading"))
        self.status = label("●  Idle", "Status")
        row.addStretch()
        row.addWidget(self.status)
        self.skip_track = QPushButton("Skip track")
        self.skip_track.clicked.connect(self.skip_current_track)
        self.skip_track.hide()
        row.addWidget(self.skip_track)
        self.retry = QPushButton("Retry / resume")
        self.retry.clicked.connect(self.perform_action)
        self.retry.hide()
        row.addWidget(self.retry)
        self.action = QPushButton("Start capture")
        self.action.setObjectName("Primary")
        self.action.setMinimumWidth(150)
        self.action.setToolTip("Lossless FLAC encoding. Quality depends on the source.")
        self.action.clicked.connect(self.perform_action)
        row.addWidget(self.action)
        layout.addLayout(row)
        self.meter = LevelMeter()
        layout.addWidget(self.meter)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        root.addWidget(activity)

        self.player_panel = FlacPlayer(self)
        self.player_panel.message.connect(self.append_log)
        root.addWidget(self.player_panel)

        log_card, layout = card()
        self.log_card = log_card
        row = QHBoxLayout()
        self.latest_log = label("Ready", "Muted")
        self.latest_log.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.latest_log, 1)
        self.log_toggle = QToolButton()
        self.log_toggle.setText("Show activity")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.toggle_activity)
        row.addWidget(self.log_toggle)
        clear = QPushButton("Clear")
        self.clear_log = clear
        row.addWidget(clear)
        layout.addLayout(row)
        self.console = ActivityConsole()
        self.console.recording_clicked.connect(self.open_tag_editor)
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(1200)
        self.console.setMinimumHeight(64)
        self.console.setFont(QFont("Consolas", 10))
        self.console.setAccessibleName("Live activity log")
        clear.clicked.connect(self.console.clear)
        layout.addWidget(self.console)
        self.manual_panel = QWidget()
        manual_row = QHBoxLayout(self.manual_panel)
        manual_row.setContentsMargins(0, 0, 0, 0)
        manual_row.addWidget(label("Unidentified recordings", "Muted"))
        self.pending_tags = QComboBox()
        self.pending_tags.setAccessibleName("Recordings available for manual tags")
        manual_row.addWidget(self.pending_tags, 1)
        self.edit_tags = QPushButton("Edit tags")
        self.edit_tags.clicked.connect(lambda: self.open_tag_editor())
        manual_row.addWidget(self.edit_tags)
        layout.addWidget(self.manual_panel)
        self.manual_panel.hide()
        root.addWidget(log_card, 1)
        root.addStretch(1)
        self.console.hide()
        self.clear_log.hide()
        log_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.fade = QPropertyAnimation(self, b"windowOpacity", self)

    def build_live(self):
        outer = QVBoxLayout(self.live_page)
        outer.setContentsMargins(0, 0, 0, 0)
        panel, layout = card()
        row = QHBoxLayout()
        row.addWidget(label("Listen & capture", "Heading"))
        row.addStretch()
        row.addWidget(label("Shazam → AcoustID → Manual", "Muted"))
        self.silence_toggle = QToolButton()
        self.silence_toggle.setText("Silence settings")
        self.silence_toggle.setCheckable(True)
        self.silence_toggle.setChecked(True)
        self.silence_toggle.toggled.connect(self.toggle_silence)
        row.addWidget(self.silence_toggle)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.devices = QComboBox()
        self.devices.setAccessibleName("System output loopback device")
        self.devices.addItem("Refresh to discover output monitors", None)
        self.devices.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.devices.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.devices.setMinimumContentsLength(18)
        self.refresh = QPushButton("Refresh")
        self.refresh.clicked.connect(self.refresh_devices)
        self.rate = QComboBox()
        self.rate.addItem("48,000 Hz", 48000)
        self.rate.addItem("44,100 Hz", 44100)
        self.rate.addItem("96,000 Hz", 96000)
        self.rate.setAccessibleName("Capture sample rate")
        self.rate.setToolTip(
            "Match your system output / virtual device rate. Higher rates do not restore lossy source detail."
        )
        row.addWidget(self.devices, 1)
        row.addWidget(self.refresh)
        row.addWidget(self.rate)
        layout.addLayout(row)
        self.silence_controls = QWidget()
        row = QHBoxLayout(self.silence_controls)
        row.setContentsMargins(0, 0, 0, 0)
        for name, attr, low, high, default in (
            ("Silence threshold", "threshold", -80, -15, -45),
            ("Split after silence", "duration", 2, 100, 15),
        ):
            column = QVBoxLayout()
            caption = label("", "Muted")
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setMinimumHeight(24)
            slider.setRange(low, high)
            slider.setValue(default)
            slider.setAccessibleName(name)
            setattr(self, attr, slider)
            setattr(self, attr + "_label", caption)
            slider.valueChanged.connect(self.update_slider_labels)
            column.addWidget(caption)
            column.addWidget(slider)
            row.addLayout(column, 1)
        layout.addWidget(self.silence_controls)
        self.live_hint = label("Output monitors only · macOS needs BlackHole routing", "Muted")
        layout.addWidget(self.live_hint)
        outer.addWidget(panel)
        self.update_slider_labels()

    def build_youtube(self):
        outer = QVBoxLayout(self.youtube_page)
        outer.setContentsMargins(0, 0, 0, 0)
        panel, layout = card()
        layout.addWidget(label("YouTube video or playlist", "Heading"))
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://www.youtube.com/watch?v=…  or  /playlist?list=…")
        self.url.setAccessibleName("YouTube video or playlist URL")
        self.url.setMinimumHeight(34)
        self.url.setMaxLength(4096)
        layout.addWidget(self.url)
        self.url.setToolTip(
            "Video links extract only the selected video. Paste an explicit playlist link for a batch."
        )
        self.youtube_hint = label(
            "Video link = one track · Playlist link = batch · Tagged FLAC", "Muted"
        )
        layout.addWidget(self.youtube_hint)
        outer.addWidget(panel)

    @Slot()
    def update_slider_labels(self):
        if hasattr(self, "duration"):
            self.threshold_label.setText(f"Silence threshold  ·  {self.threshold.value()} dBFS")
            self.duration_label.setText(
                f"Split after silence  ·  {self.duration.value() / 10:.1f} s"
            )

    def restore_settings(self):
        music = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MusicLocation)
        self.output.setText(
            str(self.settings.value("output", str(Path(music or Path.home()) / "VelvetStream")))
        )
        for key in ("client_id", "client_secret", "redirect_uri"):
            self.settings.remove(key)
        self.threshold.setValue(int(self.settings.value("threshold", -45)))
        self.duration.setValue(int(self.settings.value("duration", 15)))
        index = self.rate.findData(int(self.settings.value("rate", 48000)))
        self.rate.setCurrentIndex(max(0, index))
        self.tabs.setCurrentIndex(1 if self.settings.value("mode", "live") == "youtube" else 0)
        self.dark = self.settings.value("theme", "dark") != "light"
        self.url.setText(str(self.settings.value("youtube_url", ""))[:4096])
        self.player_panel.volume.setValue(
            max(0, min(100, int(self.settings.value("player_volume", 50))))
        )

    def save_settings(self):
        for key, value in {
            "output": self.output.text(),
            "threshold": self.threshold.value(),
            "duration": self.duration.value(),
            "rate": self.rate.currentData(),
            "theme": "dark" if self.dark else "light",
            "mode": "youtube" if self.tabs.currentIndex() else "live",
            "device": self.devices.currentData() or "",
            "youtube_url": self.url.text(),
            "player_volume": self.player_panel.volume.value(),
        }.items():
            self.settings.setValue(key, value)
        self.settings.sync()

    def apply_theme(self):
        self.setStyleSheet(stylesheet(self.dark))
        self.theme.setText("Light mode" if self.dark else "Dark mode")
        self.meter.dark = self.dark
        self.meter.update()

    @Slot()
    def toggle_theme(self):
        self.dark = not self.dark
        self.apply_theme()
        self.settings.setValue("theme", "dark" if self.dark else "light")

    @Slot(int)
    def change_mode(self, index):
        self.stack.setCurrentIndex(index)
        page = self.stack.currentWidget()
        if hasattr(self, "fade"):
            self.fade.stop()
            self.fade.deleteLater()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        effect.setOpacity(0.35)
        self.fade = QPropertyAnimation(effect, b"opacity", self)
        self.fade.setDuration(180)
        self.fade.setStartValue(0.35)
        self.fade.setEndValue(1.0)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.start()
        if hasattr(self, "action") and not self.jobs.busy:
            self.action.setText("Extract to FLAC" if index else "Start capture")
            self.meter.set_active(False)
            self.progress.setValue(0)
            self.retry.setVisible(index == 1 and self.retry.property("available") is True)
        if hasattr(self, "player_panel"):
            self.update_source_height()

    @Slot()
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose FLAC output directory", self.output.text()
        )
        if folder:
            self.output.setText(folder)

    @Slot(str)
    def append_log(self, message):
        self.console.appendPlainText(f"{datetime.now():%H:%M:%S}  {message[:3000]}")
        self.latest_log.setText(message[:180])
        self.latest_log.setToolTip(message[:3000])

    @Slot(bool)
    def toggle_activity(self, expanded):
        self.console.setVisible(expanded)
        self.clear_log.setVisible(expanded)
        self.log_toggle.setText("Hide activity" if expanded else "Show activity")
        self.log_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding if expanded else QSizePolicy.Policy.Fixed,
        )
        self.root_layout.setStretch(self.root_layout.count() - 1, 0 if expanded else 1)

    @Slot(bool)
    def toggle_silence(self, expanded):
        self.silence_user_changed = True
        self.silence_controls.setVisible(expanded)
        self.update_source_height()

    def update_source_height(self):
        page = self.stack.currentWidget()
        page.layout().invalidate()
        self.stack.setFixedHeight(page.layout().sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "player_panel"):
            return
        compact = self.height() < 850
        if compact != self.compact:
            self.compact = compact
            self.eyebrow.setVisible(not compact)
            self.tagline.setVisible(not compact)
            self.live_hint.setVisible(not compact)
            self.youtube_hint.setVisible(not compact)
            self.player_panel.set_compact(compact)
            if not self.silence_user_changed:
                self.silence_toggle.blockSignals(True)
                self.silence_toggle.setChecked(not compact)
                self.silence_toggle.blockSignals(False)
                self.silence_controls.setVisible(not compact)
        self.update_source_height()

    @Slot(object)
    def on_manual_required(self, path):
        path = Path(path)
        if self.pending_tags.findData(str(path)) < 0:
            self.pending_tags.addItem(path.name, str(path))
        self.manual_panel.show()
        self.append_log(f"Unidentified · click to edit tags: {path.name}")
        self.console.document().lastBlock().setUserData(RecordingBlockData(path))

    @Slot(object)
    def open_tag_editor(self, path=None):
        if self.closing or self.tag_jobs.busy:
            return
        if self.tag_dialog is not None:
            self.tag_dialog.raise_()
            return
        path = path or self.pending_tags.currentData()
        if not path or self.pending_tags.findData(str(path)) < 0:
            return
        self.tag_dialog = TagEditor(Path(path), self)
        self.tag_dialog.submitted.connect(self.save_manual_tags)
        self.tag_dialog.finished.connect(self.on_tag_dialog_closed)
        self.tag_dialog.open()

    @Slot()
    def on_tag_dialog_closed(self):
        self.tag_dialog = None

    @Slot(object, object)
    def save_manual_tags(self, path, metadata):
        self.tag_jobs.start("manual", ManualTagWorker(threading.Event(), path, metadata))

    @Slot(bool)
    def on_tags_busy(self, busy):
        self.edit_tags.setEnabled(not busy)
        self.pending_tags.setEnabled(not busy)

    @Slot()
    def maybe_close_if_requested(self):
        if self.closing:
            self.maybe_close()

    @Slot()
    def refresh_devices(self):
        if self.jobs.busy or self.devices_job.busy or self.closing:
            return
        self.refresh.setEnabled(False)
        self.devices.setEnabled(False)
        self.append_log("Discovering system output monitors…")
        self.devices_job.start("devices", DeviceWorker(threading.Event()))

    @Slot(str)
    def on_device_error(self, message):
        self.append_log("Device discovery failed: " + message)

    @Slot()
    def on_devices_idle(self):
        if self.closing:
            self.maybe_close()
            return
        self.refresh.setEnabled(not self.jobs.busy)
        self.devices.setEnabled(not self.jobs.busy)

    @Slot(str, object)
    def on_result(self, role, value):
        if role == "manual":
            self.player_panel.replace_recording(value["original"], value["path"])
            index = self.pending_tags.findData(str(value["original"]))
            if index >= 0:
                self.pending_tags.removeItem(index)
            self.manual_panel.setVisible(self.pending_tags.count() > 0)
        elif role == "devices":
            selected = self.devices.currentData() or self.settings.value("device", "")
            self.devices.clear()
            for device in value:
                self.devices.addItem(device.name, device.id)
            if not value:
                self.devices.addItem("No output monitors found — see platform setup", None)
                self.append_log(
                    "No loopback devices found. Microphones are excluded. See README platform routing."
                )
            elif selected:
                index = self.devices.findData(selected)
                if index >= 0:
                    self.devices.setCurrentIndex(index)

    @Slot(bool)
    def set_busy(self, busy):
        self.tabs.setEnabled(not busy)
        self.live_page.setEnabled(not busy)
        self.youtube_page.setEnabled(not busy)
        self.output.setEnabled(not busy)
        self.browse.setEnabled(not busy)
        self.skip_track.setVisible(busy and self.operation == "youtube")
        self.skip_track.setEnabled(busy)
        self.retry.setVisible(
            not busy and self.tabs.currentIndex() == 1 and self.retry.property("available") is True
        )
        self.player_panel.set_capture_active(busy and self.operation == "capture")
        if busy:
            self.action.setText("Stop")
            self.action.setEnabled(True)
        else:
            self.action.setEnabled(True)
            self.action.setText("Extract to FLAC" if self.tabs.currentIndex() else "Start capture")
            self.refresh.setEnabled(not self.devices_job.busy)
            self.devices.setEnabled(not self.devices_job.busy)
            self.meter.set_active(False)

    @Slot(str)
    def on_state(self, state):
        if self.stopping:
            return
        self.status.setText("●  " + state)
        self.meter.set_active(state == "Recording")

    @Slot(float)
    def on_level(self, value):
        if not self.stopping:
            self.meter.set_level(value)

    @Slot(int)
    def on_progress(self, value):
        if value < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(value)

    @Slot(str)
    def on_error(self, message):
        self.had_error = True
        self.append_log("Error: " + message)
        if not self.stopping:
            self.status.setText("●  Error")

    @Slot()
    def perform_action(self):
        if self.jobs.busy:
            self.stop_jobs()
            return
        if not self.output.text().strip():
            self.on_error("Choose an output directory first")
            return
        self.had_error = False
        self.stopping = False
        self.save_settings()
        output = Path(self.output.text().strip())
        stop = threading.Event()
        if self.tabs.currentIndex() == 1:
            try:
                url = validate_youtube_url(self.url.text())
            except ValueError as exc:
                self.on_error(str(exc))
                return
            self.operation = "youtube"
            self.retry.setProperty("available", True)
            self.jobs.start("youtube", YouTubeWorker(stop, url, output))
        else:
            if self.devices_job.busy or not self.devices.currentData():
                self.on_error("Select an available system output monitor first")
                return
            self.operation = "capture"
            self.status.setText("●  Preparing capture")
            jobs, recognition_jobs = queue.Queue(maxsize=8), queue.Queue(maxsize=8)
            self.jobs.start("finalize", FinalizeWorker(stop, jobs))
            metadata_worker = MetadataWorker(stop, recognition_jobs)
            capture_worker = CaptureWorker(
                stop,
                self.devices.currentData(),
                output,
                self.rate.currentData(),
                self.threshold.value(),
                self.duration.value() / 10,
                jobs,
                None,
            )
            capture_worker.recognition_requested.connect(
                metadata_worker.submit, Qt.ConnectionType.DirectConnection
            )
            self.jobs.start("metadata", metadata_worker)
            self.jobs.start("capture", capture_worker)

    def stop_jobs(self):
        self.stopping = True
        self.jobs.stop_all()
        self.action.setText("Stopping…")
        self.action.setEnabled(False)
        self.skip_track.setEnabled(False)
        self.status.setText("●  Stopping")
        self.meter.set_active(False)
        self.append_log(
            "Stopping extraction; completed FLAC files stay saved."
            if self.operation == "youtube"
            else "Stopping safely; captured audio is finalized before release."
        )

    @Slot()
    def skip_current_track(self):
        self.jobs.skip_youtube()
        self.append_log("Skipping the current extraction; the playlist will continue.")

    @Slot(object)
    def on_recording_saved(self, path):
        self.player_panel.add_recording(path)

    @Slot()
    def on_idle(self):
        self.status.setText("●  Error" if self.had_error else "●  Idle")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.stopping = False
        self.operation = ""
        if self.closing:
            self.maybe_close()

    def maybe_close(self):
        if not self.jobs.busy and not self.devices_job.busy and not self.tag_jobs.busy:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event):
        self.save_settings()
        self.player_panel.shutdown()
        if self.jobs.busy or self.devices_job.busy or self.tag_jobs.busy:
            event.ignore()
            self.closing = True
            self.devices_job.stop_all()
            self.tag_jobs.stop_all()
            if self.tag_dialog is not None:
                self.tag_dialog.reject()
            if self.jobs.busy and not self.stopping:
                self.stop_jobs()
            self.status.setText("●  Closing safely…")
        else:
            event.accept()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("VelvetStream")
    app.setOrganizationName("VelvetStream")
    app.setStyle("Fusion")
    configure_fonts(app)
    window = MainWindow()
    window.show()
    return app.exec()
