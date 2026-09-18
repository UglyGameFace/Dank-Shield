# ACTIVE TASK

## DS-WELCOME-UNICODE-260 — Preserve exact Unicode in lifecycle cards

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `fix/welcome-card-unicode-rendering`
**Base main:** `84f4b3ca5bec12037fd992e9d7e0d9271f5d6d87`

## Active task lock

The Single Active Task Lock is exact Unicode rendering for Welcome Card Studio
and the shared exit-card typography path. No unrelated redesign or cleanup is
in scope.

## Problem

Discord display names may contain mathematical alphabets, symbols, non-Latin
scripts, combining sequences, and emoji. The lifecycle image path currently
cannot render that input faithfully.

Two mechanisms cause the regression:

- `lifecycle_card_text.image_safe_text()` applies NFKC compatibility
  normalization, intentionally rewriting names such as `𝔼𝕪𝕖𝕫` to `Eyez`.
- `welcome_card_typography_engine` renders dynamic text through one Pillow
  font at a time. Missing cmap entries therefore become replacement/tofu boxes.

Some styles also uppercase member names before rendering, which conflicts with
the exact-display-name requirement.

## Required behavior

- preserve the exact Discord Unicode spelling of member and guild display text
- collapse only card-incompatible line/repeated whitespace
- keep the selected/custom visual font when it contains the requested glyphs
- fall back per grapheme cluster when the preferred font lacks coverage
- keep adjacent same-font clusters together so RAQM can shape Arabic/Indic runs
- disable manual letter tracking where it would break complex shaping
- cover Western/math/symbol, RTL, South Asian, Southeast Asian, African, CJK,
  and emoji text with bundled Noto fallback packs
- never use NFKC/transliteration as a rendering workaround
- retain existing card dimensions, effects, fitting, custom fonts, and vector
  card icons

## Execution path

`welcome_card_runtime.py`
→ `lifecycle_card_text.image_card_member()`
→ `welcome_card_service.py`
→ `welcome_card_typography_engine.py`
→ PNG

Exit cards share the same text adapter and typography engine through
`exit_card_runtime.py` / `exit_card_renderer.py`.

## Changes

- add a Unicode-aware font fallback/shaping helper
- install deterministic Noto fallback packs plus regex grapheme segmentation
- preserve original lifecycle Unicode text instead of NFKC normalization
- route styled name/welcome text and dynamic subtitle text through fallback
  measurement/rendering
- remove style-driven uppercasing of dynamic member names
- add Unicode/fallback/custom-font regression coverage

## Validation / merge gate

Before merge:

- exact-text adapter regressions pass
- deterministic primary→fallback glyph selection regressions pass
- mixed decorative, Greek, accented, RTL, CJK, and emoji card renders pass
- custom-font missing glyphs fall back rather than tofu
- existing Welcome Card Studio / custom-font / exit-card regressions pass
- full Python compile and applicable repository CI pass on exact final head
- required protected-branch checks pass
- final diff is task-limited and branch is current with main
- merge only the exact validated SHA
- verify resulting main and deployment status

## Cleanup / conflicts

The old NFKC workaround and its tests are superseded by the fallback renderer
and must not remain as duplicate behavior. Existing single-font helpers may
remain only where they render controlled static ASCII or are required by
backward-compatible tests.

## Backlog

None. Unrelated findings remain outside this task.

## Next step

Wire the shared typography engine to the Unicode fallback helper, add focused
renderer regressions, then run exact-head validation.
