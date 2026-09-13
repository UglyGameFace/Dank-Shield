# ACTIVE TASK

## DS-CLEANUP-001 — Canonical schema authority consolidation

**Status:** IMPLEMENTATION COMPLETE — final exact-head validation pending
**Branch:** `cleanup/schema-authority-consolidation`
**Base:** `91bce1e3a72b16e079f99febb6519b3144c36955` (`main`)
**PR:** #205 — draft until final exact-head validation is green

## Outcome

Make committed files under `supabase/migrations/` the sole authority for database schema mutation. Runtime startup may inspect schema readiness and report precise migration guidance, but it must not create/alter database objects or execute migration SQL.

## Scope

- remove runtime DDL/direct-Postgres migration execution from schema startup guards;
- preserve read-only runtime readiness diagnostics and migration-path guidance;
- eliminate import-time cross-module migration registration side effects;
- promote bootstrap-only/current application schema contracts into the canonical migration chain;
- remove obsolete standalone schema SQL and direct-DSN runtime configuration/documentation;
- move affected schema assertions to migration-owned tests;
- add SQL/runtime regressions that enforce the new authority boundary.

Broader startup-guard/monkey-patch consolidation is intentionally backlogged. Separate read-only health modules may remain when they own distinct diagnostics, but none may mutate schema.

## Findings / root cause

- `stoney_verify/startup_guards/auto_schema_bootstrap.py` embedded DDL and executed selected migrations at runtime when a direct DSN was present.
- `stoney_verify/startup_guards/operation_queue_schema_guard.py` independently embedded operation-queue DDL/security SQL and executed it at runtime.
- `stoney_verify/startup_guards/ticket_category_schema_bootstrap_guard.py` mutated another startup module's private migration tuple at import time.
- several core/current tables and columns had no complete canonical creation path under `supabase/migrations/`; runtime bootstrap or the standalone `supabase/2026-05-08_runtime_stability_schema.sql` supplied part of that missing authority.
- ticket health/doctor output still advertised the retired direct-DSN auto-repair path.
- `psycopg` was present only to support runtime schema mutation.

## Execution path

Before:

application startup -> schema startup guards -> direct DSN detection -> runtime DDL / selected migration execution -> REST health checks.

After:

migration deployment pipeline -> committed `supabase/migrations/` schema changes;
application startup -> REST/read-only readiness probes -> actionable migration guidance only.

## Changes

- `auto_schema_bootstrap.py` is now read-only and reports migration guidance; `SCHEMA_SQL` remains an empty compatibility symbol.
- `operation_queue_schema_guard.py` is now read-only and reports the committed queue migrations instead of executing DDL.
- `ticket_category_schema_bootstrap_guard.py` is an inert compatibility manifest; it no longer mutates `auto_schema_bootstrap` at import time.
- added `supabase/migrations/20260913154500_canonical_runtime_schema_authority.sql` for previously bootstrap-owned/current runtime contracts.
- added `.github/workflows/schema-authority-sql.yml` covering fresh apply, repeat apply, legacy partial schema, and duplicate historical ticket data.
- removed obsolete `supabase/2026-05-08_runtime_stability_schema.sql`; schema-changing SQL directly under `supabase/` is now regression-tested as forbidden.
- removed the runtime `psycopg` dependency and direct-DSN schema-bootstrap settings/guidance from `.env.example` and production docs.
- removed stale direct-DSN auto-repair advice from canonical ticket-panel health and the doctor compatibility copy.
- converted ticket/schema/counter tests and category audit expectations to the migration-only authority model.

## Validation / results

Green evidence already observed on implementation heads before the final bookkeeping commit:

- Schema Authority SQL: fresh DB + second application + legacy partial schema + duplicate historical ticket data passed;
- Ticket Counter SQL passed;
- Ticket Panel Single Owner passed;
- Ticket Panel Doctor Sanity passed;
- Ticket Category Menu Sanity passed after its obsolete bootstrap-wording assertion was updated;
- Ticket Category Repair SQL passed;
- Ticket Category Rich Recovery SQL passed;
- DS Backlog 027 Validation passed;
- Dank Design Regression CI passed;
- Application Command Size Diagnostics passed;
- Profile Runtime Diagnostics passed on prior implementation head;
- compile/diff checks reached green stages on prior implementation heads.

**Required before completion claim:** all applicable workflows must finish green on the final exact PR head after this task-record update. Prior-head green runs are supporting evidence only.

## Cleanup / conflicts

- no runtime schema guard in this affected area retains direct `psycopg` DDL/migration execution;
- no standalone schema-changing SQL remains directly under `supabase/`;
- direct-DSN schema repair is no longer advertised to operators or ticket-health users;
- category compatibility metadata remains temporarily because category audits/tests import it, but it has no database or cross-module import side effect;
- broader startup monkey-patch removal is outside this task and remains backlogged;
- AntiNuke and all unrelated product behavior remain untouched.

## Blockers / risks

- final exact-head GitHub Actions validation is still pending;
- applying the new canonical migration to production remains a deployment action through the existing Supabase migration pipeline, not something this PR performs from bot runtime;
- no claim of completion or merge readiness until exact-head CI is green and the final PR diff is rechecked.

## Backlog (not active)

Remaining master-audit cleanup areas include guild re-invite state continuity, authorization boundaries, protection-role authority, outage semantics, verification durability, role discovery, ticket compatibility/UX, and broader startup monkey-patch reduction. They remain separate tasks under the single-active-task lock.

## Next step

Validate the final exact PR head, inspect any failures as task regressions until disproven, recheck the complete diff/changed-file scope, update PR #205 validation notes, and only then consider marking the PR ready.

## Definition of done

- no startup path in the affected schema area performs DDL or executes migration SQL;
- `supabase/migrations/` is the sole committed schema-mutation authority;
- runtime health diagnostics remain actionable and non-destructive;
- affected callers/tests/tooling/docs are reconciled without stale direct-DSN repair behavior;
- fresh and legacy migration smoke paths pass;
- exact-head targeted/full CI is green;
- final diff contains no unrelated, temporary, debug, conflict, or secret-bearing changes;
- remaining deployment risks/limitations are recorded before merge.
