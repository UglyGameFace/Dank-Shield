# ACTIVE TASK

## DS-AUD-PROTECTION-INVITE-PICKER-OWNERSHIP — Move /dank protection invite targeting into native ownership

**Outcome target:** The production `/dank protection` Invite Shield / invite hard-block targeting and invite-cleanup channel selection are owned by the canonical protection feature, use the shared Dank picker/resource-browser contract, and do not depend on startup guards that monkey-patch picker callbacks or `ProtectionCenterView.__init__`. The flow must remain guild-scoped, owner-safe, mobile-usable, searchable/paged where needed, and must answer failures instead of ending in `Interaction failed`.

**Status:** IMPLEMENTATION / EXACT-HEAD VALIDATION

**Branch:** `audit/protection-picker-native-ownership`
**Base main:** `c5820840a162803765d50806686f539fb3e388ff`
**Previous integrated task:** PR #241 merged at `c5820840a162803765d50806686f539fb3e388ff`
**Current PR:** #242 `Move Protection Center invite pickers into native ownership`

## Scope

- production `/dank protection` invite-target/scope flow
- production Invite Shield historical-cleanup channel selection
- canonical `stoney_verify.commands_ext.public_protection_center` ownership boundary
- shared `DankGuildResourceBrowserView` reuse for server-channel discovery
- explicit durable target metadata ownership without patching Spam Guard persistence
- preservation of existing invite-policy enforcement, allowed/internal invite behavior, target precedence, guarded interactions, and the original Invite Shield on/off action
- focused regression/static acceptance coverage
- task/PR bookkeeping

Out of scope unless tracing proves a direct dependency:
- AntiNuke policy behavior
- Automod filter-list editor ownership
- generic Spam Guard detection thresholds / response-mode semantics
- `/dank design`, tickets, members, self-role/profile, welcome, and modlog picker migrations
- unrelated startup-guard cleanup

## Findings / root cause

1. PR #241 completed shared resource-browser migration for normal public `/dank setup` and is merged.
2. The shared-picker migration contract lists `/dank protection` invite/link/spam pickers as a next feature surface and identifies startup-guard callback monkey-patching as an ownership anti-pattern.
3. The important runtime finding is that the historical invite picker guard chain is **not** a normal boot owner. `startup_guards.__init__` now treats `_STARTUP_GUARDS` as historical metadata and the runtime-ownership audit says the old bulk loader is not part of normal startup.
4. Canonical `public_protection_center.ProtectionCenterView` owns `/dank protection`, but before this task it had no native target browser or historical-cleanup browser. Its Invite Blocker button only called `_toggle_invite_shield`.
5. Dormant historical implementations are fragmented across `protection_invite_cleanup_picker_guard`, `protection_center_invite_controls_guard`, `spam_guard_invite_scope_pagination_guard`, and `protection_center_invite_simple_flow_guard`. They define raw `discord.ui.Select` surfaces and/or replace view initializers/component callbacks. They are reference material, not a production owner to revive.
6. `invite_policy_engine` is already the authoritative delete-decision owner for both live and historical invite handling. `scan_channel_invites` is therefore the correct cleanup boundary.
7. The policy engine's protected-target matcher reads all-bot, bot-ID, channel-ID, and protected-poster gate values from the policy settings dictionary.
8. Native `spam_guard._normalize_settings` and its DB payload intentionally do not preserve those target metadata keys. Historical guards worked around that by monkey-patching Spam Guard get/save/normalize behavior.
9. The correct ownership split is therefore: guild config owns Invite Shield target metadata, `invite_policy_engine` consumes it for enforcement, and the Protection Center owns the user-facing picker flow. Spam Guard does not need another patch.
10. The old invite guards still reference each other and a few tests/tools read them as historical artifacts. Deleting that dormant cluster inside this focused runtime migration would broaden scope and can break compatibility sentinels without improving the live execution path.

## Implementation

- Added `stoney_verify/invite_scope_settings.py` as the canonical guild-scoped Invite Shield target-settings service.
- It normalizes canonical and legacy aliases, parses IDs/mentions, saves canonical plus compatibility keys through `upsert_guild_config`, invalidates guild-config and invite-policy caches, and never patches Spam Guard persistence.
- Added an explicit command-bootstrap policy binding that augments `invite_policy_engine.load_invite_policy` with the durable target metadata already present in the guild config object. Actual delete decisions remain inside `invite_policy_engine`.
- Added `stoney_verify/commands_ext/public_protection_invite_ui.py` as the feature-owned Invite Shield UI.
- The existing Protection Center Invite Blocker button still owns its guarded interaction path; command bootstrap redirects the feature-level `_toggle_invite_shield` function to the native editor rather than replacing `ProtectionCenterView.__init__` or a Discord component callback.
- The native editor provides:
  - **Fix This Channel**
  - **Turn Shield On / Off**, preserving the original Invite Blocker toggle semantics
  - **Watch Every Bot**
  - **Choose Watched Channel** through `DankGuildResourceBrowserView`
  - **All Channels**
  - **Advanced IDs** as an explicit fallback for exact bot/user or channel IDs
  - **Clean Existing Invites** through `DankGuildResourceBrowserView`
  - **Back to Protection**
- Watched-channel and cleanup selection re-resolve the selected channel through the live guild before saving or scanning.
- Historical cleanup calls `invite_policy_engine.scan_channel_invites(..., source="protection-center-native-invite-cleanup")`; the UI never performs message deletion directly.
- View and modal error paths send safe replies instead of silently ending in `Interaction failed`.

## Validation

Focused regression coverage now proves:
- legacy scope aliases and Discord mentions normalize correctly
- canonical plus compatibility target keys are persisted together
- policy loading receives durable target metadata without a Spam Guard persistence patch
- the native Invite Shield editor uses buttons rather than raw role/channel entity selectors
- channel discovery/cleanup uses the shared `DankGuildResourceBrowserView`
- cleanup delegates to `scan_channel_invites`
- the original on/off toggle remains reachable from the native editor
- bootstrap does not replace `ProtectionCenterView.__init__` or a component callback

Exact-head GitHub Actions are the release gate. Do not mark PR #242 ready or merge it until the final head has all required checks green and the branch remains 0 behind `main`.

## Cleanup / compatibility

- No new startup guard was added.
- Spam Guard persistence is not patched by the new production path.
- The dormant historical invite-guard cluster is intentionally left inert for this PR because guard-to-guard dependencies and historical compatibility tests still reference those files. The live path does not import them.
- Coordinated deletion of dormant invite-guard artifacts belongs in a separate cleanup unit only after their test/tool references are migrated; deleting files merely to make the tree look cleaner would be the software equivalent of sweeping broken glass under a different rug.

## Backlog

- coordinated retirement of dormant invite-guard artifacts after reference/test migration
- `/dank protection` remaining non-invite picker cleanup
- `/dank design` style/layout/font/separator picker migration
- ticket/member/self-role/welcome/modlog picker migrations in documented order
- admin-only legacy setup picker cleanup

## Next step

Let exact-head CI finish on the final implementation head. Fix only concrete in-scope failures, then perform final scope/drift/review checks, update PR #242 validation metadata without moving the head, mark it ready, merge with the exact validated SHA, and only then release the Single Active Task Lock.
