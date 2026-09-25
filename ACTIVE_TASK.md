# Active Task

## Active task / desired outcome

**P0-ACCESS-REPAIR-SELF-LOCKOUT-006 — make Fix Access parent-aware, preventative, and truthful across categories/channels**

Desired outcome: Diagnostics, Setup repair, and Specific Channel repair should use
the parent category's known Dank Shield overwrite as a bot-only template when
Discord still permits the write, preserve unrelated member/role permissions, and
prevent future self-lockouts by retaining Dank Shield's own Manage Permissions
authority. If a target has already removed effective Manage Permissions, the UI
must explain the exact Discord-side manual fix instead of claiming the bot can
bypass Discord's permission gate.

## Scope / single active task lock

Only the access-repair self-lockout path is active:

- explain why a correct category can coexist with an inaccessible unsynced child;
- reuse a parent category's Dank Shield overwrite only for Dank Shield, never as
  a full category sync;
- automatically repair targets only while Discord grants effective Manage
  Permissions;
- retain Dank Shield's own Manage Permissions bit on repaired/managed targets to
  reduce future circular lockouts;
- keep explicit bot-member denies fail-closed in safe repair;
- make Diagnostics bulk repair and Specific Channel report the same truth;
- add regression coverage in existing test modules rather than another one-off
  test file.

Do not broaden into verification interaction failures, ticket redesign, AntiNuke,
or general test-suite consolidation unless required by this root cause.

## Status

**MERGED + REPOSITORY VALIDATED — production Discord acceptance pending**

Implementation PR: **#319 — Make bot access repair parent-aware and self-lockout safe**

Validated PR head:
`1676e342f70d27900d589db68b924c63cefb34aa`

Merged to `main` as:
`967998446982c706d67645861fd8e7fdce4d1dbe`

The merge commit has no file-content differences from the validated PR head.

## Findings / root cause

1. Discord channels can be synced or unsynced from their parent category. The
   reported case has a correct Dank Shield member overwrite on the category and
   no equivalent Dank Shield overwrite on the unsynced child.
2. A full Discord Sync Now operation makes the child's complete permission set
   match the category. That is too broad for automatic repair because intentional
   channel-specific role/member permissions could be lost.
3. Activity repair previously started only from
   `channel.overwrites_for(Dank Shield)` and added View Channel / Read Message
   History / Manage Threads. It did not use an otherwise-empty child's parent
   category as a bot-only template.
4. Setup/Diagnostics and Specific Channel both correctly stop when effective
   Manage Roles/Manage Permissions is missing on the target, but their message
   did not recognize that a parent category might already contain the correct
   Dank Shield permission.
5. The first implementation attempt tried to escape the self-lockout by calling
   channel `edit(overwrites=...)` with Manage Channels. Current Discord API
   documentation explicitly states that modifying permission overwrites through
   Modify Channel also requires Manage Roles. That attempted bypass was invalid
   and has been removed before merge.
6. Therefore a true target-level Manage Permissions self-lockout cannot be
   repaired by the bot itself with `set_permissions`, a bulk overwrite edit,
   or category sync. The owner must restore that permission once. The bot can
   then perform the remaining scoped repair.
7. Prevention is possible: while effective Manage Permissions still exists,
   Dank Shield can persist `manage_roles=True` on its own channel/category
   overwrite. Managed setup baselines should do this so later @everyone/role
   drift is less likely to cut off repair authority.

## Execution path

Diagnostics activity repair:

`/dank diagnostics`
→ Repair Bot Access
→ authoritative activity-scope gaps
→ build exact affected channel targets
→ if the child has no Dank Shield overwrite, seed only that bot overwrite from
  the parent category
→ add only required activity permissions and retained repair authority
→ check the real Manage Permissions mutation prerequisite
→ apply with `set_permissions` only when Discord allows it
→ otherwise show the exact parent-aware manual handoff
→ rerun Diagnostics after repair.

Specific Channel repair:

Fix Access → Specific Channel
→ audit target
→ use the same shared Manage Permissions prerequisite
→ when repairable and the child has no bot overwrite, seed Dank Shield's bot-only
  overwrite from the parent category before adding selected missing permissions
→ persist Manage Permissions while it is still effective
→ preserve explicit denies unless the separate Resolve Explicit Denies flow is
  confirmed
→ when already self-locked, keep the repair button manual-only and explain why
  Discord prevents the bot from applying even the known-good parent template.

## Changes

- Removed the invalid channel-edit self-lockout bypass before merge.
- Added shared parent-category bot-overwrite discovery and bot-only seeding in
  `permission_repair_core.py`.
- A parent template is used only when the child has no explicit Dank Shield
  overwrite; an existing child bot overwrite remains authoritative.
- Specific Channel repair can seed the one Dank Shield overwrite from the parent
  while Discord still grants Manage Permissions.
