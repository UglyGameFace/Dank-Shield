# Phase 19: dedicated Supabase runtime contracts, pre-cutover (2026-10-21)

**Status: preparation only. No Discloud switch or original source writer freeze.**

Live database identity:
- Original production writer: Supabase `hyncpbvqyjojxhjzcdzg`.
- Dedicated **offline** replacement: Supabase `viamriafkbqhquvrsnua`.
- Discloud production remains on its original database credentials.

## Evidence so far

PR #676 (2026-10-21) was merged into main with four green PR checks. It corrects the final read-only delta audit's handling of three May 2026 VC verification rows: all three have passed their revoke deadlines, have expired linked verification tokens and closed linked tickets. The patched SQL was independently run read-only on the original source, returning 3 historical/expired rows and 0 unresolved voice verification sessions. It preserves every original row. **The final owner-run 34-table hash parity report on this updated code remains to be checked.**

Additional independent read-only metadata queries found:
- All 75 dedicated candidate **public tables** have RLS, backend CRUD privilege, and no direct anon/authenticated SELECT/INSERT/UPDATE/DELETE grant.
- Zero public-schema SECURITY DEFINER functions callable by anon or authenticated roles.
- All eight named critical application PostgreSQL RPC signatures exist with service_role EXECUTE and **without** anon/authenticated EXECUTE: guild CAS patch; member activity batch/ack, record, heartbeat, fail, resume, start.
- Separate earlier read-only source/target catalog and row count comparisons found 34/34 selected table counts equal and identical column definitions except documented safe staff_metrics normalization. Counts alone are **not data hash evidence**.
- At the prior read-only observation both dedicated execution queues were empty.

## New owner-only, read-only preflight

Use `.github/workflows/dankshield-dedicated-runtime-preflight.yml` on **main** when merged. This workflow:
- Requires exact GitHub owner, repository and branch; loads only `DANK_FRESH_DATABASE_URL` from a secret, never source/Discloud/Discord credentials.
- Checks that the connection resolves only to pinned offline Supabase project `viamriafkbqhquvrsnua`.
- Uses a repeatable-read read-only transaction and read-only session settings, requires PostgreSQL 17.
- Checks all public tables for private RLS/grants and service-role CRUD and public SECURITY DEFINER routines for dangerous anon/authenticated access.
- Checks eight critical app RPCs for exact signature and privilege, and requires target-only bot command/operation queues empty.
- Reports only aggregate counts and enumerated failure categories; no table row data, Discord IDs, SQL, connection strings, credentials or source writes.
- Never deploys code, invokes RPCs, starts Discord sessions, alters credentials or approves a cutover. A passing workflow is **DATABASE CONTRACT readiness**, not **PRODUCTION RUNTIME** readiness. It proves network reachability from the GitHub runner, not from Discloud, and cannot validate Supabase REST credentials or the live bot.

## Still required before 100%

1. Owner reruns revised **Final Delta Audit** on main and checks its actual JSON for 34 matching data hashes and zero active work blockers. A prior success on old audit code does not prove the revised output. **Without a writer freeze this remains snapshot only.**
2. Owner runs this runtime contract preflight after merge, and keeps an independent, durable private copy of the successfully verified encrypted candidate backup with its passphrase stored separately. Short-lived GitHub artifacts alone are not backups.
3. Prepare and explicitly authorize a **maintenance window** to quiesce original production writers. Under the freeze, perform a fresh *complete* source/dedicated hash comparison, reconcile any non-matching records by deliberate reviewed migration and rerun until identical. Never try to silently force a snapshot while the bot writes.
4. Verify from the actual Discloud runtime that dedicated Supabase DNS/TLS, REST/service credentials and required app features work under production constraints. GitHub service-role SQL checks do not replace that.
5. Explicit owner authorization for reversible canary of one low-impact guild, monitor tickets, moderation, verification, member updates, Cinema, Community Hub and profiles, and prepare tested rollback retaining the original database and Discloud app backup.
6. Only then explicitly approve the live runtime credentials switch; monitor production and retain old project for rollback. Never delete original source as part of a successful cutover.

The percentage of readiness is necessarily an estimate: source data copy, schema/security, CI and encrypted recovery are largely complete, but cutover-risk gates remain. Do not declare 100% solely from green CI checks.
