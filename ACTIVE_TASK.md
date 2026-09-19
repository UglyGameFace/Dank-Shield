# ACTIVE TASK

## ID
DS-DESIGNER-262 — Server Designer UX + font system hardening

## Status
IMPLEMENTATION COMPLETE — final exact-head CI and merge gate

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
- Updated the canonical runtime, exact-proof, full-catalog, setup gallery, and active
  queued font flow so the new styles do not exist in only one UI.
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

## Validation completed on final code head `699e8199c46815c6b195256c4737a54ec706a7f7`
- Dank Shield CI: **PASS**
  - Python compile: PASS
  - full unit suite: **1688 passed, 9 warnings**
  - standalone tools: PASS
  - public setup/command/invite/setup-safety audits: PASS
  - Dank Design Smart Auto-Detect audit: PASS
  - role-truth and event-boundary audits: PASS
- Dank Design Regression CI: **PASS**
  - focused Design Studio suite: **113 passed, 1 warning**
  - Smart Auto-Detect audit: PASS
  - redundancy/ownership audit: PASS
  - UX/static audits: PASS
- Channel Builder Queue Sanity: PASS
- Application Command Size Diagnostics: PASS
- Profile Runtime Diagnostics: PASS
- Schema Authority SQL: PASS
- DS Backlog 027 Validation: PASS
- Ticket Owner Emergency Override: PASS
- PR was mergeable and **0 commits behind main** at this validation point.
- Final diff review removed unrelated dormant compatibility-file edits and kept the
  changes scoped to active Server Designer/font ownership, tests, audits, and this task record.

## Final gate
This task-record update is documentation-only and changes the PR SHA. Re-run required
CI on that exact final head. If it stays green, mark PR #262 ready, merge it, and verify
main contains the validated branch head.

After that main verification, this task is **COMPLETE** without another code change.
Live Discord acceptance remains a deployment smoke test, not a reason to reopen or
rewrite already-green code unless the deployed behavior exposes a concrete regression.

## Risk / compatibility
- Existing saved themes and font IDs remain valid.
- Existing saved narrow rules remain authoritative over the server draft.
- Decorative fonts remain visibly marked as readability-risky.
- Upside Down remains available only as a legacy decode/compatibility transform and is
  no longer offered as a live selectable design font because it cannot safely round-trip.
- This task does not change permissions, channel order, topics, ticket placement, roles,
  verification, or protection policy.

## Next step
Run exact-head CI for this documentation-only finalization commit. If all required
checks stay green, mark PR #262 ready, merge it, and verify the merged result on main.
