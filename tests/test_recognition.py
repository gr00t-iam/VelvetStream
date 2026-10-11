import asyncio
import queue
import threading
from datetime import datetime
from io import BytesIO

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC

from velvetstream.core import FlacSink, TrackMetadata, filename
from velvetstream.recognition import (
    FourSecondSample,
    RecognitionRequest,
    RecognitionTicket,
    ShazamRecognizer,
    metadata_from_shazam,
)
from velvetstream.workers import FinalizeWorker, MetadataWorker


def response():
    return {
        "matches": [{"id": "123"}],
        "track": {
            "title": "Song",
            "subtitle": "Singer",
            "key": "123",
            "url": "https://www.shazam.com/track/123",
            "images": {
                "coverart": "https://is1-ssl.mzstatic.com/small.jpg",
                "coverarthq": "https://is1-ssl.mzstatic.com/large.jpg",
            },
            "sections": [
                {"type": "SONG"},
                {
                    "type": "METADATA",
                    "metadata": [
                        {"title": "Album", "text": "Record"},
                        {"title": "Released", "text": "2024-09-12"},
                    ],
                },
            ],
        },
    }


def test_shazam_metadata_uses_album_release_year_and_largest_art():
    metadata = metadata_from_shazam(response())
    assert (metadata.title, metadata.artist, metadata.album, metadata.year) == (
        "Song",
        "Singer",
        "Record",
        "2024",
    )
    assert metadata.art_url == "https://is1-ssl.mzstatic.com/large.jpg"
    assert metadata.source_id == "123"
    assert metadata_from_shazam({"matches": []}) is None


def test_four_second_sample_is_exact_and_bounded():
    sample = FourSecondSample(100)
    assert sample.add(np.full((250, 2), 0.25)) is None
    pcm = sample.add(np.full((250, 2), 0.5))
    assert pcm.shape == (400, 2)
    assert np.all(pcm[:250] == 0.25) and np.all(pcm[250:] == 0.5)
    assert sample.add(np.full((500, 2), 0.75)) is None
    assert sample.buffered_frames == 0


def test_timestamp_fallback_has_exact_requested_basename():
    fallback = TrackMetadata.recording(datetime(2026, 10, 9, 22, 33, 44))
    assert filename(fallback) == "Recording_20261009_223344.flac"


def test_late_result_cannot_modify_a_sealed_recording():
    ticket = RecognitionTicket(TrackMetadata.recording(datetime(2026, 10, 9, 22, 33, 44)))
    final = ticket.seal(timeout=0)
    assert not ticket.resolve(TrackMetadata("Late Song", "Wrong Artist"))
    assert ticket.seal(timeout=0) == final


def test_async_recognizer_passes_wav_bytes_not_raw_float_memory():
    class Recognizer:
        async def recognize(self, data):
            pcm, rate = sf.read(BytesIO(data), dtype="float32")
            assert pcm.shape == (400, 2) and rate == 100
            assert np.all(pcm == 0.25)
            return response()

    engine = ShazamRecognizer(factory=lambda: Recognizer())
    result = asyncio.run(engine.recognize(np.full((400, 2), 0.25), 100, threading.Event()))
    assert result.title == "Song"


def test_async_recognition_timeout_falls_back():
    class Recognizer:
        async def recognize(self, data):
            await asyncio.sleep(10)

    engine = ShazamRecognizer(factory=lambda: Recognizer(), timeout=0.03)
    with pytest.raises(TimeoutError):
        asyncio.run(engine.recognize(np.full((400, 2), 0.25), 100, threading.Event()))


def test_metadata_worker_applies_results_to_correct_tickets(qtbot):
    class Engine:
        async def recognize(self, pcm, rate, stop):
            return TrackMetadata("First" if pcm[0, 0] == 0.25 else "Second", "Artist")

    jobs = queue.Queue()
    first, second = (
        RecognitionTicket(TrackMetadata.recording()),
        RecognitionTicket(TrackMetadata.recording()),
    )
    jobs.put(RecognitionRequest(first, np.full((400, 2), 0.25), 100))
    jobs.put(RecognitionRequest(second, np.full((400, 2), 0.5), 100))
    jobs.put(None)
    worker = MetadataWorker(threading.Event(), jobs, engine=Engine())
    worker.execute()
    assert first.seal(0).title == "First" and second.seal(0).title == "Second"


def test_unmatched_live_recording_is_finalized_with_timestamp(tmp_path, qtbot):
    from velvetstream.recognition import CompletedRecording

    fallback = TrackMetadata.recording(datetime(2026, 10, 9, 22, 33, 44))
    sink = FlacSink(tmp_path, 48000, 2, fallback)
    sink.write(np.full((100, 2), 0.25))
    ticket = RecognitionTicket(fallback)
    ticket.resolve(None)
    jobs = queue.Queue()
    jobs.put(CompletedRecording(sink, ticket))
    jobs.put(None)
    result = FinalizeWorker(threading.Event(), jobs).execute()
    target = tmp_path / "Recording_20261009_223344.flac"
    assert result == {"saved": 1, "failed": 0}
    assert FLAC(target)["title"] == ["Recording_20261009_223344"]


def test_recognized_recording_embeds_all_shazam_tags_and_art(tmp_path, qtbot, monkeypatch):
    from PIL import Image

    import velvetstream.workers as workers
    from velvetstream.recognition import CompletedRecording

    image = BytesIO()
    Image.new("RGB", (64, 64), "cyan").save(image, "PNG")
    monkeypatch.setattr(workers, "download_art", lambda url, stop: image.getvalue())
    fallback = TrackMetadata.recording()
    sink = FlacSink(tmp_path, 48000, 2, fallback)
    sink.write(np.full((100, 2), 0.25))
    ticket = RecognitionTicket(fallback)
    ticket.resolve(metadata_from_shazam(response()))
    jobs = queue.Queue()
    jobs.put(CompletedRecording(sink, ticket))
    jobs.put(None)
    assert FinalizeWorker(threading.Event(), jobs).execute() == {"saved": 1, "failed": 0}
    track = FLAC(tmp_path / "Singer - Song.flac")
    assert track["title"] == ["Song"] and track["artist"] == ["Singer"]
    assert track["album"] == ["Record"] and track["date"] == ["2024"]
    assert track.pictures[0].width == 64


def test_full_recognition_queue_never_blocks_capture_or_loses_samples(tmp_path):
    from velvetstream.workers import CaptureWorker

    jobs, requests = queue.Queue(), queue.Queue(maxsize=1)
    requests.put(object())
    capture = CaptureWorker(threading.Event(), "id", tmp_path, 48000, -45, 1.0, jobs, requests)
    capture.begin_segment()
    capture.write_segment(np.full((4 * 48000, 2), 0.25))
    assert capture.submitted and capture.sample is None and capture.ticket.done.is_set()
    capture.end_segment()
    item = jobs.get_nowait()
    assert item.ticket.seal(0).recording_filename.startswith("Recording_")
    assert sf.info(item.sink.path).frames == 4 * 48000
