# ACTIVE TASK

## Active task / desired outcome

**P0-INT-001 — Retire dormant global Discord.py interaction patcher**

Remove the obsolete `global_interaction_trace_guard` artifact now that exact boot ownership proves production does not import or apply it and the native interaction service owns the useful failure/duplicate behavior.

## Why this is the active slice

PR #283 completed native Error ID visibility in `/dank diagnostics` and merged as `6c63c45f98c3c6f208fbb0447b767b060cb96031`.

Post-merge verification on `main` confirmed the validated diagnostics/test blobs exactly:

- `public_diagnostics_group.py` → `8910e492f2afc5078acbaeedd94572d7e46fb585`
- `test_public_diagnostics_native_interaction_failures_static.py` → `893dcf65862e12afd52b1925c697d78264a05c30`

The production-readiness ledger still described `global_interaction_trace_guard` as a live framework-patching blocker. Exact current-main ownership inspection disproved that assumption:

- `main.py` explicitly says not to restore global Discord.py monkey patches;
- `sitecustomize.py` owns only the Basic Verify compatibility path and does not import it;
- `usercustomize.py`, `stoney_verify/app.py`, and `stoney_verify/commands.py` do not import it;
- `startup_guards/__init__.py` is non-executable historical metadata and is not iterated by normal boot;
- repo import/reference checks found no production import or `.apply()` owner;
- its private patch markers and legacy trace environment knobs exist only inside the dormant file and the obsolete test that required it.

Keeping a dead module capable of patching `CommandTree._call`, app-command private methods, and `View._scheduled_task` creates resurrection risk with no production benefit.

## Scope

In scope:

- delete `stoney_verify/startup_guards/global_interaction_trace_guard.py`;
- remove its entry from inert `LEGACY_DORMANT_STARTUP_GUARDS` metadata;
- delete `tests/test_global_interaction_trace_guard_static.py`, which required the monkey-patch implementation to exist;
- rewrite `tests/test_global_interaction_trace_loader_static.py` to require the retired module to stay absent from disk, startup metadata, and production boot owners;
- document the corrected runtime ownership and retirement disposition;
- update the P0 interaction readiness ledger.

Out of scope:

- changing `stoney_verify/interaction_guard.py`;
- changing canonical interaction behavior;
- changing ticket/setup/design/verification callbacks;
- retiring any other startup guard;
- changing `interaction_action_lock_guard` or unrelated historical inventory;
- changing command registration or startup order;
- solving remaining direct-command nested-lock architecture in this slice.

## Status

**IMPLEMENTED — dormant patcher deleted; targeted ownership validation pending final PR head**

## Runtime behavior impact

Expected runtime behavior change: **none**.

The deleted module had no production importer/apply path. The task removes dormant code and historical metadata only. Native interaction handling remains feature-owned by `stoney_verify.interaction_guard` and existing public owners.

## Regression rule

The replacement static regression must require:

- `global_interaction_trace_guard.py` does not exist;
- its fully qualified module name is absent from historical startup metadata;
- `main.py`, `sitecustomize.py`, `usercustomize.py`, `stoney_verify/app.py`, and `stoney_verify/commands.py` contain no reference to it;
- native `interaction_guard.py` still owns `run_guarded_interaction`, recent failures, and duplicate-action handling;
- native interaction service contains no `CommandTree._call`, `_invoke_with_namespace`, `_scheduled_task`, or old elite-wrapper markers.

## Previous completed slice

**PR #283 — Show native interaction failures in diagnostics**

- merged as `6c63c45f98c3c6f208fbb0447b767b060cb96031`;
- verified on `main`;
- guild isolation/privacy behavior validated;
- diagnostics production blob: `8910e492f2afc5078acbaeedd94572d7e46fb585`;
- regression-test blob: `893dcf65862e12afd52b1925c697d78264a05c30`.

## Next step

Validate the retirement branch against current `main`, confirm no executable references remain, open a focused PR, record exact-head CI/runner state, merge with an expected-head guard if clean, then verify the retired file remains absent on `main`.
