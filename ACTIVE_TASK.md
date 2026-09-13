# ACTIVE TASK

## DS-CLEANUP-001 — Canonical schema authority consolidation

**Status:** IN PROGRESS — implementation branch opened, validation pending
**Branch:** `cleanup/schema-authority-consolidation`
**Base:** `91bce1e3a72b16e079f99febb6519b3144c36955` (`main`)
**PR:** not opened yet

## Outcome

Make committed Supabase migrations the sole authority for schema mutation. Runtime startup may inspect schema readiness and report precise migration guidance, but it must not create/alter tables, indexes, constraints, grants, RLS policies, or execute migration SQL.

## Scope

- consolidate startup schema readiness onto one read-only health path;
- remove runtime DDL/direct-Postgres migration execution from startup guards;
- remove the ticket-category guard that mutates another guard's private migration list;
- preserve useful startup diagnostics and migration-path guidance;
- move schema assertions to the committed migration files that actually own those invariants;
- update directly affected startup/audit/tests/workflows only where required by the authority change.

## Findings / root cause

- `stoney_verify/startup_guards/auto_schema_bootstrap.py` embeds schema DDL and executes selected committed migrations at runtime when a direct DSN is present.
- `stoney_verify/startup_guards/operation_queue_schema_guard.py` independently embeds operation-queue DDL/security SQL and executes it at runtime.
- `stoney_verify/startup_guards/ticket_category_schema_bootstrap_guard.py` mutates `auto_schema_bootstrap._BOOTSTRAP_MIGRATION_FILES` at import time.
- committed files under `supabase/migrations/` already represent the deployable schema chain, and CI has a Supabase migration deployment/dry-run path.
- the repository already contains a read-only REST schema health mechanism, so startup mutation is unnecessary duplicate authority.

## Execution path

`main.py` / application startup -> startup guard loader -> schema bootstrap guards -> `on_ready` listeners -> runtime DDL/direct migration execution when a database DSN is present.

Target path:

application startup -> one read-only schema health guard -> Supabase REST probes -> actionable migration guidance; schema mutation remains exclusively in `supabase/migrations/` and migration deployment tooling.

## Planned changes

- replace runtime bootstrap ownership with read-only schema health probes;
- remove obsolete bootstrap-registration guard from startup loading;
- retire duplicate operation-queue DDL path;
- update ticket/counter/schema tests so migration SQL, not Python bootstrap strings, is the asserted authority;
- add regression coverage forbidding runtime DDL/migration execution from startup guards.

## Validation required

- targeted schema-authority regression tests;
- full `tests/` suite;
- all `tools/test_*.py` checks;
- Python compile checks;
- migration-version / SQL workflow checks applicable to touched migrations and tests;
- `git diff --check` equivalent through CI;
- exact-head GitHub Actions green before completion claim.

## Cleanup / conflicts

- stale AntiNuke task record replaced; AntiNuke implementation itself is out of scope.
- unrelated audit findings remain backlogged and are not to be mixed into this PR.

## Backlog (not active)

Remaining master-audit cleanup areas, including guild re-invite state continuity, authorization boundaries, protection-role authority, outage semantics, verification durability, role discovery, ticket compatibility/UX, and broader startup monkey-patch reduction. These will be taken one completed task at a time.

## Definition of done

- no startup path in the affected schema area performs DDL or executes migration SQL;
- migrations are the single schema mutation authority;
- runtime health diagnostics remain actionable and non-destructive;
- affected callers/tests/tooling are reconciled without duplicate compatibility implementations;
- exact-head targeted/full CI is green;
- final diff contains no unrelated changes or temporary/debug code;
- remaining risks or unvalidated deployment assumptions are recorded before merge.
