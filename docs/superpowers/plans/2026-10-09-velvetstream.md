> Historical implementation plan. The final user-directed Shazamio pivot supersedes all PKCE/Spotipy tasks below. Delivered architecture and observed checks are in README.md and VALIDATION.md.

# VelvetStream Implementation Plan

**Goal:** Deliver the complete PySide6 application and cross-platform setup matrix requested by the user.

**Architecture:** Separate Qt UI and workers from a testable streaming audio segmenter, safe output/tagging service, PKCE service, and YouTube backend. QObject workers communicate with widgets only through queued signals. Capture, metadata, authentication, device enumeration, and downloads use QThreads.

**Tech stack:** Python 3.11+, PySide6, numpy, SoundCard, SoundFile, Spotipy, yt-dlp[default], Mutagen, Pillow, Requests, ffmpeg-python, system FFmpeg.

**Spec:** The user's second message supersedes the initial design: PySide6 required, charcoal/cyan dark and accessible light themes, OAuth PKCE without client secret. Proceed inline under the explicit instruction to write the complete application.

## Constraints and review focus

- Final FLAC basename is Artist - Track Name.flac; collisions go into numbered directories.
- PCM_24 output; no normalization or artificial upsampling; accurately describe lossy source limitations.
- No microphone fallback; macOS requires explicit virtual loopback routing.
- Bounded RAM, cancellation, safe shutdown, verified atomic publication, no secrets in settings or logs.
- Cover late/stale metadata, quiet passages, mixed audio, disk/codec failure, malformed URLs/OAuth callbacks, and playlist partial failure.

## Task 1 — Audio and output services

Files: velvetstream/core.py, tests/test_core.py.
Interfaces: TrackMetadata, Segmenter.feed/finish, FlacSink.write/finish, validate_youtube_url, metadata_from_youtube.

- [ ] Write and run failing tests for short/long silence, pre-roll, stop flush, filename collisions, URL validation, and real FLAC tags/artwork.
- [ ] Implement bounded segmentation and safe tagged publication.
- [ ] Run core tests and a real FLAC decode comparison.

## Task 2 — Backends and Qt workers

Files: velvetstream/services.py, velvetstream/workers.py, tests/test_services.py.
Interfaces: SpotifySession, authenticate_pkce, MetadataStore, CaptureWorker, MetadataWorker, YouTubeWorker, DeviceWorker.

- [ ] Write failing tests for OAuth state validation, PKCE challenge, metadata freshness, loopback-only enumeration, bestaudio selection, and cancellation/conversion.
- [ ] Implement loopback callback authentication, memory-only refresh tokens, independent metadata polling, streaming capture and managed FFmpeg conversion.
- [ ] Run backend tests including actual FFmpeg conversion.

## Task 3 — GUI and lifecycle

Files: velvetstream/ui.py, velvetstream.py, tests/test_ui.py.

- [ ] Write failing offscreen tests for dynamic panels, theme changes, busy locking, and shutdown/cancellation.
- [ ] Implement rounded QSS themes, fade transitions, custom painted VU meter, sliders, settings, bounded logging, and retained worker/thread lifetimes.
- [ ] Run offscreen GUI tests and inspect rendered screenshots.

## Task 4 — Setup and release evidence

Files: README.md, requirements.txt, requirements-dev.txt, VALIDATION.md.

- [ ] Provide install matrix including OS drivers, FFmpeg, Deno/EJS, and Spotify loopback redirect.
- [ ] Run full tests, lint, byte compilation, dependency checks; record platform/live test limitations.
- [ ] Self-review cancellation, capture failure recovery, output integrity, secrets, and thread cleanup; fix verified defects.
