# ACTIVE TASK

## ID
DS-DESIGNER-262 — Server Designer UX + font system hardening

## Status
IN PROGRESS — implementation and regression validation

## Single active-task lock
Only this Server Designer improvement task is active. Do not switch to unrelated
work until this task is investigated, implemented, validated on the exact final
head, cleaned up, merged, and verified on main.

## User-visible goals
- Make **Design Entire Server** substantially easier to understand and use on mobile.
- Let the server-wide workflow choose and preview better Unicode font styles directly.
- Expand the curated theme/font catalog without reintroducing crossed ownership.
- Prevent a successful design Apply from later appearing to "randomly revert".
- Preserve preview-first safety, saved-rule precedence, protection, exact-item editing,
  and Undo.

## Analysis / root cause
### Font UX
- The naming engine already had a broad Unicode transformation system, but the
  consolidated Server Designer only exposed Theme, Strength, and Separator.
- Font choice was hidden inside theme presets even though Channel Name Fonts had a
  richer visual catalog elsewhere.
- Font labels/maps were duplicated across Channel Builder compatibility layers, which
  made adding a style in one place easy to miss in another.
- Server-wide format locks derived font only from the selected theme, so an explicit
  server font override would have been lost when a global rule was synchronized.

### Unexpected reversion
- Current Server Designer has no background "enforce saved design" loop.
- Plain channel-name edits are not classified as destructive AntiNuke channel-update
  events; overwrite mutations are handled separately.
- The active reviewed-Apply flow did contain one explicit post-success reversal path:
  after every successful live rename batch it attempted to persist durable Undo history,
  and if that file write raised, it called `compensate_applied(...)` and renamed the
  entire successful batch back.
- That behavior exactly produces the reported symptom: the design visibly finishes,
  then the names return without the user pressing Undo.
- Durable Undo storage failure alone must not mutate live names after a successful Apply.

## Implementation
### Server Designer
- Added an owned **Font** selector between Theme and Strength.
- Added **Theme Default** so users can return cleanly to a preset's recommended font.
- Theme changes intentionally clear the explicit font override and restore preset ownership.
- Added live font samples in the picker and current Server Designer summary.
- Added a combined category/channel style example before Preview.
- Current font and category-frame state are visible on the same screen.
- Controls fit Discord's five-row component limit:
  1. Theme
  2. Font
  3. Strength
  4. Separator
  5. Preview / separator preview / clean redesign
- Simplified button labels for mobile.

### Font catalog
- Added exact **Clean Sans** and **Double-Struck** Unicode styles.
- Added curated themes that use more of the supported catalog:
  - Night Gothic
  - Neon Rush
  - Terminal Neon
  - Fullwidth Arcade
  - Small Caps Social
  - Modern Minimal
  - Double-Struck Luxe
  - Luxury Script
- Kept the theme and font counts under Discord's 25-option select limit.
- Updated runtime, exact-proof, full-catalog, setup gallery, Channel Builder scope,
  and queue fallback layers so the new styles do not exist in only one UI.
- Added shared font labels/previews in the Server Design naming engine.

### Reversion hardening
- Successful reviewed Apply now uses durable Undo storage when available.
- If durable Undo persistence fails, the live design **stays applied** and an emergency
  memory-only Undo snapshot is retained.
- The completion screen explicitly warns that memory-only Undo disappears on restart.
- Successful Apply is no longer compensated merely because Undo-history persistence failed.
- Real rename/apply failures and separator-setting persistence failures still compensate,
  because those indicate an incomplete or internally inconsistent transaction.

## Validation added / updated
- Server selector ownership now covers Theme + Font + Strength + Separator.
- Explicit server font survives global-lock synchronization.
- Theme Default / theme changes restore predictable preset font ownership.
- Font picker stays within Discord select limits and includes visual samples.
- New Unicode styles transform and normalize back to the original base name.
- Runtime/full-catalog/exact-proof font maps are checked for catalog parity.
- Durable Undo write failure is behavior-tested to fall back to memory.
- Static regression forbids the old "Apply Reversed Because Undo History Could Not Be Saved"
  successful-apply path.

## Validation still required
- Exact-head Python compile.
- Dank Design focused regression suite.
- Full Dank Shield CI.
- Existing public command/setup/design audits.
- Final diff review for stale duplicated font labels/maps and unrelated edits.
- Main-currentness / mergeability check.
- Live Discord acceptance after deployment:
  - Theme → Font → Strength → Separator flow
  - live examples update correctly
  - Preview Server / Apply
  - no spontaneous post-success reversion
  - memory-only Undo warning path if durable storage is unavailable

## Risk / compatibility
- Existing saved themes and font IDs remain valid.
- Existing saved narrow rules remain authoritative over the server draft.
- Decorative fonts remain visibly marked as readability-risky.
- Upside Down remains catalogued but is still proof/compatibility constrained in live
  Channel Name Fonts paths.
- This task does not change permissions, channel order, topics, ticket placement, roles,
  verification, or protection policy.

## Next step
Create the focused draft PR, run exact-head CI, fix every task-related regression,
then review and merge only after the final head is green.
