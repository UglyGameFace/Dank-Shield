# ACTIVE TASK

## DS-AUD-PROTECTION-PICKERS — Move /dank protection invite/link/spam pickers onto canonical Dank picker ownership

**Outcome target:** Every normal public `/dank protection` invite/link/spam selection flow uses the shared Dank Shield picker/resource-browser contract, keeps feature persistence in the native protection/spam owners, is owner-locked and mobile-safe, and no longer depends on startup guards that monkey-patch raw `discord.ui.Select` callbacks.

**Status:** INVESTIGATION / IMPLEMENTATION

**Branch:** `audit/protection-picker-migration`
**Base main:** `c5820840a162803765d50806686f539fb3e388ff`
**Previous integrated task:** PR #241 merged at `c5820840a162803765d50806686f539fb3e388ff`

## Scope

- public `/dank protection` invite/link/spam picker paths
- Invite Shield cleanup channel selection
- Discord Invite Blocker bot/channel scope selection
- shared `DankPickerView` / `DankGuildResourceBrowserView` reuse
- removal of picker UI/business-logic ownership from startup guards where the production path is proven
- preservation of existing guild-scoped spam/invite persistence and protection refresh behavior
- focused regression/static acceptance coverage
- task/PR bookkeeping

Out of scope unless production tracing proves otherwise:
- setup picker work already merged in PR #241
- design/ticket/member/welcome picker migrations
- AntiNuke policy semantics
- unrelated Protection Center controls
- spam/invite enforcement algorithm changes

## Findings / root cause

1. PR #241 completed the normal public `/dank setup` picker migration and is merged.
2. The shared-picker migration order explicitly names `/dank protection` invite/link/spam pickers as the next migration target and prohibits raw one-off selectors plus startup-guard-owned business logic.
3. `startup_guards/protection_invite_cleanup_picker_guard.py` currently owns a raw `InviteCleanupChannelSelect(discord.ui.Select)`, its paging UI, and monkey-patches `CleanTargetChannelInvites.callback` at startup.
4. `startup_guards/spam_guard_invite_scope_pagination_guard.py` currently owns raw `BotPageSelect(discord.ui.Select)` and `ChannelPageSelect(discord.ui.Select)` surfaces, pages resources manually, patches spam settings accessors, and refreshes Protection Center state from the guard.
5. `startup_guards/protection_center_filter_list_guard.py` explicitly chains the invite cleanup picker guard, proving the picker is activated through startup-guard composition rather than a native protection owner.
6. `commands_ext/public_protection_center.py` is the production-facing Protection Center and already owns permission checks, guarded interaction error handling, guild config access, spam settings save/load helpers, and protection-message refresh behavior. That is the correct native UI boundary for protection choices.
7. The newly merged shared `DankGuildResourceBrowserView` can replace manual 25-item channel paging/search for cleanup targets. Normal finite protection choices should use `DankPickerView` rather than feature-local raw `discord.ui.Select` classes.

## Implementation direction

- Move Invite Shield cleanup target selection out of `protection_invite_cleanup_picker_guard.py` and into a native protection-owned helper/view using the shared guild resource browser.
- Move Discord Invite Blocker bot/channel scope selection out of `spam_guard_invite_scope_pagination_guard.py`; keep spam setting persistence in `spam_guard` / canonical guild config services.
- Reuse shared picker owner-locking, Close/Back/error behavior instead of reimplementing per guard.
- Preserve current selected IDs, all-bots/all-channels semantics, paste-ID fallback where still useful, and Protection Center refresh behavior.
- Remove startup-guard callback monkey-patches once the native production path owns the picker.

## Validation plan

- prove normal public protection invite/link/spam selection paths do not instantiate raw `discord.ui.Select` from startup guards
- prove cleanup channel discovery uses the shared cache-backed browser and re-resolves the selected channel before mutation
- prove invite-blocker bot/channel choices preserve existing selected IDs and guild-scoped saves
- prove wrong-owner interactions are rejected safely
- prove empty/removed resources return a safe user response instead of `Interaction failed`
- prove existing protection/spam runtime behavior and enforcement tests remain green
- run exact-head GitHub Actions before merge-readiness is claimed

## Cleanup / compatibility

- Do not create a second spam settings writer or guild-config writer.
- Do not move enforcement policy into UI code.
- Do not retain startup guards solely to own picker UI after native migration.
- If a guard still performs non-picker compatibility work, split or narrow it rather than deleting unrelated behavior.

## Backlog

- `/dank design` style/layout/font/separator picker migration
- ticket panel/category picker migration
- members cleanup/review picker migration
- self-role/profile picker migration
- welcome/modlog setup picker migration
- remaining startup-guard picker cleanup after feature-native migrations

## Next step

Trace the exact protection callback handoffs for Invite Shield cleanup and Discord Invite Blocker scope, move the first proven picker path to the shared native picker contract, add focused regressions, then continue through the remaining invite/link/spam picker surfaces before opening the task PR.
