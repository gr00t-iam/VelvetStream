> Historical design. Current deliverable uses Live Capture with Shazamio and no developer/API fields; see README.md.

# VelvetStream design

Date: 2026-10-09
Status: Historical initial proposal, superseded by the user's second brief.
The implemented application uses cyan accents, dark/light themes, and PKCE
without a client secret. See README.md and the implementation plan for the
current architecture and VALIDATION.md for observed verification.

## Intended outcome

A desktop Python application for Windows, macOS, and Linux with a dark grey
and purple interface. A prominent mode selector switches between Spotify
system-output recording and YouTube video/playlist processing. Both modes
produce tagged FLAC files in a user-selected directory. Deliverables include
a complete object-oriented `velvetstream.py`, dependency manifests, setup
instructions, automated tests, and a record of verification actually performed.

## Architecture choices

Recommended: PySide6, SoundCard, SoundFile, Spotipy, yt-dlp, Mutagen, Pillow,
Requests, and system FFmpeg/ffprobe. PySide6 provides native Qt controls,
thread-safe signals, accessibility, and a consistent stylesheet across platforms.
SoundCard supplies WASAPI, PulseAudio, and CoreAudio access. SoundFile writes
24-bit FLAC incrementally. FFmpeg decodes downloaded audio without an additional
lossy encode. Pillow converts downloaded artwork to validated JPEG/PNG images.

Alternatives: CustomTkinter reduces GUI dependency size but requires more custom
work for meters, accessibility, and worker integration. Native audio APIs give
more control over device formats but substantially increase platform-specific
code and validation requirements. The recommended architecture balances the
requested complete script with maintainable service boundaries.

## GUI and state

- Charcoal background, raised grey panels, soft purple accents, rounded controls,
  readable contrast, and a resizable layout.
- Spotify and YouTube mode buttons select a stacked input panel.
- Spotify panel: client ID, masked client secret, authentication control,
  input/loopback device picker and refresh, sample-rate selector, silence
  threshold in dBFS, and silence duration in seconds.
- YouTube panel: video or playlist URL input and download progress.
- Shared controls: output directory picker, status label, action/stop button,
  VU meter, and bounded timestamped log console.
- States: Idle, Authenticating, Recording, Downloading, Converting, Stopping,
  and Error. Mode and job configuration cannot change during an active job.
- Worker signals alone update widgets. Closing an active job initiates shutdown
  and waits asynchronously for worker cleanup before the window closes.
- Persist nonsecret preferences with QSettings; never persist the client secret
  or include credentials, tokens, or authorization URLs in logs.

## Spotify capture and metadata

Authenticate through user OAuth, with `user-read-currently-playing` scope and
an explicitly registered loopback redirect such as
`http://127.0.0.1:8765/callback`. Client credentials alone cannot obtain a user's
current playback. OAuth/browser work and metadata requests run off the GUI thread.
Use an in-memory token cache by default, requiring authentication per launch.

Enumerate capture devices in a worker. On Windows select a WASAPI loopback
endpoint. On Linux select a PulseAudio monitor, including PipeWire's PulseAudio
compatibility service. On macOS select a configured BlackHole virtual input;
document a Multi-Output Device for simultaneous monitoring. SoundCard does not
provide direct ALSA or native PipeWire capture in this design.

Read stereo float blocks at the selected capture rate, defaulting to 48 kHz.
Write PCM_24 FLAC incrementally; do not accumulate whole tracks in memory.
Calculate RMS dBFS for silence detection and peak dBFS for the display.
Maintain a bounded silence buffer: brief silence remains in a track, while
silence exceeding the configured duration closes the active track and waits
for the next audible block. Retain a small bounded pre-roll to avoid cutting
quiet onsets. Stop flushes the final audible segment.

