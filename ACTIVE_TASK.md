# ACTIVE TASK

## DS-AUD-PROTECTION-CONTEXTUAL-REPAIR — Move Protection-owned access onto same-screen repair

**Status:** INVESTIGATION / IMPLEMENTATION

**Branch:** `audit/protection-contextual-repair`
**Base main:** `98290563f7244af198c4384a595cb66cdc76e0e3`
**Previous integrated task:** PR #250 fixed the live contextual permission-repair false failures and is merged/verified on `main` at `98290563f7244af198c4384a595cb66cdc76e0e3`.

## Outcome target

Extend the shared same-screen repair contract into the normal public `/dank protection` experience for exact Protection-owned Discord resources. Safe bot-only access problems should expose `Fix Issues`, re-audit from fresh Discord state, and refresh the same Protection Center. Unsafe/server-level prerequisites remain `Manual Fix Needed`.

## Findings so far

1. Invite Shield public targeting/cleanup already has native feature ownership from PRs #242/#243. Do not re-open or replace that work in this task.
2. AntiNuke permission health is primarily server-level containment/readiness state. Missing server permissions, trust/hierarchy, and Strict Lockdown prerequisites are not channel-overwrite repairs and remain manual.
3. Protection Live Stats is an exact-resource case. `security_stats.py` persists the owned stats category ID in `security_stats_category_id` and exact counter channel IDs in `security_stats_channel_ids`.
4. Live Stats needs Manage Channels to create/rename the display and Manage Roles / Manage Permissions to maintain channel permission overwrites. Those server-level prerequisites remain manual when unavailable.
5. Contextual repair must use only the persisted category/channel IDs. The stats service may retain its own recovery logic for normal product maintenance, but this repair button must not guess replacement resources by name.
6. Shared permission mutation remains owned by `permission_repair_core`; this task must not add feature-local `set_permissions()` calls.

## Scope

- normal public `/dank protection` Protection Center
- enabled Live Stats exact saved category and counter-channel resources
- same-screen `Fix Issues` / `Access Healthy` / `Manual Fix Needed` state
- fresh Discord re-audit after repair
- preservation of AntiNuke manual/server-level blockers
- focused regression coverage and task bookkeeping

Out of scope:
- Invite Shield picker/cleanup redesign or retired guard resurrection
- AntiNuke policy/threshold redesign
- role movement or trust-list changes
- clearing explicit denies automatically
- widening member/@everyone visibility
- changing Live Stats audience-lock semantics
- remaining Protection non-invite picker/guard cleanup, which stays in its later backlog unit

## Safety contract

- exact persisted Protection resources only
- no guessed channels/categories
- no Administrator grant
- no role hierarchy movement
- no @everyone/member/staff visibility widening
- no automatic explicit-deny clearing
- server-level permission/readiness blockers remain manual
- all bot-overwrite mutation stays in the shared permission repair owner

## Validation gate

- prove the production Protection Center composition path
- focused Protection contextual-repair regressions pass
- exact final branch is 0 behind current `main`
- changed-file scope is task-owned only
- full required GitHub Actions pass on exact final head
- no unresolved review/thread issue
- merge only the exact validated head
- verify resulting merge commit as current `main`

## Backlog after this task

1. remaining VC-specific repair cleanup
2. Embed / Status contextual repair adoption
3. admin-only `/dank tickettool-check` contextual repair adoption
4. `/dank protection` remaining non-invite picker/guard cleanup
5. `/dank design` picker migration
6. admin-only legacy setup picker cleanup

## Next step

Implement the Protection Center Live Stats same-screen repair path using exact saved IDs and the shared permission-repair owner, add focused regression coverage, then run exact-head validation.
