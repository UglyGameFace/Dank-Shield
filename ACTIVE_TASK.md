# ACTIVE TASK

## DS-AUD-PROTECTION-INVITE-GUARD-RETIREMENT — Retire dormant Invite Shield startup-guard ownership

**Outcome target:** Remove or neutralize the dormant startup-guard implementations that still duplicate the now-native `/dank protection` Invite Shield targeting and historical cleanup UI, while preserving any compatibility helpers that are still legitimately referenced by tests/tools or non-picker runtime code. The repository must have one clear production owner for Invite Shield UI and target persistence, with no dead guard chain waiting to be accidentally reactivated later.

**Status:** INVESTIGATION

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

## Starting evidence

1. PR #242 moved production Invite Shield targeting and cleanup UX into `stoney_verify.commands_ext.public_protection_invite_ui` and durable target metadata into `stoney_verify.invite_scope_settings`.
2. `commands.py` explicitly installs those canonical owners during normal boot.
3. Exact-head PR #242 validation passed all six pull-request workflows before merge.
4. The old invite picker implementation is spread across historical startup guards, including `protection_invite_cleanup_picker_guard`, `protection_center_invite_controls_guard`, `spam_guard_invite_scope_pagination_guard`, and `protection_center_invite_simple_flow_guard`.
5. Those historical modules reference one another and some tests/tools inspect them as compatibility artifacts, so deletion must be coordinated rather than performed file-by-file on vibes, humanity's favorite dependency-management strategy.
6. The bulk startup-guard list is historical metadata and is not the normal production owner, but leaving obsolete UI owners in that inventory still creates future reactivation risk and architectural ambiguity.

## Investigation plan

- map every default-branch reference to the old Invite Shield picker/Protection Center guard cluster
- separate runtime imports from tests/tools/docs/historical metadata
- identify any helper behavior in the old guards that is still consumed outside the retired picker path
- remove or replace test/tool references that merely validate historical guard implementations
- delete only files that have no remaining legitimate consumer after migration
- update startup-guard historical metadata/audit docs when retired files leave the tree
- add regressions proving normal boot uses only the PR #242 native UI/persistence path and does not import retired invite picker guards

## Validation gate

- exact final branch must remain 0 behind `main`
- focused retirement tests/static audits must pass
- full required GitHub Actions must pass on the exact final head
- no submitted review or unresolved inline review thread may be ignored
- no merge until the final head is validated and scope-clean

## Cleanup / compatibility

- Do not change Invite Shield product behavior merely to make deletion easier.
- Do not revive the bulk startup-guard installer.
- Do not move dead guard behavior into a new compatibility wrapper unless a concrete consumer requires it.
- If a legacy helper is still genuinely needed, move that helper to a canonical non-guard module and migrate the consumer before deleting the old file.

## Backlog

- `/dank protection` remaining non-invite picker cleanup
- `/dank design` style/layout/font/separator picker migration
- ticket/member/self-role/welcome/modlog picker migrations in documented order
- admin-only legacy setup picker cleanup

## Next step

Map the old Invite Shield guard cluster and all remaining references on `main`, classify each reference as runtime/test/tool/doc/metadata, then retire the smallest safe set without changing the native PR #242 behavior.