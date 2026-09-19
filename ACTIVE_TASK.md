# ACTIVE TASK

## ID
DS-WELCOME-UNICODE-REGRESSION-263 — Guarantee long-tail Unicode glyph coverage in lifecycle cards

## Status
IMPLEMENTATION / VALIDATION

## Branch
`fix/welcome-card-long-tail-unicode`

## Base main
`e1ed7093983fffd5e947b1a240e3d455df80dc1e`

## Single active-task lock
Only this deployed welcome/exit-card Unicode regression is active. Do not move
to unrelated work until the live-font coverage fix is implemented, tested,
validated on the exact final head, merged, and verified on deployed main.

## Production evidence
A post-PR-260 live Discord welcome at approximately 2026-09-19 17:03 local time
showed the Discord mention/embed preserving the member's stylized Unicode name
while the generated welcome-card PNG rendered several characters as tofu boxes.

Representative live text:

`PᗩᗰƐᒪᗩ`

The missing characters are from Unified Canadian Aboriginal Syllabics (for
example U+15E9, U+15F0, U+14AA), mixed with ordinary Latin/Latin-Extended text.

PR #260 is present on main and Discloud reported `discloud/commit: success`.
This is therefore a real production coverage gap, not an old deployment.

## Root cause
PR #260 correctly removed destructive NFKC normalization and added grapheme-aware
font fallback, but its fallback inventory was still incomplete:

- the production fallback registry intentionally admitted only Noto families and
  STIX Two Math
- the installed JustMyType packs cover many common scripts but do not include a
  Canadian Aboriginal face
- Discloud's `APT=canvas` environment supplies common rendering libraries and
  Liberation fonts, not a guaranteed long-tail Unicode safety-net font
- the regression suite used `PΛMELA` with Greek Lambda rather than the actual
  Canadian-syllabics lookalikes, so CI never exercised this block
- when no face fully covered a grapheme, the renderer ultimately selected the
  best partial face, which preserved the code point but produced a missing-glyph box

## Required behavior
- preserve the exact Discord display-name string
- never transliterate, compatibility-normalize, strip, or silently substitute
  assigned Unicode characters
- retain themed/custom fonts whenever they cover the requested grapheme
- use script-specific Noto/STIX fallbacks next
- provide deterministic app-local long-tail fonts on Discloud rather than relying
  on whatever fonts happen to exist in the host image
- use a broad quality fallback before the last-resort pan-Unicode face
- cover all printable Unicode Plane 0 characters with an actual fallback glyph,
  while retaining current higher-plane Noto/STIX/emoji support and adding an
  upper-plane Unifont safety net
- fail the deployment build if pinned fallback fonts cannot be downloaded or
  fail integrity verification, rather than deploying known tofu behavior

## Implementation
- add `tools/provision_unicode_fonts.py`
  - provisions a pinned official Noto Sans Canadian Aboriginal variable face, GNU Unifont Plane 0, and GNU Unifont Upper
  - validates exact byte size and pinned digest
  - uses alternate GNU mirrors for Unifont
  - writes atomically into `.runtime_fonts/`
  - fails closed on network/integrity failure
- add `BUILD=python tools/provision_unicode_fonts.py` to Discloud deployment
- ignore `.runtime_fonts/` in Git
- make the renderer append app-local long-tail faces after preferred/custom and
  script-specific registered faces
- prefer the dedicated Noto Canadian Aboriginal face for the live UCAS glyphs, use any available system FreeSans as an extra broad fallback, then use Unifont as the
  standardized-Unicode safety net
- keep common system FreeSans locations as an extra portability path
- add the exact live Canadian-syllabics name to tracking, coverage, and full-card
  rendering regressions
- add deterministic synthetic-font tests proving the long-tail source is chosen
  for the missing Canadian-syllabics graphemes
- add provisioner integrity/fail-closed/deployment-wiring tests

## Validation gate
Before merge:

- exact live text `PᗩᗰƐᒪᗩ` resolves every visible grapheme to a face whose cmap
  actually contains it
- exact live text renders a non-empty full welcome card without replacement boxes
  under the production fallback path
- custom-font fallback behavior remains correct
- Arabic/Indic/emoji grapheme shaping/tracking protections remain green
- lifecycle adapter still contains no NFKC/transliteration workaround
- provisioner tests prove checksum validation, mirror fallback, atomic install,
  and fail-closed behavior
- Discloud config statically requires the provisioner build step
- full compile/unit/standalone/audit suite passes
- all companion workflows pass on the exact final head
- branch is 0 behind current main
- final diff remains task-limited
- merge only the exact validated SHA
- verify merged main contains the validated head
- require post-merge `discloud/commit: success`; because the build step is
  fail-closed, that status is also evidence that the pinned font assets were
  provisioned successfully

## Risk / compatibility
The app does not commit font binaries in Git. The provisioner downloads
pinned upstream font files during deployment. The fallback faces are used only
when higher-priority fonts lack a requested grapheme, so existing visual styles
remain unchanged for ordinary names.

Private Use Area and unassigned Unicode still cannot have a universal standardized
appearance because no standard glyph exists for them. Assigned standardized
Unicode is preserved.

## Next step
Run targeted Unicode/provisioner tests, open the regression PR, inspect exact-head
CI, fix any task-local failures, then merge and verify the Discloud deployment.
