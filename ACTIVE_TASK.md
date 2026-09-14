# ACTIVE TASK

## DS-AUD-RUNTIME-SAFETY — Retire temporary runtime monkey-patcher into canonical owners

**Status:** IN PROGRESS — root cause and live execution path mapped; implementation pending
**Branch:** `audit/runtime-safety-native-ownership`
**Base / canonical main at task start:** `0d6287d67b4a28ab4f08fdbbdf9811ec16f263c9`

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
- the five modules runtime_safety currently mutates: `raidguard`, `identity_proof_service`, `tickets_new.service`, `modlog`, and `app`;
- native owners required to preserve valid queue/off-thread/startup behavior;
- startup diagnostics, tests, and architecture documentation affected by retiring these two explicit startup owners.

Explicitly out of scope for this finding:

- `process_health` process/signal/import-health ownership;
- Basic Verify host compatibility in `sitecustomize.py`;
- direct `main.py` command/startup guards other than documentation/diagnostic consequences;
- dormant startup-guard families unrelated to this importer;
- AntiNuke / DS-SEC-044;
- schema changes;
- command-tree redesign beyond removing the duplicate public-startup-scope patch path.

## Root cause / findings

`runtime_safety.py` describes itself as a temporary production safety layer while canonical modules are refactored, but it still installs a global `builtins.__import__` hook and mutates live modules after import. `public_startup_scope.py` installs a second chained global import hook. This hides runtime behavior from the source files CI and maintainers inspect.

The patch target map is exact:

1. `stoney_verify.raidguard`
2. `stoney_verify.identity_proof_service`
3. `stoney_verify.tickets_new.service`
4. `stoney_verify.modlog`
5. `stoney_verify.app`

### RaidGuard

Canonical `raidguard.py` already refuses synchronous hard-identity DB work on the running Discord event loop and uses async/cache-aware ownership. The runtime patch is now redundant and can be retired rather than preserved.

### Identity truth

`identity_proof_service.get_identity_truth_context()` is synchronous. `commands_ext/identity_admin.py` calls it directly from an async slash-command handler. The runtime patch avoids blocking by returning `{}` whenever an event loop is running, which makes the command safe by silently suppressing the actual identity truth result. The correct owner is an explicit async/off-thread path, not a monkey-patched empty response.

### Ticket service

This is the most serious conflict. Canonical `tickets_new.service._reserve_next_ticket_number()` delegates to the durable database-authoritative `reserve_persistent_ticket_number()` allocator. Permanent regression tooling explicitly requires that path and forbids channel scanning in the service allocator.

At runtime, `runtime_safety` replaces `_reserve_next_ticket_number()` with an older channel/DB-max scan. The import hook therefore overrides a newer correctness invariant after static CI has validated it.

`runtime_safety` also wraps ticket repository/event aliases with `asyncio.wait_for`. Those repository/event functions already offload blocking DB work. Timing out a coroutine that is waiting on `asyncio.to_thread` cannot reliably stop the underlying database call, so returning a synthetic `None`/`False` can allow a write to finish after the caller believes it failed. Those wrappers are not safe ownership and must not be preserved merely because they existed.

### Modlog / voice events

Two runtime_safety targets (`_fetch_member_context_snapshot` and `post_dashboard_mod_action_log`) are not present in the canonical `modlog.py` runtime surface and are dead patch targets.

`maybe_log_voice_state_update` is live. The runtime patch routes it through the bounded `runtime_jobs` queue. That responsiveness behavior is useful, but the correct owner is the canonical voice event boundary in `events.py`, not a replacement function installed by an import hook.

### App startup maintenance

Canonical `app._startup_background_runner()` still runs departed-member reconciliation and startup ticket sync directly in its background task. `runtime_safety` replaces the function with bounded `runtime_jobs` queue ownership. Preserve the queue/backpressure behavior natively in `app.py`, without changing app import order.

### Public startup scope

`public_startup_scope.py` replaces `app._sync_beta_guild_commands_if_requested` through another global import hook. Canonical `app.py` already has native public startup scope and beta-sync behavior, and directly owned `command_scope_dedupe` already performs stale guild-command cleanup for public deployments. The guard is duplicate ownership.

The native app beta-sync helper currently defaults `DANK_SYNC_BETA_GUILD_COMMANDS` to true, while direct `command_scope_dedupe` changes the environment default to false earlier in normal boot. Canonicalize the safe default in `app.py` itself so correct behavior does not depend on a wrapper or environment mutation race.

## Planned implementation

- Remove `sitecustomize.py` runtime_safety import/call while preserving Basic Verify host compatibility.
- Retire `runtime_safety.py` and `public_startup_scope.py` after their required semantics are migrated.
- Add an explicit async/off-thread identity truth API and use it from the async identity admin surface.
- Preserve bounded voice-modlog work in the canonical event owner using `runtime_jobs`.
- Preserve bounded startup maintenance in canonical `app.py` using `runtime_jobs`.
- Keep the durable persistent ticket allocator untouched; remove the runtime override instead of reimplementing it.
- Do not preserve synthetic timeout wrappers that can report failure while a thread-backed DB write continues.
- Make the native app beta-guild sync default fail-safe/off unless explicitly enabled.
- Remove the retired modules from startup diagnostics and update architecture docs/regressions.
- Add regression coverage proving no runtime_safety/public_startup_scope import hook can return and no runtime patch can override persistent ticket numbering.

## Validation plan

Before merge, require on one frozen exact PR head:

- targeted runtime-safety/native-ownership tests;
- persistent ticket counter regression tooling;
- startup diagnostics tests;
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
- applicable companion workflows;
- final diff/file-scope/review/main-drift inspection.

## Risks / compatibility checks

- Voice events must remain non-blocking and queue-bounded after the patcher disappears.
- Startup reconciliation/backfill must remain bounded and observable.
- `/identity truth` must return real proof/manual-link data without blocking Discord's event loop.
- Ticket numbering must remain database-authoritative in actual runtime, not merely in static source inspection.
- Public deployments must remain global-command-first; beta guild sync must remain explicit opt-in.
- Basic Verify host behavior and `process_health` are not to be disturbed.

## Blockers

None identified. Implementation is not yet validated.

## Next step

Implement the native ownership migration on this branch, add focused regression coverage, inspect the complete diff, open a draft PR, and freeze an exact head for the full validation gate. Do not mark ready or merge until that exact head is green.