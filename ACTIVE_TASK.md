# Active Task

## Active task / desired outcome

**P0-ACCESS-REPAIR-SELF-LOCKOUT-006 — make Fix Access recover bot permission drift across categories/channels without rewriting unrelated access**

Desired outcome: Diagnostics, Setup repair, and Specific Channel repair must safely
restore Dank Shield access when a channel/category has lost the bot's own
Manage Permissions authority. Unsynced child channels should reuse the known-good
Dank Shield permissions from their parent category where safe, while custom
member/staff/channel visibility remains untouched.

## Scope / single active task lock

Only the access-repair self-lockout path is active:

- Diagnose why category permissions can be correct while an unsynced child is
  reported as manually blocked.
- Repair Dank Shield's own overwrite without copying unrelated role/member
  overwrites.
- Reuse safe parent-category Dank Shield allows for unsynced children.
- Apply the same rule to Diagnostics bulk repair and Specific Channel repair.
- Keep explicit bot-member Manage Permissions denies fail-closed.
- Preserve existing setup permission baselines so later repair passes do not
  remove the recovered authority.
- Add regression coverage in existing test modules rather than creating another
  one-off test file.

Do not broaden into verification interaction failures, ticket redesign, AntiNuke,
or general test-suite consolidation unless required by this root cause.

## Status

**IMPLEMENTATION IN PROGRESS — root cause confirmed; bot-only bootstrap implemented; validation pending**

Branch: `fix/access-repair-self-lockout-bootstrap-20260924`

Base: current `main` after merged PR #315
(`c1687c99acc210feda5801e7f51b51781b8cb276`).

## Findings / root cause

1. Discord channels can be unsynced from their parent category. The screenshot
   case has a correct Dank Shield overwrite on the category while the child
   channel has no equivalent Dank Shield entry.
2. Activity repair previously started only from
   `channel.overwrites_for(Dank Shield)` and added View Channel / Read Message
   History / Manage Threads. It did not reuse the parent category's bot
   permissions.
3. Before attempting any write, both Setup/Diagnostics repair and Specific
   Channel repair called `permission_overwrite_edit_blocker`. If effective
   Manage Roles/Manage Permissions was already missing in the child, the repair
   stopped and told the owner to repair Discord manually.
4. That creates a circular failure: the repair refuses to repair the permission
   that is preventing its normal `set_permissions` write.
5. discord.py 2.7.1 documents `set_permissions` as requiring Manage Roles,
   while channel `edit(overwrites=...)` is a Manage Channels operation. When
   Manage Channels is still effective, Dank Shield can safely replace only its
   own member overwrite while preserving the rest of the channel permission map.
6. Blind `sync_permissions=True` is not acceptable here because Discord sync
   makes the entire child permission set match the category and could overwrite
   intentional custom access for members or roles.

## Execution path

Diagnostics activity repair:

`/dank diagnostics`
→ Repair Bot Access
→ authoritative activity-scope gaps
→ build exact affected channel targets
→ detect Manage Permissions self-lockout
→ plan bot-only overwrite bootstrap
→ preserve every existing unrelated overwrite
→ merge safe explicit allows from parent category's Dank Shield overwrite
→ restore Dank Shield Manage Permissions
→ apply required activity access
→ rerun Diagnostics for coverage confirmation.

Specific Channel repair:

Fix Access → Specific Channel
→ audit target
→ if direct `set_permissions` is blocked but safe bot-only bootstrap is
  available, mark target auto-repairable instead of manual-only
→ write the bot-only overwrite through channel edit
→ preserve undo snapshot and normal repair history.

## Changes

- Added a canonical `BotOverwriteBootstrapPlan` in
  `permission_repair_core.py`.
- Added bot-only bootstrap planning that:
  - requires server-level Manage Roles;
  - requires effective Manage Channels on the target;
  - refuses to clear an explicit bot-member Manage Roles deny;
  - preserves every unrelated role/member overwrite;
  - merges only explicit parent-category allows for Dank Shield;
  - restores Dank Shield's own Manage Roles/Manage Permissions bit.
- Specific Channel audits now treat a safely bootstrappable self-lockout as
  repairable instead of disabling the repair button.
- Specific Channel apply uses the safe bulk channel edit when ordinary
  `set_permissions` is circularly blocked.
- Setup/Diagnostics repair uses the same canonical bootstrap instead of emitting
  a manual-action result immediately.
- Activity-repair wording now says the bot overwrite may be safely created, not
  only expanded.
- Managed setup permission baselines now retain Manage Roles for Dank Shield so
  a later repair pass does not strip the recovered repair authority.
- Full-control target repair now includes Manage Roles.
- Regression tests were added to the existing
  `tests/test_access_repair_runtime_consolidation.py`; no new test file was
  created.

## Validation required / results

Pending exact-head validation:

- Python compile for changed modules;
- focused access-repair tests;
- full `pytest tests/` suite;
- standalone tool checks;
- repository audits;
- GitHub workflow groups;
- final diff whitespace/hygiene;
- verify no unrelated permission overwrite is mutated by bootstrap;
- verify explicit bot-member Manage Roles deny stays fail-closed;
- verify branch is current/mergeable before merge.

## Cleanup / conflicts

- PR #315 is confirmed merged and all six workflow groups on its head passed, so
  the previous lifecycle task is closed before this task began.
- No open pull request existed when this branch was created.
- No full category sync is introduced.
- No second permission-repair owner is introduced; both UI paths use the shared
  core bootstrap rule.
- Existing unrelated server/channel overwrites remain authoritative.

## Blockers / risks

- The safe bootstrap depends on effective Manage Channels still being available
  on the target. If both Manage Permissions and Manage Channels are denied, the
  bot cannot safely mutate that target and manual Discord intervention remains
  required.
- Live Discord propagation still needs production acceptance after deploy.

## Backlog

- Test-suite organization/consolidation remains a separate backlog item. The
  repository currently has hundreds of valid regression files, but reorganizing
  them must not be mixed into this production permission repair.
- Verification/ticket interaction failures remain separate unless validation
  proves this access root cause directly controls them.

## Next step

Run focused and full validation on the exact branch head, inspect failures for
real regressions, correct only same-root-cause issues, then open/merge the PR
only after all applicable gates pass.

## Production acceptance after deploy

1. A category with a correct Dank Shield overwrite and an unsynced child missing
   that bot overwrite is repaired without the owner pressing Discord Sync Now.
2. The child receives the required Dank Shield permissions, including repair
   authority, while unrelated role/member overwrites remain byte-for-byte
   equivalent in meaning.
3. Categories or standalone channels with the same self-lockout are repaired
   through the bot-only path when Manage Channels remains available.
4. Explicit bot-member Manage Permissions denies are not silently removed.
5. Specific Channel and Diagnostics report the same repairability decision.
6. Re-running repair does not remove the restored Manage Permissions authority.
7. Targets where Discord truly blocks both repair routes still show a precise
   manual action instead of pretending they were fixed.
