# ACTIVE TASK

## DS-AUD-PROTECTION-INVITE-GUARD-RETIREMENT — Retire dormant Invite Shield startup-guard ownership

**Outcome target:** Remove or neutralize the dormant startup-guard implementations that still duplicate the now-native `/dank protection` Invite Shield targeting and historical cleanup UI, while preserving any compatibility helpers that are still legitimately referenced by tests/tools or non-picker runtime code. The repository must have one clear production owner for Invite Shield UI and target persistence, with no dead guard chain waiting to be accidentally reactivated later.

**Status:** IMPLEMENTATION / VALIDATION

**Branch:** `audit/protection-invite-guard-retirement`
**Base main:** `734916ba151ccc408ce9fa07a4635fd6ecb24776`
**Previous integrated task:** PR #242 merged at `734916ba151ccc408ce9fa07a4635fd6ecb24776`

## Scope

- dormant startup guards that duplicate Invite Shield target/channel picker UI
- dormant startup guards that monkey-patch Protection Center invite callbacks/view constructors solely for that old UI
- historical guard-to-guard references and compatibility tests/tools that keep those files artificially alive
- startup-guard historical metadata entries for retired invite picker owners
- focused regression/static acceptance coverage proving the native PR #242 path remains the only production UI owner
- task/PR bookkeeping

Out of scope unless tracing proves a direct dependency:
- `invite_policy_engine` enforcement semantics
- the new `invite_scope_settings` persistence service
- the new `public_protection_invite_ui` product flow
- generic Spam Guard detection/enforcement behavior
- AntiNuke, tickets, design, members, welcome, modlog, or unrelated setup cleanup
- invite-policy compatibility helpers that still have a real non-picker consumer

## Findings / root cause

1. PR #242 made `public_protection_invite_ui` the production owner for Invite Shield targeting/cleanup and `invite_scope_settings` the owner for target metadata.
2. `commands.py` installs those canonical owners explicitly during normal boot; the historical startup-guard registry is inert metadata.
3. The obsolete Invite Shield UI was not one isolated file. It was a dependency cluster where guards imported and patched one another:
   - `protection_center_invite_simple_flow_guard`
   - `protection_center_invite_controls_guard`
   - `protection_center_invite_status_guard`
   - `spam_guard_invite_scope_pagination_guard`
   - `invite_hard_block_all_bots_controls_guard`
   - `protection_invite_cleanup_picker_guard`
   - `protection_invite_toggle_cleanup_guard`
4. `protection_center_filter_list_guard` secretly chained the retired cleanup picker even though it owns content-filter presentation, not Invite Shield cleanup.
5. `protection_center_clear_categories_guard` imported the old invite controls/status guards and patched their editor in addition to its broader category-wording behavior.
6. Two unrelated retirement tests still read `protection_center_invite_simple_flow_guard.py` as a convenient source file, and `tools/audit_invite_link_safety.py` audited the old cleanup guard rather than the native owner.
7. `spam_guard_invite_override_options`, `protection_invite_target_precedence_guard`, live invite enforcers, and central policy code are not removed in this task because they contain broader policy/compatibility behavior outside the superseded picker/UI chain.

## Implementation

- Deleted the seven obsolete Invite Shield UI/cleanup guard files listed above as one coordinated retirement set.
- Removed `protection_center_invite_controls_guard` and `protection_center_invite_simple_flow_guard` from inert historical startup-guard metadata.
- Refactored `protection_center_clear_categories_guard` so it keeps only general Protection Center category wording and no longer imports or patches a retired Invite Shield editor.
- Refactored `protection_center_filter_list_guard` so it no longer chains Invite Shield cleanup.
- Updated legacy setup/VC retirement tests so they no longer depend on a deleted Invite Shield file.
- Updated `tools/audit_invite_link_safety.py` to audit the native `public_protection_invite_ui` cleanup path and require the retired guard files to remain absent.
- Added `tests/test_protection_invite_guard_retirement.py` to lock file deletion, registry cleanup, surviving-guard decoupling, native boot ownership, shared resource-browser usage, and no raw Discord resource-picker/direct-delete regression.
- Added `docs/PROTECTION_INVITE_GUARD_RETIREMENT.md` as the current disposition record while preserving the older runtime-ownership audit as a historical snapshot of its examined base.

## Validation gate

- exact final branch must remain 0 behind `main`
- focused retirement tests/static audits must pass
- full required GitHub Actions must pass on the exact final head
- no submitted review or unresolved inline review thread may be ignored
- no merge until the final head is validated and scope-clean

## Cleanup / compatibility

- No Invite Shield product behavior was moved back into a startup guard.
- The central invite policy/delete path is unchanged.
- The new native UI/persistence owners from PR #242 are unchanged.
- Broader invite override/precedence compatibility is intentionally left for a separate policy audit rather than being deleted merely because neighboring UI guards were retired.

## Backlog

- `/dank protection` remaining non-invite picker/guard cleanup
- broader invite override/precedence compatibility ownership audit
- `/dank design` style/layout/font/separator picker migration
- ticket/member/self-role/welcome/modlog picker migrations in documented order
- admin-only legacy setup picker cleanup

## Next step

Compare the branch against current `main`, open a draft PR, run exact-head GitHub Actions and focused regressions, fix only concrete in-scope failures, then perform final drift/review cleanup before merge-readiness is claimed.