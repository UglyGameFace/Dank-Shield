# ACTIVE TASK

## DS-AUD-RUNTIME-SAFETY — Retire temporary runtime monkey-patcher into canonical owners

**Status:** IMPLEMENTATION COMPLETE — final exact-head revalidation required
**Branch:** `audit/runtime-safety-native-ownership`
**Base / canonical main at task start:** `0d6287d67b4a28ab4f08fdbbdf9811ec16f263c9`
**Implementation PR:** #223 — `Retire temporary runtime safety import hooks`

## Previous finding closure

The prior startup-loader/runtime-ownership finding is closed. Its bookkeeping PR #222 merged as `0d6287d67b4a28ab4f08fdbbdf9811ec16f263c9` and passed canonical post-closeout acceptance:

- Dank Shield CI #2113 / `34873092523` — success on the exact merge SHA.
- Ticket Owner Emergency Override #684 / `34873092697` — success on the same SHA.
- Deploy Supabase migrations #25 / `34874281385` — success on the same SHA, triggered only after canonical CI.
- immutable current-main verification, migration status, preview, and apply — success.
- canonical `main` remained `0d6287d67b4a28ab4f08fdbbdf9811ec16f263c9` after promotion.

Do not reopen DS-AUD-STARTUP-OWNERSHIP without new regression evidence.

## Scope

This finding is limited to the live `startup_guards.runtime_safety` host/import-hook subsystem and its transitive `startup_guards.public_startup_scope` hook.

In scope:

- `sitecustomize.py` ownership of `runtime_safety`;
- `stoney_verify/startup_guards/runtime_safety.py`;
- `stoney_verify/startup_guards/public_startup_scope.py`;
- the runtime patch targets in RaidGuard, identity truth, tickets, modlog/voice handling, and app startup maintenance;
- startup diagnostics, historical inventory, tests, and architecture documentation affected by retiring these two explicit startup owners.

Explicitly out of scope:

- `process_health` process/signal/import-health ownership;
- Basic Verify host compatibility in `sitecustomize.py`;
- direct `main.py` command/startup guards;
- unrelated dormant startup-guard families;
- AntiNuke / DS-SEC-044;
- schema changes;
- persistent-view ownership.

## Root cause and final findings

`runtime_safety.py` remained a temporary production monkey-patcher that installed a global `builtins.__import__` hook and replaced live functions after their canonical modules loaded. `public_startup_scope.py` installed a second chained global import hook. That meant reviewed source and static CI were not necessarily the code actually running.

The most serious conflict was ticket numbering: canonical `tickets_new.service._reserve_next_ticket_number()` delegates to the durable database-authoritative persistent allocator, while `runtime_safety` replaced it at runtime with older channel/DB-max scanning logic. The patcher could therefore override a newer correctness invariant after CI had already validated it.

Other patch behavior was traced before removal:

- **RaidGuard:** canonical code already avoids synchronous hard-identity DB work on the running Discord event loop. Runtime patching was redundant.
- **Identity truth:** `get_identity_truth_context()` is synchronous and `/identity_truth` called it from an async command. The runtime patch avoided blocking by returning `{}` on a running loop, silently suppressing real truth data. This required a native migration.
- **Ticket timeout wrappers:** canonical repository/event paths already offload blocking DB work. `asyncio.wait_for` around thread-backed work can report timeout while the underlying operation continues, so those wrappers were unsafe and were not preserved.
- **Voice modlog:** canonical voice logging already uses async Discord APIs. The extra runtime-job replacement was defensive layering, not unique correctness ownership, so it was retired instead of rebuilt.
- **Startup maintenance:** canonical app startup reconciliation/backfill already runs in its own background task over async services. The extra runtime-job replacement was redundant and was retired instead of rebuilt.
- **Public startup scope:** canonical app startup scope plus directly owned `command_scope_dedupe` already owns public command cleanup and safe beta-guild sync defaults. The second import-hook owner was duplicate behavior and was removed.

## Implementation

- Removed `sitecustomize.py` loading/calling `runtime_safety` while preserving Basic Verify compatibility behavior.
- Deleted `stoney_verify/startup_guards/runtime_safety.py`.
- Deleted `stoney_verify/startup_guards/public_startup_scope.py`.
- Removed both retired modules from startup diagnostics and the inert historical startup inventory.
- Migrated `/identity_truth` to await the real synchronous truth lookup through `asyncio.to_thread`, so it remains non-blocking and returns actual truth data.
- Left canonical `tickets_new.service` unchanged, which preserves the database-authoritative persistent ticket allocator instead of reimplementing it.
- Did not add replacement voice/startup queue wrappers after proving canonical async/background ownership was sufficient.
- Left `main.py`, `app.py`, events, modlog, ticket service, schema, AntiNuke, persistent views, and `process_health` unchanged.
- Added focused architecture documentation in `docs/RUNTIME_SAFETY_NATIVE_OWNERSHIP_AUDIT.md`.
- Updated `CLAUDE.md` so the retired import hooks are not treated as live debt to restore.
- Added behavioral regression coverage for host startup, off-thread identity truth execution, persistent ticket allocator delegation, and preservation of `process_health`.

## Validation state

The first frozen implementation head started full validation. Before this bookkeeping correction, the following companion workflows had already passed on that implementation state:

- Application Command Size Diagnostics #1128 — success.
- Ticket Category Menu Sanity #514 — success.
- Ticket Owner Emergency Override #685 — success.
- Dank Design Regression CI #378 — success.
- Schema Authority SQL #43 — success.
- Profile Runtime Diagnostics #884 — success.
- Dank Shield CI #2114 had Claim-first ticket security and Managed category SQL smoke test green and was still running its full unit-suite lane.

This task-file correction changes the PR head. Therefore none of the above is sufficient for merge by itself. The branch must now remain frozen and the complete required gate must pass again on the new exact head.

## Final validation requirements

Before merge, require on one frozen exact PR head:

- targeted runtime-safety/native-ownership behavioral tests;
- persistent ticket counter regression tooling;
- startup diagnostics tests;
- committed diff whitespace check;
- Python compile;
- full repository unit suite;
- standalone `tools/test_*.py` checks;
- public setup/isolation audit;
- canonical public command-surface audit;
- public command/startup-friction audit;
- invite permissions audit;
- setup safety audit;
- Dank Design audit;
- role-truth audit;
- event-boundary audit;
- Claim-first ticket security;
- Managed category SQL smoke test;
- all applicable companion workflows;
- final PR head, file scope, review-thread, mergeability, and canonical-main drift inspection.

## Risks / compatibility checks

- `/identity_truth` must return real proof/manual-link data without blocking Discord's event loop.
- Ticket numbering must remain database-authoritative in actual runtime, not merely in static source inspection.
- Voice modlog and startup maintenance must remain functional through their existing canonical async/background owners after the patcher disappears.
- Public deployments must remain global-command-first with safe beta-guild command behavior.
- Basic Verify host behavior and `process_health` must remain intact.

## Blockers

No code blocker is known. Final exact-head CI/review/merge validation is still required.

## Next step

Freeze the branch at this bookkeeping-corrected head. Do not edit it again unless exact-head validation exposes a real defect. Re-run the complete validation gate, re-fetch canonical `main`, verify PR head/scope/reviews/mergeability, record immutable run evidence in the PR body rather than another branch commit, then mark ready and merge only that exact validated head. After merge, perform canonical-main CI, Ticket Owner, migration-promotion, and main-drift acceptance before closing this finding and selecting the next master-audit item.
