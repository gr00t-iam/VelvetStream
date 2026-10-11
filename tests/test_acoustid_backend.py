import asyncio
import threading
from uuid import UUID

import numpy as np
import pytest

from velvetstream.core import TrackMetadata
from velvetstream.fallback import (
    AcoustIDRecognizer,
    cover_url_from_archive,
    metadata_from_musicbrainz,
    recording_id_from_acoustid,
)
from velvetstream.recognition import RecognitionTicket

RECORDING = "38035858-f990-4fbb-b3b2-f2f8b958eeba"
RELEASE = "0a6f25dc-957a-41c1-a9b0-39427076df15"


def official():
    return {
        "title": "Official Song",
        "artist-credit": [{"name": "Artist", "joinphrase": " & "}, {"name": "Guest"}],
        "releases": [{"id": RELEASE, "title": "Album", "status": "Official", "date": "2022-01-01"}],
    }


def test_acoustid_rejects_low_scores_and_ambiguous_recording_ids():
    def answer(score, extra=None):
        data = {"status": "ok", "results": [{"score": score, "recordings": [{"id": RECORDING}]}]}
        if extra:
            data["results"].append(extra)
        return data

    assert recording_id_from_acoustid(answer(0.9)) == RECORDING
    assert recording_id_from_acoustid(answer(0.6)) is None
    assert (
        recording_id_from_acoustid(answer(0.9, {"score": 0.89, "recordings": [{"id": RELEASE}]}))
        is None
    )
    with pytest.raises(ValueError):
        recording_id_from_acoustid({"status": "error"})


def test_musicbrainz_tags_preserve_artist_credit_and_official_release():
    metadata, release = metadata_from_musicbrainz(official(), RECORDING)
    assert metadata.artist == "Artist & Guest"
    assert metadata.album == "Album" and metadata.year == "2022"
    assert str(UUID(release)) == RELEASE
    assert metadata_from_musicbrainz({"title": "No artist"}, RECORDING) == (None, "")


def test_cover_url_upgrades_archive_to_https_and_rejects_other_hosts():
    assert (
        cover_url_from_archive(
            {"images": [{"front": True, "image": "http://archive.org/download/a/cover.jpg"}]}
        )
        == "https://archive.org/download/a/cover.jpg"
    )
    assert not cover_url_from_archive(
        {"images": [{"front": True, "image": "https://other.test/a.jpg"}]}
    )


def test_real_backend_waits_for_total_duration_then_queries_over_https(monkeypatch):
    import velvetstream.fallback as backend

    queried = []
    monkeypatch.setattr(backend, "fingerprint_window", lambda *args: (30, "fingerprint"))

    def lookup(key, fingerprint, duration, **options):
        queried.append(duration)
        assert backend.acoustid.API_BASE_URL == "https://api.acoustid.org/v2/"
        return {"status": "ok", "results": [{"score": 0.95, "recordings": [{"id": RECORDING}]}]}

    monkeypatch.setattr(backend.acoustid, "lookup", lookup)
    engine = AcoustIDRecognizer(key="test-application", command="fpcalc")

    async def json_response(url, params=None):
        return (
            official()
            if "musicbrainz.org" in url
            else {"images": [{"front": True, "image": "https://archive.org/download/a/cover.jpg"}]}
        )

    monkeypatch.setattr(engine, "_json", json_response)
    ticket = RecognitionTicket(TrackMetadata.recording())

    async def probe():
        task = asyncio.create_task(
            engine.recognize((np.ones((300, 2)),), 10, threading.Event(), ticket=ticket)
        )
        await asyncio.sleep(0.08)
        assert not queried
        ticket.end_audio(240)
        result = await asyncio.wait_for(task, 1)
        assert result.title == "Official Song" and result.art_url.startswith("https://archive.org/")

    asyncio.run(probe())
    assert queried == [240]


def test_invalid_key_disables_further_acoustid_attempts(monkeypatch):
    import velvetstream.fallback as backend

    monkeypatch.setattr(backend, "fingerprint_window", lambda *args: (30, "fingerprint"))
    monkeypatch.setattr(
        backend.acoustid,
        "lookup",
        lambda *args, **kwargs: {"status": "error", "error": {"code": 4}},
    )
    engine = AcoustIDRecognizer(key="invalid", command="fpcalc")
    ticket = RecognitionTicket(TrackMetadata.recording())
    ticket.end_audio(240)
    with pytest.raises(ValueError):
        asyncio.run(engine.recognize((np.ones((300, 2)),), 10, threading.Event(), ticket=ticket))
    assert not engine.available and "invalid" in engine.unavailable_reason


@pytest.mark.parametrize(
    "origin, location",
    [
        (
            "https://coverartarchive.org/release/123",
            "https://archive.org/download/release/index.json",
        ),
        (
            "https://musicbrainz.org/ws/2/recording/123",
            "https://musicbrainz.org/ws/2/recording/456",
        ),
    ],
)
def test_cover_archive_json_redirect_and_chunked_body(monkeypatch, origin, location):
    import velvetstream.fallback as backend

    class Content:
        async def iter_chunked(self, size):
            yield b'{"images":'
            yield b" []}"

    class Response:
        def __init__(self, redirect):
            self.status = 307 if redirect else 200
            self.headers = {"Location": location}
            self.content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def raise_for_status(self):
            pass

    visited = []

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def get(self, url, **kwargs):
            visited.append(url)
            return Response(len(visited) == 1)

    monkeypatch.setattr(backend.aiohttp, "ClientSession", Session)
    engine = AcoustIDRecognizer(key="", command="fpcalc")
    assert asyncio.run(engine._json(origin)) == {"images": []}
    assert len(visited) == 2
