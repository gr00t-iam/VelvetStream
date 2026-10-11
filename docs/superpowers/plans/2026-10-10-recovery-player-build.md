# VelvetStream recovery, playback and Windows build

The user needs to recover from a failed YouTube playlist entry without restarting,
audition completed FLAC files inside the application, and reopen a native x64 EXE.

## Design

- Keep GUI and recording QThreads. Isolate each yt-dlp extraction in a spawned
  process, supervised by the existing YouTube QThread. Stop or Skip kills the owned
  child process tree; no QThread termination and no GUI waits.
- Flat, bounded playlist enumeration happens in the child too. Treat YouTube
  radio mixes as finite batches. Timeout extraction and stalled transfers. Skip
  failed entries and report useful, sanitized errors.
- Persist completed URL/path pairs in a private output-folder journal using atomic
  replacement. Retry/resume ignores completed files that still exist, and retries
  failed entries. Completed FLAC publication remains transactional.
- Add Qt Multimedia player with a recent-completions selector, Open FLAC,
  play/pause, Stop, seek/time display and independent playback volume. Never
  autoplay completed files. Prevent playback through the capture output during
  live recording because loopback would record it again.
- Package with PyInstaller for Windows x64, include the approved icon, FFmpeg,
  ffprobe, Chromaprint and JavaScript runtime. Keep the local AcoustID key in a
  separate configuration file, excluded from distributable source.

## Verification

1. Reproduce no-hook cancellation, timeout and skip with real blocked children.
2. Test failed entries, retry/resume, preservation and restart through Qt workers.
3. Decode a real FLAC through QMediaPlayer and exercise player controls.
4. Run the complete suite, Ruff, standalone regeneration, and native GUI smoke.
5. Build x64 executable, check PE architecture, launch it and test bundled
   extraction/FLAC playback without relying on the development interpreter.

This is implementation work authorized by the user's explicit request. No
additional publication, installation or release is part of this work.
