# ACTIVE TASK

## ID
DS-WELCOME-UNICODE-LONGTAIL — Deterministic long-tail lifecycle-card Unicode fallback

## Status
IMPLEMENTED — validation and exact-head merge gate pending

## Single active-task lock
Only the lifecycle-card long-tail Unicode fallback task is active. Do not admit
unrelated redesign, cleanup, or feature work until this branch is validated,
cleaned up, merged, and verified on main.

## Previous task closed
PR #262 (Server Designer UX + font system hardening) merged as
`e1ed7093983fffd5e947b1a240e3d455df80dc1e`. Its exact final head
`08be759a4ca87592e05ca8c600e9a5d5ab92d4b3` passed all eight required
workflows, and the merged commit reports `discloud/commit: success`.

## User-visible problem
Welcome/exit cards still rendered tofu boxes for a live Discord display name
containing `ᗩ ᗰ ᒪ`, even after PR #260 added exact-Unicode preservation and
grapheme-aware font fallback.

## Root cause
PR #260 fixed text rewriting and single-font rendering, but the production
fallback inventory did not contain a face covering Unified Canadian Aboriginal
Syllabics. The resolver therefore preserved the exact characters but eventually
selected the best available unsupported face, which Pillow rendered as tofu.

This is a coverage problem, not a normalization or Discord-name problem.

## Execution path
`welcome_card_runtime.py`
→ `lifecycle_card_text.image_card_member()`
→ `welcome_card_service.py`
→ `welcome_card_typography_engine.py`
→ `unicode_font_fallback.py`
→ PNG

Exit cards share the same lifecycle text adapter and Unicode fallback engine.

## Implementation
- ship the official Noto Sans Canadian Aboriginal variable font inside the repo
- retain the upstream SIL Open Font License alongside the binary
- discover packaged fallback faces from a deterministic module-relative path
- put packaged fallbacks ahead of environment-dependent JustMyType/system faces
- preserve themed/custom fonts as the primary choice and only fall back for
  grapheme clusters they cannot render
- add the exact live sample `ᗩ ᗰ ᒪ` to production fallback coverage tests
- add the exact live sample to full welcome-card rendering regressions
- add a self-contained regression that disables registered/system fallbacks and
  proves the bundled face alone covers the live name

## Compatibility / cleanup
- no NFKC/transliteration/replacement behavior is reintroduced
- no dynamic Discord text is uppercased or rewritten
- no existing fallback pack or custom-font behavior is removed
- no unrelated Server Designer behavior is changed
- the bundled font is unmodified and kept with its OFL-1.1 license

## Validation / merge gate
Pending on the exact final branch head:
- targeted Unicode fallback tests
- full unit suite
- Python compile
- standalone repository audits
- Dank Shield CI
- Dank Design Regression CI and companion required workflows
- final diff / accidental-change inspection
- branch currentness against main
- merge only the exact validated SHA
- post-merge main verification and `discloud/commit: success`

## Backlog
None added from this task.

## Next step
Open the task PR, run targeted and full CI on the exact head, fix only failures
that share this task's root cause, then complete the merge and deployment gate.
