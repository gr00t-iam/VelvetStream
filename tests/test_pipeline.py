"""External network/device boundaries are faked; encoding/tagging are real."""

import queue
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC

from velvetstream.core import TrackMetadata
from velvetstream.services import YouTubeDownloader
from velvetstream.workers import CaptureWorker, FinalizeWorker, MetadataWorker


def test_playlist_keeps_successful_tracks_and_reports_unavailable_entry(tmp_path, monkeypatch):
    import velvetstream.services as services

    class Downloader:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            if not download:
                return {"_type": "playlist", "entries": [{"id": "one"}, None, {"id": "three"}]}
            self.path = Path(self.options["outtmpl"].replace("%(ext)s", "wav"))
            sf.write(self.path, np.full((441, 2), 0.25), 44100, subtype="PCM_24")
            return {
                "id": url.rsplit("=", 1)[1],
                "title": "Song",
                "channel": "Channel",
                "upload_date": "20260601",
                "ext": "wav",
            }

        def prepare_filename(self, info):
            return str(self.path)

    def extraction(url, template, directory, stop, log, progress, *, playlist=False, skip=None):
        with Downloader({"outtmpl": template}) as downloader:
            info = downloader.extract_info(url, download=not playlist)
            return {"info": info, "filename": "" if playlist else downloader.prepare_filename(info)}

    monkeypatch.setattr(services, "extract_youtube", extraction)
    logs = []
    worker = YouTubeDownloader(threading.Event(), logs.append, lambda v: None, lambda v: None)
    assert worker.run("https://youtube.com/playlist?list=test", tmp_path) == (2, 1)
    first, second = (
        tmp_path / "Channel - Song.flac",
        tmp_path / "Duplicate 2" / "Channel - Song.flac",
    )
    assert FLAC(first)["tracknumber"] == ["1"]
    assert FLAC(second)["tracknumber"] == ["3"]
    assert FLAC(second)["date"] == ["2026"]
    assert not list(tmp_path.glob(".velvetstream-download-*"))
    assert not list(tmp_path.glob(".velvetstream-list-*"))
    assert not list(tmp_path.glob(".velvetstream-journal-*"))


def test_capture_loop_routes_real_samples_and_recognizes_each_segment(tmp_path, monkeypatch):
    import soundcard

    stop, jobs, recognition_jobs = threading.Event(), queue.Queue(), queue.Queue()
    blocks = iter(
        [
            *[np.full((2400, 2), 0.25) for _ in range(80)],
            np.zeros((2400, 2)),
            np.zeros((2400, 2)),
            *[np.full((2400, 2), 0.5) for _ in range(80)],
        ]
    )

    class Recorder:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def record(self, numframes):
            try:
                block = next(blocks)
            except StopIteration:
                stop.set()
                return np.zeros((2400, 2))
            return block

    class Device:
        id, name, isloopback = "output", "Output monitor", True

        def recorder(self, **kwargs):
            assert kwargs["channels"] == 2 and kwargs["samplerate"] == 48000
            return Recorder()

    device = Device()
    monkeypatch.setattr(soundcard, "all_microphones", lambda **kwargs: [device])
    monkeypatch.setattr(soundcard, "get_microphone", lambda **kwargs: device)
    capture = CaptureWorker(stop, "output", tmp_path, 48000, -45, 0.1, jobs, recognition_jobs)
    capture.execute()

    class Engine:
        async def recognize(self, pcm, rate, stop):
            assert len(pcm) == 4 * rate
            return TrackMetadata("First" if np.max(pcm) == 0.25 else "Second", "Singer")

    MetadataWorker(threading.Event(), recognition_jobs, engine=Engine()).execute()
    finalizer = FinalizeWorker(stop, jobs)
    assert finalizer.execute() == {"saved": 2, "failed": 0}
    assert FLAC(tmp_path / "Singer - First.flac")["title"] == ["First"]
    assert FLAC(tmp_path / "Singer - Second.flac")["title"] == ["Second"]
    audio, rate = sf.read(tmp_path / "Singer - First.flac")
    assert rate == 48000 and np.all(audio == 0.25)


def test_failed_publication_preserves_recoverable_flac(tmp_path, monkeypatch):
    import velvetstream.core as core

    sink = core.FlacSink(tmp_path, 48000, 2, TrackMetadata("Song", "Artist"))
    sink.write(np.full((100, 2), 0.25))

    def fail(*args):
        raise OSError("filesystem cannot publish")

    monkeypatch.setattr(core, "publish_flac", fail)
    with pytest.raises(OSError):
        sink.finish()
    assert sink.path.is_file()
    assert not (tmp_path / "Artist - Song.flac").exists()
    assert FLAC(sink.path)["title"] == ["Song"]
