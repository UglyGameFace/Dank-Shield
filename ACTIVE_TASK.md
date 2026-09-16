# ACTIVE TASK

## DS-AUD-SETUP-PICKER-RESOURCE-DISCOVERY — Replace Fix Access native entity picker with dedicated Dank target browser

**Outcome target:** The production **Fix Access** channel/category selection path is owned by Dank Shield, built from the current guild cache, searchable, paged past Discord's 25-option static-select limit, mobile-safe, owner-locked, and error-reporting. It must not open Discord's generic native ChannelSelect for target discovery or silently end in `This interaction failed`.

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `audit/setup-picker-resource-discovery`
**Base main:** `363e0d9026ac10a0c930badd606df59e0750e46f`
**Previous integrated task:** PR #239 merged at `363e0d9026ac10a0c930badd606df59e0750e46f`

## Scope

- canonical `stoney_verify.permission_repair` UI boundary
- Fix Access channel/category discovery and selection only
- shared Dank picker reuse
- setup and diagnostics handoffs that already call `open_target_permission_repair`
- regression/static acceptance coverage
- task / PR bookkeeping

Out of scope:
- permission-repair mutation semantics
- permissions granted by minimum/full repair modes
- guild-config persistence
- unrelated setup wizard role/channel pickers
- protection/design/ticket/member picker migrations
- startup-guard redesign

## Findings / root cause

1. The user-reported Fix Access screen resolves to `stoney_verify.permission_repair.TargetPermissionRepairView`.
2. That view directly created `_TargetChannelSelect(discord.ui.ChannelSelect)`.
3. Discord therefore owned target discovery/rendering for this path; Dank Shield had no paging, no bot-side search, and no deterministic server-resource catalog.
4. The shared picker contract already exists, but `DankChannelSelect` is intentionally a wrapper around Discord's native entity selector, so swapping one native wrapper for another would preserve the reported UX failure.
5. The shared-picker migration documentation explicitly requires setup picker surfaces to avoid silent `Interaction failed` behavior and remain usable on mobile.
6. Both production entry points — Setup Permission Repair (`Specific Channel`) and `/dank diagnostics` (`Fix Channel Access`) — lazily import the canonical `open_target_permission_repair`, so fixing that boundary covers both without duplicating UI logic.
7. Historical picker branches confirm that dedicated/shared picker work existed, but channel/role entity selection remained native Discord selection.

## Implementation

- Preserve the existing repair/audit implementation byte-for-byte as internal `permission_repair_core`.
- Keep `stoney_verify.permission_repair` as the stable public module boundary.
- Add `permission_repair_ui` as the production UI owner.
- Replace the raw native target selector with a **Choose Channel / Category** button.
- Open a `DankPickerView`-based browser populated from supported `guild.channels`.
- Hide targets the acting admin cannot view/manage unless they are guild owner/Administrator.
- Page target choices in groups of 25.
- Add name / channel-ID / mention search.
- Preserve current feature, repair mode, target, and category-child state while navigating.
- Resolve selections back through `guild.get_channel(id)` immediately before use.
- Add explicit `View.on_error` handling for both the browser and Fix Access screen so callback exceptions produce a safe answer instead of a silent Discord failure.
- Keep existing setup/diagnostics callers unchanged; their canonical import automatically receives the fixed UI.

## Validation plan

- Existing permission-repair UI regression tests must still pass.
- Add dedicated tests proving:
  - Fix Access no longer contains a native `discord.ui.ChannelSelect`.
  - the canonical module binds core return points to the dedicated UI.
  - 61 candidates paginate 25 / 25 / 11.
  - hidden channels are filtered for non-Administrator actors.
  - search works by channel name and channel ID / mention.
  - dedicated views expose explicit error handlers.
- Update DS-BACKLOG-027 static acceptance ownership so repair behavior is checked in the unchanged core while picker ownership is checked in the dedicated UI.
- Run all pull-request workflows on the exact final head before merge-readiness is claimed.

## Cleanup / compatibility

- No startup guard or import hook is added.
- Repair application, undo, explicit-deny confirmation, permission calculations, queueing, audit snapshots, and reauthorization remain unchanged in the internal core.
- The stable `stoney_verify.permission_repair` imports used by setup, diagnostics, and existing tests remain valid.
- Broader raw picker migration in `public_setup_picker.py` is deliberately not mixed into this task.

## Backlog

- Continue the master audit after this task integrates.
- Audit remaining `/dank setup` raw RoleSelect/ChannelSelect surfaces separately; do not fold them into this Fix Access root-cause PR.

## Next step

Commit the dedicated target browser and focused regression coverage on this branch, open a draft PR, run exact-head CI, correct only in-scope failures, then complete review/drift/scope cleanup before integration.
