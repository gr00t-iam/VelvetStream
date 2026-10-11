"""Read the reported radio URL using the supervised extraction boundary."""

import multiprocessing
import sys
import tempfile
import threading
from pathlib import Path


def main():
    from velvetstream.download_tasks import extract_youtube

    stop = threading.Event()
    with tempfile.TemporaryDirectory() as directory:
        result = extract_youtube(
            "https://www.youtube.com/watch?v=yTCdrfyzUfg&list=RDyTCdrfyzUfg&start_radio=1",
            str(Path(directory) / "unused.%(ext)s"),
            directory,
            stop,
            print,
            lambda value: None,
            playlist=True,
        )
        info = result["info"]
        print("Playlist type:", info.get("_type"))
        print("Entries:", len(info.get("entries") or []))
        print("Selected video:", info.get("id"))
        assert info.get("id") == "yTCdrfyzUfg" and info.get("entries") is None
        if "--download" in sys.argv:
            import soundfile as sf
            from mutagen.flac import FLAC

            from velvetstream.services import YouTubeDownloader

            saved, failed = YouTubeDownloader(stop, print, print, lambda value: None).run(
                "https://www.youtube.com/watch?v=yTCdrfyzUfg", Path(directory)
            )
            print("Actual download:", {"saved": saved, "failed": failed})
            if saved != 1 or failed:
                raise RuntimeError("Video download did not complete")
            file = next(Path(directory).glob("*.flac"))
            audio, tags = sf.info(file), FLAC(file)
            print(
                "FLAC:", audio.samplerate, audio.channels, audio.subtype, round(audio.duration, 2)
            )
            print("Tags:", sorted(tags.keys()), "cover count:", len(tags.pictures))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
