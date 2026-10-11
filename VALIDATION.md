# VelvetStream validation

## Selected-video extraction fix (2026-10-10)

- Full suite: 90 tests passed in 13.62 seconds; Ruff and compileall clean.
- Reported watch URL with `list=RD...&start_radio=1` resolves through the real
  supervised yt-dlp process to video `yTCdrfyzUfg`, with no playlist entries.
- Regression tests cover watch, shortened, music and Shorts URLs; explicit playlist
  support; unexpected playlist refusal; real FLAC conversion and retry of a saved
  selected video without fetching other entries.
- Updated executable: `dist/single-video/VelvetStream/VelvetStream.exe`.
- Frozen executable local diagnostics passed: bundled tools, cancellation (0.25s,
  no remaining child processes), PCM_24 FLAC playback/seek/pause/stop, and compact
  window layout. Report: `docs/previews/exe-single-video-diagnostics.json`.
- Desktop testing shortcut verified to target the updated executable.

Observed on Windows with Python 3.11.15. Original source-delivery checks and
subsequent Windows x64 executable checks are recorded below; they do not establish
production readiness across all three platforms.

## Checks completed

- `python -m pytest -q`: **70 passed in 3.91 seconds** in the final rerun.
- `python -m ruff check .`: clean after import formatting.
- `python -m compileall -q velvetstream velvetstream.py VelvetStreamStandalone.py`: successful.
- `python -m pip check`: no broken requirements.
- Real SoundFile FLAC encode/decode, Mutagen text tags and embedded picture tests.
- Real FFmpeg conversion in a playlist pipeline with simulated downloader responses;
  successful entries survived an unavailable entry, with no overwrite on collisions.
- Simulated capture-to-recognition-to-finalization pipeline preserves recorded samples,
  associates separate recognition results with their own files, and writes final tags.
- Exact four-second PCM collection, bounded queues, recognition timeout, sealed late
  results, timestamp fallback, finalization failure recovery and worker shutdown tests.
- Qt tests cover mode/theme transitions, legacy OAuth-settings removal, configuration
  locking, queued thread results, cancellation, close lifecycle and small-window layout.
- Complete generated standalone script imports and displays both source modes and themes.
- Logo is embedded in the native header and multi-size window icon, including standalone.
- Manual editor remains usable while capture controls are locked; actual copied FLAC is
  retagged, decoded and safely renamed without overwriting a collision or changing samples.
- A slow lookup test ran three real QThreads: the capture producer wrote all **360 blocks**
  while recognition waited, with over 100 reads and over 10 GUI heartbeat ticks during
  the lookup. This demonstrates thread isolation in that synthetic scenario, not a
  guarantee against OS, device, disk, or CPU stalls on every machine.
- The local Windows Chromaprint 1.6.1 executable was downloaded from its official release
  and checked against SHA256 `735d6182b38e9f364b84ce6f4ccd682c75e2851de89735711d6b762d12b92a4e`.
  A generated 30-second tone produced a valid fingerprint in approximately **0.16 seconds**.
- A live AcoustID lookup rejected the supplied application key with **error code 4,
  invalid API key**. This prevents claiming a verified live AcoustID music match.
  The code disables further invalid-key attempts and supports Shazam/manual fallback.
- Live MusicBrainz retrieval yielded title/artist/official-release data; its merged-ID
  redirect and Cover Art Archive metadata redirect exposed issues that were fixed and
  covered by tests. A follow-up live lookup yielded a front-cover URL successfully.
- AcoustID's public API uses whole-recording duration. The implementation generates
  Chromaprint at 30 seconds and waits for the detected end before querying with actual
  recorded duration; a test verifies a 240-second recording is submitted as 240, not 30.
- Native Windows UI launched, discovered **nine WASAPI loopback endpoints**, and rendered
  a 43-pixel-high device selector. No personal playback was captured in this check.
- Live Shazamio 0.8.1 request using a generated stereo four-second 440 Hz tone returned
  no match in approximately **0.42 seconds**. This exercises encoded in-memory audio and
  the external recognition service, but does not establish music-recognition accuracy.
- Actual-widget previews are in `docs/previews/`; native Windows screenshot is
  `docs/previews/windows-native.png`.

## Remaining release checks

- Capture a known track from an actual playback application on each platform; verify
  routing, sample-rate compatibility, permissions, silence splitting and saved playback.
- Identify real musical intros through Shazam and validate their returned metadata/art;
  configure a valid AcoustID application key and exercise a real fingerprint match.
- Exercise current public YouTube video and playlist downloads through actual yt-dlp
  network requests and a supported EJS JavaScript runtime.
- Test sustained capture, device removal, disk exhaustion and native-driver shutdown
  under target-platform conditions.
