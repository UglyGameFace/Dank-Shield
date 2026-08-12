# ACTIVE TASK

## DS-LIFECYCLE-028 — Repair welcome/exit card visual integrity

**Status:** ACTIVE — IMPLEMENTED / VALIDATING
**Branch:** `fix/ds-lifecycle-card-visual-integrity`
**Base:** `03e90746c230b66599e8d931f86df56dd9fa0d8c` (merged PR #182)
**Started:** 2026-08-11

## Reported production failure

The public join/leave log still exposed internal runtime identifiers in embed footers, including:

- `dank_shield:welcome_card_runtime:v1`
- `dank_shield:exit_card_runtime:v1`

The generated bitmap card also rendered decorative Unicode display names as missing-glyph boxes even though Discord itself displayed the same names correctly.

## Root cause

- `build_join_card_embed()` and `build_exit_card_embed()` explicitly wrote internal ownership/debug identifiers into public `Embed.set_footer()` calls.
- Lifecycle duplicate suppression does not depend on those footers; both canonical runtimes already own scoped in-memory delivery locks/recent-delivery suppression.
- The Pillow image path received the raw Discord `display_name`. Its existing `_safe_text()` logic handled whitespace/length only and did not normalize Unicode compatibility alphabets commonly used for decorative Discord names.

## Implementation

- [x] Removed internal runtime identifiers from live welcome-card and exit-card embed footers.
- [x] Preserved embed timestamps and the original Discord-facing display-name/template text.
- [x] Added `stoney_verify/lifecycle_card_text.py` as the shared bitmap-only text adapter.
- [x] Apply Unicode NFKC compatibility normalization only to the member/server copy passed into lifecycle image rendering.
- [x] Preserve ordinary accented, emoji, and non-Latin text instead of ASCII-stripping names.
- [x] Updated canonical join and exit runtimes to render image files through the shared adapter.
- [x] Added runtime regressions proving styled names become readable in image rendering while the public embed retains the original styled name.
- [x] Updated welcome/exit wiring and join/leave centralization guards so leaked live runtime footer IDs cannot return silently.
- [x] Added direct lifecycle image-text tests covering mathematical double-struck/script/monospace compatibility alphabets plus unchanged ordinary Unicode.

## Validation required

- [ ] Targeted welcome/exit runtime behavior tests green.
- [ ] Lifecycle image-text tests green.
- [ ] Welcome/exit wiring static tests green.
- [ ] Join/leave centralization tool green.
- [ ] Python compile/static coverage green.
- [ ] Full Dank Shield CI green on exact final PR head.
- [ ] Final diff/dead-reference/review-thread inspection complete.

## Definition of Done

A real join or leave must post a clean public lifecycle embed with no internal `dank_shield:*runtime*` footer text, generated card names using decorative compatibility alphabets must remain human-readable instead of tofu/missing-glyph boxes, normal Discord-facing names must not be rewritten, join/exit ownership and duplicate suppression must remain unchanged, and the exact final PR head must pass targeted plus full regression validation.
