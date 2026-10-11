import queue
import threading

import numpy as np
from mutagen.flac import FLAC

from velvetstream.core import FlacSink, TrackMetadata
from velvetstream.workers import CaptureWorker, FinalizeWorker, Worker


def test_worker_emits_error_and_finished_on_failure(qtbot):
    class Broken(Worker):
        def execute(self):
            raise RuntimeError("disk unavailable")

    worker = Broken(threading.Event())
    errors, done = [], []
    worker.error.connect(errors.append)
    worker.finished.connect(lambda: done.append(True))
    worker.run()
    assert errors == ["disk unavailable"] and done == [True]


def test_finalize_failure_does_not_prevent_later_tracks(qtbot, tmp_path):
    class BrokenSink:
        path = tmp_path / "recoverable.flac"
        metadata = TrackMetadata("Bad", "Singer")

        def finish(self, art=None):
            raise OSError("disk failure")

    good = FlacSink(tmp_path, 48000, 2, TrackMetadata("Good", "Singer"))
    good.write(np.full((100, 2), 0.25, dtype=np.float32))
    jobs = queue.Queue()
    jobs.put(BrokenSink())
    jobs.put(good)
    jobs.put(None)
    worker = FinalizeWorker(threading.Event(), jobs)
    results, errors = [], []
    worker.result.connect(results.append)
    worker.error.connect(errors.append)
    worker.run()
    assert results == [{"saved": 1, "failed": 1}]
    assert len(errors) == 1
    assert FLAC(tmp_path / "Singer - Good.flac")["title"] == ["Good"]


def test_capture_queue_pressure_preserves_completed_audio(tmp_path):
    jobs = queue.Queue(maxsize=1)
    jobs.put(object())
    worker = CaptureWorker(threading.Event(), "id", tmp_path, 48000, -45, 1.0, jobs, queue.Queue())
    worker.begin_segment()
    worker.sink.write(np.full((100, 2), 0.25, dtype=np.float32))
    try:
        worker.end_segment()
    except RuntimeError:
        pass
    else:
        assert False, "Capture must stop instead of silently dropping completed tracks"
    assert worker.sink.path.is_file() and worker.sink.stream.closed


def test_capture_cleanup_sends_sentinel_even_if_encoder_close_fails(tmp_path):
    import pytest

    class BrokenSink:
        path = tmp_path / "recoverable.flac"

        def close(self):
            raise OSError("disk full during encoder close")

    stop = threading.Event()
    stop.set()
    jobs = queue.Queue()
    worker = CaptureWorker(stop, "id", tmp_path, 48000, -45, 1.0, jobs, queue.Queue())
    worker.sink = BrokenSink()
    with pytest.raises(OSError):
        worker.execute()
    assert jobs.get_nowait() is None