- Build and test platform-specific installers/signatures if distributing binaries.

Shazamio is an unofficial client; recognition availability and accuracy depend on the
service and the four-second sample. Timestamp fallback is a supported normal outcome.
FLAC preserves the decoded/captured signal, but cannot restore details lost in a lossy
source. The setup guide describes platform dependencies and recovery-file behavior.

## Credential follow-up — 2026-10-10

The user supplied an updated application key. It was imported into the ignored
local `.env` file without executing the supplied file or exposing the key.
Effective application configuration matches the updated value. A live AcoustID
track-ID lookup returned `status: ok` and one result; a real lookup using a
Chromaprint fingerprint from a generated 30-second tone returned `status: ok`
and zero matches. Credentials are now accepted and fingerprint requests work.
A real musical fingerprint match remains unverified. Start a new capture session
to create a metadata worker with the updated credentials.

## Recovery, player and Windows x64 build — 2026-10-10

- Final suite: **80 tests passed in 7.43 seconds**. Ruff, compileall and pip check
  passed. Generated standalone was refreshed and imports with the new player.
- Real blocked child-process tests verify Stop without progress hooks, timeout,
  Skip without stopping the playlist, and Windows job termination of a spawned
  grandchild. Qt tests verify GUI heartbeat, return to Idle and starting again.
- Real FLAC/FFmpeg/tagging tests verify successful entries survive failure, retry
  skips completed files without modifying their bytes, and earlier-version FLAC
  source tags bootstrap resume before a journal exists.
- The supplied YouTube radio URL resolved through the isolated extractor to a
  finite **200-entry** batch. The original lazy network iterator is no longer
  evaluated in the supervising QThread.
- A live source-app download of video `yTCdrfyzUfg` completed: **1 saved, 0 failed**,
  275.78 seconds, 44.1 kHz stereo PCM_24 FLAC, Title/Artist/Year/source tags and one
  embedded cover. Source codec AAC (`mp4a.40.2`), approximately 129.494 kb/s. Album
  metadata was absent from the source and was not fabricated. The test output was
  private temporary data and was removed after verification.
- The Qt Multimedia player decoded a real three-second stereo 24-bit FLAC;
  play/pause/stop and one-second seeking passed. Tests verify no automatic media
  loading/playback on completion and playback is disabled during live capture.
- One Qt process abort occurred during an intermediate test run. Completed files
  are now added to the selector without automatically starting Qt decoder work.
  Subsequent targeted and complete suites passed. This is not proof of causation;
  sustained real-world playback remains a release check.
- PyInstaller **6.22.3** produced a windowed executable with PE machine **0x8664**
  (Windows x64), GUI subsystem 2. FFmpeg, ffprobe, Node.js and Chromaprint are
  bundled; private application config stays in an external `.env` file.
- Initial frozen startup failed because PyInstaller collected Poppler's ICU 78
  DLL from PATH, while Qt expects Windows' native unversioned ICU symbols. The
  reproducible build now omits third-party ICU DLLs and uses the OS library.
- Packaged-app diagnostics verify bundled executable tool paths, nine loopback
  devices, no-hook cancellation without remaining child processes, real FLAC
  encoding, and muted playback controls. See `docs/previews/exe-final-diagnostics.json`
  and its accompanying native screenshot for the final-build report.
- Automatic approval review rejected the frozen EXE's optional live YouTube
  diagnostic command with only "blocked by policy". No workaround was used for
  that command. Source-app live downloading and frozen local diagnostics passed;
  a live YouTube download from the frozen GUI remains a user testing step.

The executable is an unsigned local testing folder build. Keep its `_internal`
directory beside the EXE. Windows capture from actual playback, long-session
testing, macOS/Linux runtime verification and distribution signing remain open.

## Compact velvet-purple interface — 2026-10-10

- Complete suite: **82 passed in 7.59 seconds**. Both themes render the primary
  button in `#3B1E54`; every enabled button and selected source tab has white text.
- Both source modes fit at **820 × 700** with session actions, the FLAC player and
  activity summary within the visible central widget. There is no outer page
  scroll area. The activity console can expand and scroll internally.
- Cards use smaller content margins; source panels size to the active mode.
  Compact windows hide extra help/header lines and initially collapse silence
  settings, which can be reopened with the explicit control.
- Actual-widget compact previews for live/YouTube modes and both themes are in
  `docs/previews/*-compact.png`. The rebuilt x64 app is side-by-side in
  `dist/purple/VelvetStream/VelvetStream.exe`, allowing the old app to remain open.
- Final packaged compact-mode checks are recorded in
  `docs/previews/exe-purple-diagnostics.json` with a native screenshot alongside it.
