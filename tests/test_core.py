from io import BytesIO

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC
from PIL import Image

from velvetstream.core import (
    FlacSink,
    Segmenter,
    TrackMetadata,
    metadata_from_youtube,
    safe_component,
    validate_youtube_url,
)


def segmenter():
    tracks = []
    current = []

    def start():
        current.clear()

    def data(block):
        current.append(block.copy())

    def end():
        tracks.append(np.concatenate(current))

    return Segmenter(100, -40, 0.2, start, data, end, pre_roll=0.05), tracks


def test_short_silence_preserves_samples_and_long_silence_splits():
    s, tracks = segmenter()
    s.feed(np.full((10, 2), 0.3))
    s.feed(np.zeros((10, 2)))
    s.feed(np.full((10, 2), 0.4))
    s.feed(np.zeros((20, 2)))
    s.feed(np.full((10, 2), 0.5))
    s.finish()
    assert [len(t) for t in tracks] == [30, 15]
    assert np.array_equal(tracks[0][10:20], np.zeros((10, 2)))
    assert np.all(tracks[1][-10:] == 0.5)


def test_leading_silence_is_bounded_and_silence_alone_creates_no_track():
    s, tracks = segmenter()
    for _ in range(100):
        s.feed(np.zeros((10, 2)))
    s.finish()
    assert tracks == []
    s.feed(np.full((10, 2), 0.2))
    s.finish()
    assert len(tracks[0]) <= 15


def test_stop_preserves_short_trailing_silence():
    s, tracks = segmenter()
    s.feed(np.full((10, 2), 0.3))
    s.feed(np.zeros((10, 2)))
    s.finish()
    assert len(tracks[0]) == 20


def test_invalid_samples_rejected():
    s, _ = segmenter()
    with pytest.raises(ValueError):
        s.feed(np.array([[float("nan"), 0.0]]))


@pytest.mark.parametrize("value", ["../A/B", "CON", "NUL.txt", "A:*?<>|", " . ", "a" * 500])
def test_filename_components_are_portable(value):
    result = safe_component(value)
    assert result and len(result.encode("utf-8")) <= 100
    assert not any(c in result for c in "/\\:*?<>|")
    assert result.rstrip(" .") == result
    assert result.split(".")[0].upper() not in {"CON", "NUL"}


def test_flac_round_trip_tags_art_and_collision_preserve_basename(tmp_path):
    art = BytesIO()
    Image.new("RGB", (32, 24), "purple").save(art, "PNG")
    track = TrackMetadata(title="Song", artist="Artist", album="Album", year="2026")
    audio = np.array([[0.25, -0.25], [0.5, -0.5]] * 200, dtype=np.float32)
    paths = []
    for _ in range(2):
        sink = FlacSink(tmp_path, 48000, 2, track)
        sink.write(audio)
        paths.append(sink.finish(art.getvalue()))
    assert paths[0].name == paths[1].name == "Artist - Song.flac"
    assert paths[0].parent != paths[1].parent
    restored, rate = sf.read(paths[0], dtype="float32")
    assert rate == 48000 and np.array_equal(restored, audio)
    tags = FLAC(paths[0])
    assert tags["title"] == ["Song"]
    assert tags["artist"] == ["Artist"]
    assert tags["album"] == ["Album"]
    assert tags["date"] == ["2026"]
    assert tags.pictures[0].width == 32 and tags.pictures[0].height == 24
    assert not list(tmp_path.glob(".velvetstream-*"))


@pytest.mark.parametrize(
    "url",
    [
        "http://youtube.com/watch?v=x",
        "https://youtube.com.evil.test/watch?v=x",
        "file:///x",
        "https://user:pass@youtube.com/watch?v=x",
        "https://youtu.be:443/x",
        "",
    ],
)
def test_url_validation_rejects_invalid_origins(url):
    with pytest.raises(ValueError):
        validate_youtube_url(url)


def test_youtube_metadata_fallback_and_track_tags():
    m = metadata_from_youtube(
        {"title": "Video", "channel": "Channel", "upload_date": "20260905", "id": "x"}
    )
    assert (m.artist, m.title, m.year) == ("Channel", "Video", "2026")
    m = metadata_from_youtube(
        {
            "title": "Video",
            "channel": "Channel",
            "artist": "Singer",
            "track": "Song",
            "album": "Record",
        }
    )
    assert (m.artist, m.title, m.album) == ("Singer", "Song", "Record")
