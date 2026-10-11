"""Local FLAC audition panel backed by Qt's asynchronous multimedia engine."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)


class FlacPlayer(QFrame):
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.capture_active = False
        self.compact = False
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.5)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        row = QHBoxLayout()
        title = QLabel("FLAC player")
        title.setObjectName("Heading")
        row.addWidget(title)
        self.tracks = QComboBox()
        self.tracks.setMinimumWidth(100)
        self.tracks.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.tracks.setMinimumContentsLength(18)
        self.tracks.setAccessibleName("Completed FLAC files")
        self.tracks.setPlaceholderText("Select a completed recording")
        self.tracks.activated.connect(self.select_track)
        row.addWidget(self.tracks, 1)
        self.open_button = QPushButton("Open FLAC")
        self.open_button.clicked.connect(self.choose_file)
        row.addWidget(self.open_button)
        layout.addLayout(row)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setMinimumHeight(20)
        self.seek.setAccessibleName("Playback position")
        self.seek.sliderReleased.connect(self.seek_to_position)
        layout.addWidget(self.seek)
        row = QHBoxLayout()
        self.play = QPushButton("Play")
        self.stop = QPushButton("Stop")
        self.play.setEnabled(False)
        self.stop.setEnabled(False)
        self.play.clicked.connect(self.toggle_play)
        self.stop.clicked.connect(self.player.stop)
        row.addWidget(self.play)
        row.addWidget(self.stop)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("Muted")
        row.addWidget(self.time_label)
        row.addStretch()
        row.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(50)
        self.volume.setMaximumWidth(130)
        self.volume.setAccessibleName("Player volume")
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        row.addWidget(self.volume)
        layout.addLayout(row)
        self.hint = QLabel("Listen to your saved files. Playback never starts automatically.")
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.player.durationChanged.connect(self.update_duration)
        self.player.positionChanged.connect(self.update_position)
        self.player.playbackStateChanged.connect(self.update_state)
        self.player.seekableChanged.connect(self.update_seekable)
        self.player.errorOccurred.connect(self.on_error)

    @Slot()
    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a FLAC recording", "", "FLAC audio (*.flac)"
        )
        if path:
            self.open_path(Path(path))

    def add_recording(self, path):
        path = Path(path).resolve()
        if path.suffix.lower() != ".flac" or not path.is_file():
            return
        index = self.tracks.findData(str(path))
        if index < 0:
            self.tracks.addItem(path.name, str(path))
            index = self.tracks.count() - 1
        # Preserve the user's current selection as new recordings arrive.
        if self.player.source().isEmpty():
            if self.tracks.currentIndex() < 0:
                self.tracks.setCurrentIndex(index)
            self.stop.setEnabled(True)
            self.play.setEnabled(not self.capture_active)

    def replace_recording(self, original, path):
        index = self.tracks.findData(str(Path(original).resolve()))
        was_open = (
            bool(self.player.source().toLocalFile())
            and Path(self.player.source().toLocalFile()).resolve() == Path(original).resolve()
        )
        if was_open:
            self.player.stop()
        if index >= 0:
            self.tracks.removeItem(index)
        self.add_recording(path)
        if was_open:
            self.open_path(path)

    def open_path(self, path):
        path = Path(path).resolve()
        if path.suffix.lower() != ".flac" or not path.is_file():
            self.message.emit("Player: select an existing FLAC file")
            return
        self.player.stop()
        self.add_recording(path)
        self.tracks.setCurrentIndex(self.tracks.findData(str(path)))
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.play.setEnabled(not self.capture_active)
        self.stop.setEnabled(True)

    @Slot(int)
    def select_track(self, index):
        path = self.tracks.itemData(index)
        if path:
            self.open_path(path)

    @Slot()
    def toggle_play(self):
        if self.capture_active:
            return
        if self.player.source().isEmpty():
            path = self.tracks.currentData()
            if not path:
                return
            self.open_path(path)
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def set_capture_active(self, active):
        self.capture_active = active
        if active:
            self.player.stop()
        self.play.setEnabled(
            not active and (not self.player.source().isEmpty() or bool(self.tracks.currentData()))
        )
        self.hint.setText(
            "Playback paused during live capture to avoid recording it again."
            if active
            else "Listen to your saved files. Playback never starts automatically."
        )
        self.hint.setVisible(active or not self.compact)

    def set_compact(self, compact):
        self.compact = compact
        self.hint.setVisible(self.capture_active or not compact)
        self.play.setToolTip(
            "Playback is disabled during live capture to prevent recording it again."
        )

    @Slot()
    def seek_to_position(self):
        if self.player.isSeekable():
            self.player.setPosition(self.seek.value())

    @Slot(bool)
    def update_seekable(self, seekable):
        self.seek.setEnabled(seekable)

    @Slot(int)
    def update_duration(self, duration):
        self.seek.setRange(0, max(0, duration))
        self.update_position(self.player.position())

    @Slot(int)
    def update_position(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)

        def stamp(value):
            seconds = max(0, value // 1000)
            return f"{seconds // 60}:{seconds % 60:02}"

        self.time_label.setText(f"{stamp(position)} / {stamp(self.player.duration())}")

    @Slot(object)
    def update_state(self, state):
        self.play.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    @Slot(object, str)
    def on_error(self, error, detail):
        self.play.setEnabled(False)
        self.message.emit("Player: " + detail[:600])

    def shutdown(self):
        self.player.stop()
        self.player.setSource(QUrl())
