# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Guard direct /verify grant-vr mutation without nested-lock collision**

Move the multi-role `/verify grant-vr` mutation boundary onto the native interaction service while preserving Verification Center delegation and avoiding collision with its already-guarded component boundary.

## Why this is next

PR #284 retired the dormant global interaction framework patcher and merged as `ca473dac5fd87e0c611608f8e9d3f2fe3be666c2`.

Post-merge verification on `main` confirmed:
- `global_interaction_trace_guard.py` is absent;
- its obsolete implementation-preservation test is absent;
- startup historical metadata no longer names it;
- replacement retirement regression blob is `3c7878dd2ad5de4dc16535ac16327b98b5100377`;
- native `interaction_guard.py` remained unchanged at `b8fa9dc52b04d43cfe58f07ee5c69e27fd885bd3`.

The readiness audits also incorrectly described `interaction_action_lock_guard` as live. Current code proves it was already retired before this slice:
- `interaction_action_lock_guard.py` does not exist;
- it is absent from historical startup metadata and `main.py`;
- `tests/test_interaction_native_ownership.py` explicitly requires the retired module to stay absent and verifies importing native `interaction_guard` does not replace `discord.ui.View._scheduled_task`.

The next real native-guard gap is a direct state-changing slash command. `/verify grant-vr` is the highest-risk first slice because it can add Verified and Resident roles, remove Pending, and repair the member's verify UI.

## Nested-lock constraint

Verification Center already calls canonical verify command callbacks through a native guard. The native default action key prioritizes a component `custom_id`, so blindly adding another default guard inside `verify_grant_vr` would reuse the outer component lock and reject itself.

The direct command guard must therefore use an explicit command-scoped lock key distinct from the outer component lock.

## Scope

In scope:
- canonical `verify_grant_vr` callback;
- a thin guarded wrapper around its current business logic;
- an explicit verify-command lock key safe for both slash and Verification Center invocation;
- preserve existing early acknowledgement, staff/guild checks, role resolution/hierarchy checks, specific Forbidden response, ticket/UI repair, and success wording;
- let unexpected mutation exceptions reach native structured Error IDs rather than the current generic `Failed: ...` response;
- focused regression coverage for lock-key separation and business-logic preservation.

Out of scope:
- guarding every `/verify` command in one PR;
- changing role resolution or setup mappings;
- changing Verification Center dispatcher behavior;
- changing Basic Verify or ID Verify mode policy;
- changing ticket/setup/design code;
- changing `interaction_guard.py` global lock semantics in this slice.

## Status

**LOCKED — NOT IMPLEMENTED**

## Recovery rule

Because multiple role mutations can partially succeed before an unexpected exception, failure guidance must not say nothing changed. It must tell staff to reopen Verification Center or run verify status/diagnostics, inspect the member's current roles, and only then retry using the Error ID.

## Previous completed slice

**PR #284 — Retire dormant global interaction patcher**

- merged as `ca473dac5fd87e0c611608f8e9d3f2fe3be666c2`;
- verified on `main`;
- no runtime replacement was needed because the deleted module had no production owner.

## Next step

Inspect the exact `verify_grant_vr` body and Verification Center call path, implement the smallest explicit-lock native wrapper, add focused regression coverage, then validate the exact final head before merge.