Metadata polling runs independently of the capture loop so network stalls cannot
interrupt acquisition. Capture a metadata snapshot when a segment begins and
do not relabel it using a later track. Poll results have timestamps and track IDs;
stale, unavailable, advertisement, episode, and paused results are distinguished.
Silence remains the default split trigger. Gapless playback, crossfades, quiet
passages, remote Spotify playback, and other system sounds can prevent correct
track segmentation or tagging; report these limits rather than claiming sample
accurate alignment from a polled Web API.

Missing metadata produces an explicitly identified Unknown Artist recording
with a timestamped title and a warning. Artwork failures preserve audio and text
tags with a warning. Show clipping and capture failures in the log.

## YouTube processing

Validate an HTTPS YouTube/youtu.be URL. Use yt-dlp to enumerate playlist entries
and process each independently. Select its best available audio-only stream;
source codec/bitrate are logged where known. Bound retries and request timeouts.
Install yt-dlp's supported JavaScript runtime requirements in the setup guide.

Download into a per-job temporary directory under the output directory. Invoke
FFmpeg with an argument list through a managed subprocess or binding, preserving
sample rate and channels and encoding 24-bit FLAC without normalization or
artificial upsampling. Cancellation stops download hooks and terminates active
conversion safely. Playlist failures are counted and summarized while other
entries continue; cancellation stops the remaining entries.

Tags use explicit artist/track fields where present, otherwise channel/uploader
and video title. Include album when available, upload date/year, source URL,
video ID, and playlist position. Fetch the highest-resolution available thumbnail
with bounded size and timeout; validate and convert WebP/other image formats
to JPEG or PNG before adding a Mutagen FLAC picture block.

## Output integrity

Both modes share one tagging and filename service. Required final filename:
`Artist - Track Name.flac`, or `Channel - Video Title.flac`. Sanitize separators,
control characters, Windows reserved names, trailing dots/spaces, and excessive
length. Never overwrite an existing file. On collisions choose a numbered
subdirectory so the requested basename stays intact.

Write and tag a temporary FLAC, verify it is readable, then publish it atomically
on the same filesystem. Failed or incomplete files never appear as finished
FLACs. Capture errors preserve recoverable audio separately with explicit status.
Check output writability and available disk space before starting; surface disk
full and device-disconnect errors without claiming a successful job.

FLAC is lossless relative to the encoded PCM. Spotify and YouTube source audio
may already be lossy. A 24-bit container cannot restore source information;
capture quality also depends on the operating-system mixer and virtual routing.
System loopback captures the output mix, including unrelated application sounds.

## Verification and release claims

Automated tests cover silence boundaries, quiet onset buffering, exact sample
accounting, metadata snapshots, filename safety/collisions, FLAC tags/artwork,
playlist error accounting, cancellation, and GUI mode/action state transitions.
Use synthesized audio and mocked APIs for repeatable checks; additionally perform
an actual encode/decode/tag round trip with installed libraries and FFmpeg.

Run a Qt offscreen smoke test and inspect the GUI on Windows when available.
Document external credentials, source access, audio hardware, and macOS/Linux
machines needed for live tests. Automated tests and Windows smoke checks do not
establish production readiness across all three platforms. Report completed
checks separately from release gates still requiring live platform validation.

## Setup deliverable

Provide Python virtual-environment commands for PowerShell and POSIX shells,
`pip install` commands for all Python dependencies, platform FFmpeg installation,
Linux PulseAudio/PipeWire and libsndfile prerequisites, macOS BlackHole routing
and microphone permission, Spotify developer application/OAuth registration,
launch instructions, troubleshooting, and test commands.

## Primary references checked

- SoundCard: https://soundcard.readthedocs.io/en/stable/index.html
- SoundFile: https://python-soundfile.readthedocs.io/en/0.13.1/
- Spotipy: https://spotipy.readthedocs.io/en/master/
- Spotify redirect URIs: https://developer.spotify.com/documentation/web-api/concepts/redirect_uri
- yt-dlp: https://github.com/yt-dlp/yt-dlp
- BlackHole: https://github.com/ExistentialAudio/BlackHole

## Next step

After design approval, write the implementation plan for review and execution
selection, then build and verify the complete application and setup guide.
