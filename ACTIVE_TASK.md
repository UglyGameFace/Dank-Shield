# ACTIVE TASK

## DS-WELCOME-UNICODE-260 — Preserve exact Unicode in lifecycle cards

**Status:** FINAL HEAD REVALIDATION

**Branch:** `fix/welcome-card-unicode-rendering`
**Base main:** `84f4b3ca5bec12037fd992e9d7e0d9271f5d6d87`
**Functionally validated head:** `2f9c6cce45d100d7a4d9df0d87acd18c1083e3d2`

## Active task lock

The Single Active Task Lock remains exact Unicode rendering for Welcome Card
Studio and the shared exit-card typography path. No unrelated implementation,
redesign, or cleanup has been admitted.

## Problem / root cause

Discord display names may contain mathematical alphabets, symbols, non-Latin
scripts, combining sequences, and emoji. The lifecycle bitmap path was not
faithful because:

- `lifecycle_card_text.image_safe_text()` applied NFKC compatibility
  normalization, intentionally rewriting names such as `𝔼𝕪𝕖𝕫` to `Eyez`.
- `welcome_card_typography_engine` rendered dynamic text through one Pillow
  font at a time, so missing cmap entries became replacement/tofu boxes.
- some visual styles uppercased dynamic member names before rendering.

## Execution path

`welcome_card_runtime.py`
→ `lifecycle_card_text.image_card_member()`
→ `welcome_card_service.py`
→ `welcome_card_typography_engine.py`
→ PNG

Exit cards share the same text adapter and typography engine through
`exit_card_runtime.py` / `exit_card_renderer.py`.

## Implemented changes

- preserve the exact Discord Unicode spelling of lifecycle member/server text;
  only single-line whitespace cleanup remains
- add cmap-aware font selection and fallback per Unicode grapheme cluster
- keep adjacent same-font clusters together for RAQM shaping
- disable manual tracking for complex scripts/emoji where tracking would break
  shaping
- keep themed/custom fonts first and fall back only for missing coverage
- add deterministic fallback packs for Western/math/symbol, RTL, South Asian,
  Southeast Asian, African, CJK, and emoji coverage
- include STIX math fallback for mathematical/decorative Unicode alphabets
- make name/subtitle measurement use the same fallback logic as final rendering
- truncate on grapheme boundaries instead of raw code points
- stop visual styles from rewriting dynamic member-name casing
- apply the same exact-Unicode path to exit cards
- replace legacy NFKC tests/static audits with exact-Unicode regressions
- add production coverage tests for decorative Unicode, Greek, accents, Arabic,
  CJK, emoji, and partial custom fonts
- add Discloud Canvas dependencies required by the production text-render stack

## Validation results

On head `2f9c6cce45d100d7a4d9df0d87acd18c1083e3d2`:

- Dank Shield CI: PASS
- Python compile check: PASS
- full unit suite: **1656 passed**, 9 warnings, 0 failures
- standalone repository tool checks: PASS
- join/leave log centralization audit: PASS
- managed-category SQL smoke test: PASS
- claim-first ticket security: PASS
- Application Command Size Diagnostics: PASS
- Dank Design Regression CI: PASS
- Profile Runtime Diagnostics: PASS
- Ticket Owner Emergency Override: PASS
- public setup / command surface / friction / invite permissions audits: PASS
- setup safety, Dank Design auto-detect, role-truth, and event-boundary audits:
  PASS
- branch was 0 commits behind `main` at validation time
- diff review contained only lifecycle-card implementation, dependencies,
  configuration, task record, and directly related regressions/audits

The earlier CI failures were traced to task-local causes and corrected:
dependency version alignment, legacy tests/audits that still required NFKC,
and missing decorative-math fallback coverage.

## Cleanup / conflict inspection

- obsolete NFKC normalization behavior is removed from the lifecycle adapter
- stale tests and the standalone centralization audit no longer preserve the
  superseded normalization workaround
- legacy single-font helper remains only as a last-resort renderer path and for
  controlled/static compatibility; dynamic lifecycle text uses the fallback
  engine
- no unrelated production behavior was changed
- no debug/temporary code or generated artifacts are intentionally included
- Private Use Area/unassigned Unicode cannot have a standardized universal
  appearance without the defining custom font; standardized text is preserved
  rather than transliterated

## Merge gate

This task is not complete yet. This task-record commit changes the PR head, so
the new exact final head must pass all required checks again before the PR can
be marked ready or merged. After merge, `main` and deployment status must be
verified before the task is closed.

## Backlog

None added from this task.

## Next step

Run exact-head CI on the task-record head, perform the final main-currentness
and diff check, then mark PR #260 ready, merge only the validated SHA, and
verify the resulting `main`.
