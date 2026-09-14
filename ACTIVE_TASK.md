# ACTIVE TASK

## DS-AUD-STARTUP-OWNERSHIP — Retire dead bulk startup loader and document real runtime ownership

**Status:** CLOSED — implementation, merge, and production acceptance passed
**Implementation PR:** #221 — `Retire dormant startup guard bulk loader`
**Validated final PR head:** `a8dc5755195179bd3eb3936d58567b7f9042181c`
**Canonical implementation merge:** `b604de8b20ffb8b4c8a80ad90c054873ccf33c12`

## Outcome

The executable startup-guard bulk loader is retired. Production startup ownership is explicit and auditable, diagnostics are read-only, the proven-dead `usercustomize.py -> panel_menu_retry_guard` path is removed, and verified live startup/feature owners remain intact. Channel Builder remains directly registered through its canonical API server path rather than a startup guard.

## Final implementation scope

- Removed executable `load_startup_guards()` / `load_all_startup_guards()` and loader-only state.
- Retained the 76-module historical list only as inert `LEGACY_DORMANT_STARTUP_GUARDS` metadata.
- Made startup diagnostics observe verified already-loaded owners without importing missing guards.
- Removed obsolete bulk-loader compatibility from `sitecustomize.py`.
- Removed the dead panel retry host hook from `usercustomize.py` and retired `panel_menu_retry_guard.py` after reference verification.
- Updated ownership regressions/audits and documented the live-vs-dormant runtime map.
- Corrected stale Channel Builder architecture documentation.
- Preserved `main.py` boot order, `stoney_verify/app.py` import order, schema authority, AntiNuke behavior, persistent-view ownership, and all verified live owners.

## Exact-head pre-merge validation

Final PR head `a8dc5755195179bd3eb3936d58567b7f9042181c` passed the complete gate:

- Dank Shield CI #2110 / `34866937298` — success.
- Profile Runtime Diagnostics #882 / `34866937192` — success.
- Ticket Category Menu Sanity #511 / `34866937324` — success.
- Schema Authority SQL #41 / `34866937297` — success.
- Application Command Size Diagnostics #1126 / `34866937238` — success.
- Ticket Owner Emergency Override #681 / `34866937354` — success.
- Dank Design Regression CI #376 / `34866937317` — success.
- DS Backlog 027 Validation #77 / `34866937216` — success.
- No unresolved review threads; PR scope remained the expected 13 files; canonical `main` had not drifted before merge.

## Post-merge production acceptance

PR #221 merged through protected `main` as `b604de8b20ffb8b4c8a80ad90c054873ccf33c12`.

Acceptance on that exact canonical SHA passed:

- Dank Shield CI #2111 / `34868701823` — success.
  - committed diff whitespace — success.
  - Python compile — success.
  - full repository unit suite — success.
  - standalone `tools/test_*.py` checks — success.
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
- Ticket Owner Emergency Override #682 / `34868701837` — success on the same SHA.
- DS Backlog 027 Validation #78 / `34868701818` — success on the same SHA.
- Ticket Category Menu Sanity #512 / `34868701946` — success on the same SHA.
- Schema Authority SQL #42 / `34868701834` — success on the same SHA.
- Deploy Supabase migrations #24 / `34869794858` — success on the same SHA.
  - CI #2111 completed successfully at 2026-09-14T16:37:44Z.
  - production promotion started afterward at 2026-09-14T16:37:46Z.
  - immutable current-main target verification — success.
  - required secrets / CLI / production project link — success.
  - migration status — success.
  - migration preview — success.
  - migration apply — success.
- Canonical `main` was re-fetched after production promotion and remained `b604de8b20ffb8b4c8a80ad90c054873ccf33c12`.

The startup-loader/runtime-ownership finding is therefore closed. Do not reopen it without new regression evidence.

## Remaining master-audit backlog

These remain separate findings and were not pulled into this repair:

- live explicitly owned legacy patch/import-hook behavior (`process_health`, `runtime_safety`, `public_startup_scope`, command-tree wrappers, selected feature helpers);
- dormant startup-guard file deletion/consolidation, only after individual importer/supersession proof;
- dual/dead implementation trees such as `commands_new`, `db_new`, `tasks_new`, `core/`, and other parallel implementations;
- any remaining stale Channel Builder runbook/workflow references;
- DS-SEC-044 hostile re-entry production acceptance remains separately suspended.

## Next step

Merge this bookkeeping-only closeout through protected `main`, verify its canonical post-closeout CI/promotion, then select the next unresolved master-audit finding from current repository evidence and create a fresh active-task record from that exact canonical main.