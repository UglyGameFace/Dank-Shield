# ACTIVE TASK

## DS-AUD-PROTECTION-INVITE-PICKER-OWNERSHIP — Move /dank protection invite targeting out of startup guards

**Outcome target:** The production `/dank protection` Invite Shield / invite hard-block targeting and invite-cleanup channel selection are owned by the canonical protection feature, use the shared Dank picker/resource-browser contract, and no longer depend on startup guards that monkey-patch picker callbacks or `ProtectionCenterView.__init__`. The flow must remain guild-scoped, owner-safe, mobile-usable, searchable/paged where needed, and must answer failures instead of ending in `Interaction failed`.

**Status:** INVESTIGATION / IMPLEMENTATION

**Branch:** `audit/protection-picker-native-ownership`
**Base main:** `c5820840a162803765d50806686f539fb3e388ff`
**Previous integrated task:** PR #241 merged at `c5820840a162803765d50806686f539fb3e388ff`

## Scope

- production `/dank protection` invite-target/scope picker flow
- production Invite Shield cleanup channel picker
- canonical `stoney_verify.commands_ext.public_protection_center` ownership boundary
- shared `DankGuildResourceBrowserView` / `DankPickerView` reuse where discovery or static choices are needed
- removal of picker-specific startup-guard callback / view-init monkey patches once their behavior is native
- preservation of existing invite-policy persistence, allowed/internal invite behavior, target precedence, guarded interactions, and protection-center refresh behavior
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
2. The shared-picker migration contract explicitly lists `/dank protection` invite/link/spam pickers as the next feature surface and calls startup guards that monkey-patch select callbacks an anti-pattern.
3. Canonical `public_protection_center.ProtectionCenterView` owns the public Protection Center but currently does not natively own the full invite target/cleanup picker implementation.
4. `protection_invite_cleanup_picker_guard.py` defines its own paged raw `discord.ui.Select` (`InviteCleanupChannelSelect`) and rewrites `CleanTargetChannelInvites.callback` at startup.
5. `protection_center_invite_controls_guard.py` builds cache-backed bot/channel target selectors inside a startup guard and patches `ProtectionCenterView.__init__` / protection rendering behavior.
6. `spam_guard_invite_scope_pagination_guard.py` independently defines more raw bot/channel selectors for the same invite-scope product surface and depends back on `protection_center_invite_controls_guard` for refresh behavior.
7. `protection_center_invite_simple_flow_guard.py` also patches the Protection Center / invite-scope flow, creating stacked runtime ownership for one user-facing path.
8. The existing shared resource browser already solves the channel-side discovery/search/paging problem. The protection feature should consume it rather than retaining parallel picker engines in startup guards.

## Implementation direction

- Trace the exact live invite flow after all startup guards apply and preserve only behavior that is actually reachable.
- Move invite target/scope and cleanup UI into the canonical protection feature (or a protection-owned helper imported explicitly by it), not another startup guard.
- Use `DankGuildResourceBrowserView` for text-channel discovery/cleanup targets.
- Use the shared picker contract for finite invite-scope choices and any bot/user target browser that remains necessary; keep manual ID entry only as an advanced fallback.
- Re-resolve selected channels/members from the live guild immediately before save/action.
- Keep invite-policy persistence through the existing authoritative services; do not create a second writer.
- Remove picker-specific startup-guard registrations/patches only after equivalent native behavior and regression coverage exist.

## Validation plan

- prove the production Protection Center reaches the native invite picker path without importing a picker implementation from startup guards
- prove invite cleanup no longer uses a raw `discord.ui.Select` startup-guard picker
- prove target/channel discovery is paged/searchable and owner-locked
- prove channel selections are guild-local and re-resolved before cleanup/save
- prove manual ID fallback still parses mentions/IDs where advanced targeting needs it
- prove existing allowed/internal invite semantics and invite hard-block target precedence are unchanged
- prove wrong-owner and callback failures produce a safe response
- run the full exact-head PR workflow suite before merge-readiness is claimed

## Cleanup / compatibility

- Do not add another startup guard or import-time monkey patch.
- Do not fork spam/invite persistence logic into the UI layer.
- Remove only startup-guard picker ownership that has been replaced natively; unrelated compatibility guards stay untouched in this task.
- Record any guard-to-guard dependency that blocks safe deletion instead of hiding it.

## Backlog

- `/dank protection` remaining non-invite picker cleanup if any is left after this task
- `/dank design` style/layout/font/separator picker migration
- ticket/member/self-role/welcome/modlog picker migrations in documented order
- admin-only legacy setup picker cleanup

## Next step

Trace the final runtime class/callback chain across the Protection Center invite guards, identify the smallest native ownership boundary that replaces all duplicate invite target/cleanup pickers, implement it using the shared picker/resource browser, remove superseded guard registration, add focused regressions, then open a draft PR and run exact-head CI.
