import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
import soundfile as sf

from velvetstream.services import (
    Cancelled,
    convert_to_flac,
    loopback_devices,
    resolve_loopback,
    youtube_options,
)


def test_devices_never_fall_back_to_physical_microphone():
    class Device:
        def __init__(self, id, name, loopback=False):
            self.id, self.name, self.isloopback = id, name, loopback

    class Backend:
        def all_microphones(self, **kwargs):
            return [
                Device("mic", "Microphone"),
                Device("out", "Speakers monitor", True),
                Device("bh", "BlackHole 2ch"),
            ]

    assert [d.id for d in loopback_devices(Backend(), "win32")] == ["out"]
    assert [d.id for d in loopback_devices(Backend(), "darwin")] == ["bh"]


def test_download_cancel_hook_and_bitrate_selection():
    stop = threading.Event()
    options = youtube_options("source.%(ext)s", stop, lambda *a: None, lambda *a: None)
    assert options["format"] == "bestaudio/best"
    assert options["format_sort"][:1] == ["abr"]
    stop.set()
    with pytest.raises(Cancelled):
        options["progress_hooks"][0]({"status": "downloading"})


def test_real_ffmpeg_conversion_preserves_rate_channels_and_samples(tmp_path):
    source, dest = tmp_path / "input.wav", tmp_path / "output.flac"
    audio = np.array([[0.25, -0.5], [0.5, -0.25]] * 200, dtype=np.float32)
    sf.write(source, audio, 44100, subtype="PCM_24")
    convert_to_flac(source, dest, threading.Event())
    restored, rate = sf.read(dest, dtype="float32")
    assert rate == 44100 and np.array_equal(restored, audio)
    assert sf.info(dest).subtype == "PCM_24"


def test_conversion_cancelled_before_spawn_creates_no_output(tmp_path):
    stop = threading.Event()
    stop.set()
    with pytest.raises(Cancelled):
        convert_to_flac(tmp_path / "in.wav", tmp_path / "out.flac", stop)
    assert not (tmp_path / "out.flac").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="WASAPI COM initialization")
def test_device_discovery_works_in_a_second_native_thread():
    loopback_devices()  # initializes SoundCard on this thread first
    with ThreadPoolExecutor(max_workers=1) as executor:
        devices = executor.submit(loopback_devices).result(timeout=10)
    assert isinstance(devices, list)


def test_macos_loopback_resolution_preserves_native_integer_id():
    device = type("Device", (), {"id": 42, "name": "BlackHole 2ch", "isloopback": False})()
    backend = type("Backend", (), {"all_microphones": lambda self, **kwargs: [device]})()
    assert resolve_loopback("42", backend, "darwin") is device
    with pytest.raises(RuntimeError):
        resolve_loopback("missing", backend, "darwin")
