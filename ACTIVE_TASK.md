# ACTIVE TASK

## DS-AUD-RUNTIME-SAFETY — Retire temporary runtime monkey-patcher into canonical owners

**Status:** CLOSED — implementation, merge, canonical CI, and production promotion passed
**Implementation PR:** #223 — `Retire temporary runtime safety import hooks`
**Validated final PR head:** `d3c5a75ef3b19882d9c62447ef14e73ec6612410`
**Canonical implementation merge:** `2726f33699d2e73c82c90c49f131ce1105fd19b9`

## Outcome

The temporary `startup_guards.runtime_safety` monkey-patcher and its transitive `public_startup_scope` import hook are retired. Runtime behavior now matches the canonical source that CI inspects instead of being replaced after import by hidden global hooks.

The most serious correctness conflict is removed: the retired patcher can no longer replace the database-authoritative persistent ticket-number allocator with older channel/DB-max scanning logic.

The one behavior that required native migration, `/identity_truth`, now performs the real synchronous truth lookup off the Discord event loop with `asyncio.to_thread` rather than relying on a runtime patch that returned an empty result while a loop was running.

## Final implementation scope

- Removed `sitecustomize.py` loading/calling `runtime_safety` while preserving Basic Verify compatibility.
- Deleted `stoney_verify/startup_guards/runtime_safety.py`.
- Deleted `stoney_verify/startup_guards/public_startup_scope.py`.
- Removed both retired modules from startup diagnostics and the inert historical startup inventory.
- Migrated `/identity_truth` to off-thread execution of the canonical truth lookup.
- Preserved canonical `tickets_new.service` unchanged, including its durable database-authoritative persistent ticket allocator.
- Retired redundant/unsafe RaidGuard, ticket-timeout, voice-modlog, startup-maintenance, and public-startup-scope patch behavior instead of rebuilding it.
- Added focused behavioral regression coverage and `docs/RUNTIME_SAFETY_NATIVE_OWNERSHIP_AUDIT.md`.
- Updated `CLAUDE.md` so the retired import hooks are not restored as supposed runtime owners.
- Left `main.py`, `app.py`, events, modlog, ticket service, schema, AntiNuke, persistent views, and `process_health` unchanged.

## Exact-head pre-merge validation

Final frozen PR head `d3c5a75ef3b19882d9c62447ef14e73ec6612410` passed the complete gate:

- Dank Shield CI #2115 / `34876108367` — success.
  - committed diff whitespace — success.
  - Python compile — success.
  - full repository unit suite — success.
  - standalone tool checks — success.
  - public setup/isolation audit — success.
  - canonical public command-surface audit — success.
  - public command/startup-friction audit — success.
  - public invite permissions audit — success.
  - setup safety audit — success.
  - Dank Design Smart Auto-Detect audit — success.
  - role-truth ownership audit — success.
  - event-boundary ownership audit — success.
  - Claim-first ticket security — success.
  - Managed category SQL smoke test — success.
- Profile Runtime Diagnostics #885 / `34876108372` — success.
- Ticket Category Menu Sanity #515 / `34876108365` — success.
- Schema Authority SQL #44 / `34876108388` — success.
- Application Command Size Diagnostics #1129 / `34876108368` — success.
- Ticket Owner Emergency Override #686 / `34876108423` — success.
- Dank Design Regression CI #379 / `34876108336` — success.
- No unresolved review threads; PR scope remained the expected 10 files; canonical `main` had not drifted before merge.

## Post-merge production acceptance

PR #223 merged through protected `main` as `2726f33699d2e73c82c90c49f131ce1105fd19b9`.

Acceptance on that exact canonical SHA passed:

- Dank Shield CI #2116 / `34877361979` — success.
  - committed diff whitespace — success.
  - Python compile — success.
  - full repository unit suite — success.
  - standalone tool checks — success.
  - public setup/isolation audit — success.
  - canonical public command-surface audit — success.
  - public command/startup-friction audit — success.
  - public invite permissions audit — success.
  - setup safety audit — success.
  - Dank Design Smart Auto-Detect audit — success.
  - role-truth ownership audit — success.
  - event-boundary ownership audit — success.
  - Claim-first ticket security — success.
  - Managed category SQL smoke test — success.
- Ticket Owner Emergency Override #687 / `34877361932` — success on the same SHA.
- Schema Authority SQL #45 / `34877361931` — success on the same SHA.
- Ticket Category Menu Sanity #516 / `34877362002` — success on the same SHA.
- Deploy Supabase migrations #26 / `34878587626` — success on the same SHA.
  - canonical CI #2116 completed successfully at 2026-09-14T18:04:33Z.
  - production promotion started afterward at 2026-09-14T18:04:35Z.
  - checkout of validated release commit — success.
  - immutable current-main target verification — success.
  - required secrets / Supabase CLI / production project link — success.
  - migration status — success.
  - migration preview — success.
  - migration apply — success.
- Canonical `main` was re-fetched after production promotion and remained `2726f33699d2e73c82c90c49f131ce1105fd19b9`.

The runtime-safety/import-hook finding is therefore closed. Do not reopen it without new regression evidence.

## Remaining master-audit backlog

These remain separate findings and were not pulled into this repair:

- `process_health` package-level process/signal/import-health ownership and other explicitly owned legacy patch/wrapper behavior;
- dormant startup-guard file deletion/consolidation, only after individual importer/supersession proof;
- dual/dead implementation trees such as `commands_new`, `db_new`, `tasks_new`, `core/`, and other parallel implementations;
- remaining stale Channel Builder runbook/workflow references, if current repository evidence still shows any;
- DS-SEC-044 hostile re-entry production acceptance remains separately suspended and must not be resumed without explicit authorization.

## Next step

Merge this bookkeeping-only closeout through protected `main`, verify canonical post-closeout CI and migration promotion on the exact merge SHA, then select the next unresolved master-audit finding from current repository evidence and create a fresh active-task record from that exact canonical main. Do not choose the next finding from stale summaries alone.
