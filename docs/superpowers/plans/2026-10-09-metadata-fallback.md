# Metadata fallback implementation plan

User-authorized continuation: complete the requested sequential metadata chain,
manual tagging and integration of the approved flat vector logo.

- [x] Test per-recording 4/30-second windows, Shazam priority, AcoustID fallback,
  lookup failure, short-track handling, cancellation and concurrent capture.
- [x] Add bounded sample mailboxes and direct Qt signal/slot handoff; all slots
  on that handoff only publish data, never perform network or encoding work.
- [x] Add cancellable Chromaprint fingerprinting, pyacoustid lookup, rate-limited
  MusicBrainz retrieval and optional Cover Art Archive artwork.
- [x] Add safe background manual retagging and clickable fallback notifications.
- [x] Integrate the approved SVG into the header/window icon and standalone source.
- [x] Load the supplied key locally without packaging or logging it; verify local
  dependencies and run source, pipeline, native GUI and service smoke checks.
- [x] Update standalone source, setup guide, validation evidence and source ZIP.

AcoustID requires an application key. Ship no personal configuration in the source
package. Publisher configuration can remove the end-user key setup. Albums/releases
can be ambiguous; do not claim a fingerprint identifies the original album edition.

All source-delivery tasks are implemented and verified locally. The supplied
AcoustID key was rejected (code 4); a valid application key and real musical
match remain an operational release gate, recorded in VALIDATION.md.