- True self-lockouts remain blocked, but the message now detects a parent that
  already grants Dank Shield Manage Permissions and explains:
  - the child is unsynced/not inheriting the category;
  - Discord will not let the bot edit or sync permission overwrites without the
    permission it has already lost;
  - add Dank Shield → Manage Permissions on the child, or use Sync Now only when
    the owner intentionally wants the child's entire permission set to match the
    category.
- Activity repair seeds an empty child bot overwrite from its parent, preserves
  explicit bot denies, and persists Manage Permissions when currently effective.
- Managed setup permission baselines retain Manage Roles/Manage Permissions for
  Dank Shield on public, staff, posting, and voice-verification targets.
- Full-control selected-target repair includes Manage Roles.
- Regression coverage stays in existing access/activity test modules; no new
  test file was created.

## Validation required / results

Exact-head validation on
`1676e342f70d27900d589db68b924c63cefb34aa` passed before merge:

- all **7 GitHub workflow groups passed**;
- committed diff whitespace check passed;
- Python compile passed;
- full unit suite passed: **1993 passed, 9 warnings**;
- standalone tool checks passed;
- Public Setup audit passed;
- canonical public command surface audit passed;
- public command/startup friction audit passed;
- public invite/permissions audit passed;
- Setup Safety audit passed;
- Dank Design Smart Auto-Detect audit passed;
- Role Truth audit passed;
- Event Boundary audit passed;
- Claim-first ticket security passed;
- Managed category SQL smoke test passed;
- Schema Authority SQL passed;
- Application Command Size Diagnostics passed;
- Profile Runtime Diagnostics passed;
- Ticket Owner Emergency Override passed;
- DS Backlog 027 Validation passed;
- no automatic full category sync was introduced;
- the invalid overwrite-bulk-edit bypass was removed before the validated head;
- regression coverage verifies parent-aware/manual handoff and bot-only seeding
  while repair authority still exists.

Post-merge repository verification:

- PR #319 is merged;
- `main` is exactly merge commit
  `967998446982c706d67645861fd8e7fdce4d1dbe`;
- comparing the validated PR head to the merge commit shows **zero changed files**.

Only live Discord production acceptance remains.

## Cleanup / conflicts

- PR #315 is confirmed merged and all six workflow groups on its head passed, so
  the previous lifecycle task was closed before this task began.
- No open PR existed when this branch was created.
- No full category sync is introduced.
- No second permission-repair owner is introduced; Setup, Diagnostics, and
  Specific Channel continue through the shared repair core/prerequisite.
- The invalid self-lockout bootstrap code and its tests were removed before
  validation of the corrected head.
- Existing unrelated server/channel overwrites remain authoritative.

## Blockers / risks

- Discord itself is the hard blocker once effective Manage Permissions is lost
  on a target. The bot token cannot use the server owner's interaction
  permissions to bypass that API requirement.
- A one-time manual correction is therefore unavoidable for targets already in
  that state unless the bot is granted Administrator, which this project does
  not request as its normal public permission model.
- Live Discord propagation still needs production acceptance after deploy.

## Backlog

- Test-suite organization/consolidation remains separate. The repository has
  hundreds of valid regression files, but reorganizing them must not be mixed
  into this production permission repair.
- Verification/ticket interaction failures remain separate unless validation
  proves this access root cause directly controls them.

## Next step

Deploy/restart the merged `main` build, then run the production acceptance
sequence on one known affected unsynced child such as the reported
`#general` under its correct parent category:

1. Run Diagnostics / Repair Bot Access before changing Discord manually and
   confirm it recognizes the parent category state instead of pretending the
   child is safely auto-fixable.
2. If the child is already self-locked, restore only Dank Shield → Manage
   Permissions on that child, not a full Sync Now unless the entire child
   permission set is intentionally meant to match the category.
3. Rerun Fix Access and confirm the remaining bot-only permissions repair.
4. Reopen the child's Discord permission screen and confirm unrelated role/member
   overwrites were preserved.
5. Rerun Diagnostics and confirm that target no longer appears as an access gap.

After those live checks pass, mark this task complete. Do not start the separate
test-suite consolidation or verification/ticket work before this acceptance is
recorded.

## Production acceptance after deploy

1. An unsynced child with no Dank Shield overwrite and still-valid Manage
   Permissions can use the parent category's Dank Shield overwrite as a bot-only
   template without changing any other role/member permission.
2. Repair writes retain Dank Shield's Manage Permissions authority where that
   authority is currently effective.
3. A child already self-locked by missing Manage Permissions is not falsely
   reported as auto-fixable.
4. If that child's parent category already has the correct Dank Shield
   permission, Diagnostics and Specific Channel say so and give the exact
   minimal manual recovery path.
5. Sync Now is presented only as an intentional full-category-permission choice,
   never as an automatic bot repair.
6. Explicit bot-member denies remain untouched by safe repair until the existing
   confirmation flow is used.
7. Re-running repair after the one-time manual permission restore completes the
   remaining scoped bot access repair.
