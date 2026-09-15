# ACTIVE TASK

## DS-AUD-INTERACTION-OWNERSHIP — Retire private View scheduler patch into native interaction ownership

**Status:** IN PROGRESS — root cause proven; implementation branch active
**Branch:** `audit/interaction-lock-native-ownership`
**Base main:** `ca9b831169c9477c62da74d2b808a7bc16cd58da`

## Previous finding closure

`DS-AUD-COMMAND-OWNERSHIP` is closed and must not be reopened without new regression evidence.

Implementation PR #227 merged as `f17ad5d2af0809a9738eb6bbcbf4c295c693ba10` and passed exact-SHA production acceptance. Bookkeeping closeout PR #228 then merged as `ca9b831169c9477c62da74d2b808a7bc16cd58da`.

Post-closeout acceptance on that exact canonical SHA passed:

- Dank Shield CI #2131 / `34912852902` — SUCCESS, including the full unit suite and every standard audit lane.
- Ticket Owner Emergency Override #702 / `34912852978` — SUCCESS on the same SHA.
- Deploy Supabase migrations #31 / `34913644427` — SUCCESS on the same SHA after canonical CI; validated-release checkout, immutable current-main verification, required secrets, Supabase CLI, project link, migration status, preview, and apply all passed.
- Canonical `main` remained exactly `ca9b831169c9477c62da74d2b808a7bc16cd58da` after promotion.

## Finding

Production boot still imports `startup_guards.interaction_action_lock_guard`, which globally replaces private `discord.ui.View._scheduled_task` for every component interaction in the process.

The patch is not the canonical owner of real duplicate protection:

- default mode is `observe`, so duplicate detections normally log and still execute the original callback;
- enforcement requires both block mode and a matching `DANK_SHIELD_INTERACTION_ACTION_LOCK_BLOCK_TARGETS` pattern;
- repository configuration does not configure or advertise those block targets;
- the guard's duplicate/cooldown counters have no consumer outside the guard and its static source-shape test;
- no feature code depends on the `_dank_shield_action_lock_wrapped` marker or saved private scheduler original.

Meanwhile `stoney_verify.interaction_guard` already owns explicit native interaction safety without framework mutation. It provides `asyncio.Lock` action ownership, duplicate in-flight rejection, clear ephemeral busy responses, safe defer/send/follow-up handling, and structured failure diagnostics. Live feature paths already use `run_guarded_interaction(...)`, including tickets, Protection Center, Design, Help, Setup, Diagnostics, and related public UI flows.

## Root cause / old execution path

1. `main.py` imports `interaction_action_lock_guard` as a required startup owner.
2. Import executes `apply()` immediately.
3. `apply()` replaces private `discord.ui.View._scheduled_task` globally.
4. Every view component dispatch enters the compatibility wrapper regardless of whether that feature uses the native interaction owner.
5. The old static test asserts that this private framework patch must continue to exist, locking the repository to discord.py internals rather than user-visible behavior.

This is unnecessary global ownership and a discord.py upgrade risk.

## Intended fix

- remove `interaction_action_lock_guard` from explicit production boot;
- remove it from startup diagnostics and the inert historical startup inventory;
- delete the superseded startup guard module;
- delete the source-shape test that requires the private scheduler patch;
- update the explicit-main startup ownership audit tool;
- preserve `stoney_verify.interaction_guard` and all feature-owned native callers unchanged;
- preserve native ticket close/reopen/delete mutation locks and unrelated compatibility families unchanged;
- add behavior-level proof that the native interaction owner rejects an in-flight duplicate and that importing/using it does not modify `discord.ui.View._scheduled_task`.

## Deliberately unchanged

- ticket lifecycle mutation locking and ticket security;
- feature callback behavior behind the existing menu-first UI;
- Discord API retry/audit-log ownership;
- guild-config/public-env safety ownership;
- AntiNuke, moderation, verification, design, schema, Supabase migrations, command ownership, and DS-SEC-044;
- dormant ticket lock compatibility guards absent separate importer proof.

## Separate follow-up discovered during proof

`stoney_verify.interaction_guard` retains completed `asyncio.Lock` objects in its `_ACTION_LOCKS` mapping. That is real bounded-by-keyspace retention debt, but changing lock lifecycle is not required to retire the global framework patch and has different concurrency semantics when `reject_duplicate=False`. Keep it as a focused follow-up unless implementation evidence shows it is required for this finding. Do not smuggle a concurrency rewrite into the scheduler-patch retirement.

## Validation plan

- prove production boot no longer imports the retired guard;
- prove startup diagnostics no longer expects it;
- prove the old private scheduler marker/module is absent from live ownership;
- prove native duplicate rejection remains behaviorally covered;
- prove native interaction ownership does not mutate `discord.ui.View._scheduled_task`;
- run compile, full pytest, standalone tools, public setup/isolation, command surface/friction, invite/setup safety, Dank Design, role truth, event boundary, Claim-first security, Managed SQL, and every applicable companion workflow on one frozen exact PR head;
- inspect exact file scope, reviews/threads, mergeability, and main drift before merge;
- after merge require canonical main CI and applicable companions on the merge SHA, then gated Supabase promotion on the same SHA after CI success with immutable-main/status/preview/apply checks, and verify main remains unchanged afterward.

## Current blockers

None known.

## Next step

Implement the bounded ownership retirement on this branch, open a draft PR, freeze one exact head, and fix only evidence-backed failures.
