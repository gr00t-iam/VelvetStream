# Compact velvet-purple UI

User intent: replace green buttons with deep velvet purple and keep the main
functions visible while the app is a smaller, non-fullscreen window.

Implementation keeps the existing backend and media player. Buttons/selected
tabs use #3B1E54 with white text; lavender accents keep dark-theme controls legible.
Cards use content-sized heights with reduced padding. The outer scroll area is
removed. The player selector and Open control share a row; session actions share
the status row. Output-folder controls become a single row. The latest activity
is always visible, and full logs expand in place. Compact windows simplify help
copy and put silence sliders behind an explicit toggle.

Verification checks actual button rendering in both themes, positions of primary
actions and player controls at 820 x 700 in both source modes, log expansion,
existing UI/backend tests, previews and a native x64 rebuild. Build side-by-side
so currently running recordings/downloads are not interrupted; repoint the user's
testing shortcut after packaged-app validation.
