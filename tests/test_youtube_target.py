import pytest

from velvetstream.core import youtube_target


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=yTCdrfyzUfg&list=RDyTCdrfyzUfg&start_radio=1",
        "https://youtu.be/yTCdrfyzUfg?list=old&index=2",
        "https://music.youtube.com/watch?v=yTCdrfyzUfg&list=old",
        "https://www.youtube.com/shorts/yTCdrfyzUfg?list=old",
    ],
)
def test_video_links_never_expand_attached_playlists(url):
    assert youtube_target(url) == ("https://www.youtube.com/watch?v=yTCdrfyzUfg", False)


def test_explicit_playlist_still_allows_batch():
    assert youtube_target("https://youtube.com/playlist?list=PLtest") == (
        "https://www.youtube.com/playlist?list=PLtest",
        True,
    )


def test_non_media_link_rejected():
    with pytest.raises(ValueError):
        youtube_target("https://youtube.com/@channel")


def test_single_video_and_resume_never_download_other_entries(tmp_path, monkeypatch):
    import threading
    from pathlib import Path

    import numpy as np
    import soundfile as sf

    import velvetstream.services as services

    calls = []

    def extract(url, template, directory, stop, log, progress, *, playlist=False, skip=None):
        calls.append((url, playlist))
        assert url == "https://www.youtube.com/watch?v=yTCdrfyzUfg"
        info = {"id": "yTCdrfyzUfg", "title": "Selected", "channel": "Artist"}
        source = Path(directory) / "source.wav"
        if not playlist:
            sf.write(source, np.full((441, 2), 0.25), 44100, subtype="PCM_24")
        return {"info": info, "filename": str(source)}

    monkeypatch.setattr(services, "extract_youtube", extract)
    worker = services.YouTubeDownloader(
        threading.Event(), lambda _: None, lambda _: None, lambda _: None
    )
    url = "https://www.youtube.com/watch?v=yTCdrfyzUfg&list=RDyTCdrfyzUfg&start_radio=1"
    assert worker.run(url, tmp_path) == (1, 0)
    assert worker.run(url, tmp_path) == (1, 0)
    assert [metadata_only for _, metadata_only in calls] == [True, False, True]
    assert len(list(tmp_path.rglob("*.flac"))) == 1


def test_single_video_rejects_unexpected_playlist(tmp_path, monkeypatch):
    import threading

    import velvetstream.services as services

    monkeypatch.setattr(
        services,
        "extract_youtube",
        lambda *a, **k: {"info": {"_type": "playlist", "entries": [{"id": "previous"}]}},
    )
    worker = services.YouTubeDownloader(
        threading.Event(), lambda _: None, lambda _: None, lambda _: None
    )
    with pytest.raises(RuntimeError, match="unexpected playlist"):
        worker.run("https://youtu.be/yTCdrfyzUfg?list=old", tmp_path)
    assert not list(tmp_path.rglob("*.flac"))
