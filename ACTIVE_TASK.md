# ACTIVE TASK

## DS-BACKLOG-027 — Finish incorrectly bulk-closed production backlog

**Status:** ACTIVE — IMPLEMENTATION COMPLETE / FINAL VALIDATION
**Branch:** `fix/ds-backlog-integrity-027`
**PR:** #182 (draft)
**Base:** merged `main` at `13759f84ce15ae5dcd7a48e01438d1dc94a1639b` (PR #181)
**Started:** 2026-08-11

## Scope

Finish or correctly supersede the issues that were bulk-closed as completed without enough implementation evidence:

- #119 — one-click channel/category permission repair
- #57 — Channel Builder direct registration + remove compatibility shim
- #56 — global guild operation queue completion/integration
- #20 — production inactive-member cleanup execution
- #11 — TicketTool parity stabilization umbrella completion/supersession
- #2 — invite/join/inviter/approval truth pipeline completion/supersession

Issues #1 and #168 were independently verified as genuinely implemented and are not being reopened or rewritten.

## Rules

- Do not use issue state as implementation evidence.
- Fix owner files/services directly; remove obsolete compatibility shims instead of adding patch-on-patch behavior.
- Preserve the compact `/dank` public command surface and current multi-server isolation.
- No Administrator permission requirement for public installs.
- Dangerous mutations must use shared queue/idempotency or an equivalent scoped exclusive path.
- Definition of Done requires targeted tests, regression/static/compile coverage, cleanup/dead-code/conflict inspection, and exact-final-head CI before merge readiness.

## Implemented

### #57 Channel Builder direct registration
- [x] `api_new/server.py` directly registers Channel Builder routes.
- [x] Execution/preflight lives in canonical `services/channel_builder_execution.py`.
- [x] Apply jobs produce real reverse-order rollback plans.
- [x] Rollback can recover the completed source job through persistent queue storage after restart.
- [x] Removed Channel Builder API injection/runtime-export shims and one-time registration workflow/codemod.
- [x] Updated `tools/audit_channel_builder_queue.py` for the direct architecture.

### #119 Permission repair
- [x] Added exact channel/category selection with feature-specific audit.
- [x] Added Recommended Minimum and Full Dank Shield Control modes without Administrator.
- [x] Safe Fix Access changes only missing permissions on Dank Shield's own overwrite.
- [x] Explicit bot denies are preserved unless separately confirmed.
- [x] Category-child repair is opt-in and previewed.
- [x] Added hierarchy/managed-role/Manage Channels blockers and reauthorization fallback.
- [x] Added before snapshots, audit records, undo token/restore.
- [x] Integrated Specific Channel repair into setup and Fix Channel Access into diagnostics.

### #56 Operation queue
- [x] Persistent job reattach and persistent idempotency lookup.
- [x] Startup reconciliation of stale active jobs.
- [x] Cancellation rules for queued/running jobs.
- [x] Global/per-guild/per-operation backpressure.
- [x] Duration/failure/retry/rate-limit/stale-recovery metrics and health output.
- [x] Discord-aware retry helper for safe individual API calls.
- [x] Structured API ticket/member mutations register directly through canonical queue handlers.
- [x] Channel Builder apply/rollback use canonical queue and retry paths.
- [x] Removed API/persistence/member-cleanup queue import-hook shims.
- [x] Added RLS/service-role-only hardening migration and security-equivalent direct bootstrap.
- [x] Normalized historical queue UUID default so migration/bootstrap contracts match.

### #20 Inactive cleanup
- [x] Preserved conservative scan/review engine and low-confidence protections.
- [x] Actual cleanup execution now directly uses the canonical member-scoped operation queue.
- [x] Final action-time scan/hierarchy/staff/owner/bot/lock safety remains required.
- [x] Saved no-confirm mode actually works only for owner/Admin/configured Bot Manager.
- [x] Bulk queue/purge-all finalize one persisted cleanup-run summary.
- [x] At most one configured Discord modlog/status summary is posted per run.
- [x] Removed member-cleanup queue monkeypatch.

### #2 Join/approval truth
- [x] Invite cache/diff work serialized per guild.
- [x] Invite baseline readiness tracked across warm/detect paths.
- [x] Historical inviter/join field aliases normalized.
- [x] Original join attribution and later verification approval truth are separate.
- [x] Staff approval cannot overwrite a confirmed original invite source.
- [x] Contradictory join evidence is surfaced and downgraded instead of silently trusted.
- [x] Shared member-context reader exposes truth quality/confidence/conflict.

### #11 TicketTool parity umbrella
- [x] Replaced stale May audit with current canonical ownership map.
- [x] Persistent Create Ticket/category/Confirm/Back behavior remains in canonical panel owner.
- [x] Moved stale-menu/duplicate-interaction/confirm/preflight behavior into `public_ticket_panel_clean.py` itself.
- [x] Persistent view is primary owner; fallback listener is only a registration-failure fallback.
- [x] Removed the two runtime callback rewrite guards.
- [x] Updated ticket owner/category/doctor tests and audits so deleted shims are not hidden dependencies.
- [x] Existing sharding/scale/schema/setup ownership linked in `docs/TICKETTOOL_PARITY_AUDIT.md`.

## Validation state

Permanent validation added:

- `tests/test_backlog_027_core.py`
- `tools/test_backlog_027_static.py`
- `.github/workflows/backlog-027-validation.yml`
- updated Channel Builder, ticket-category, ticket-panel-doctor, and ticket-panel-owner audits/workflows
- PostgreSQL 16 apply-twice/RLS/grant/check/unique/recovery-index validation for the operation queue

The first SQL run exposed a real historical mismatch: the June table had no DB UUID default while direct bootstrap did. The hardening migration now normalizes that with `gen_random_uuid()`.

### Remaining Definition-of-Done gates
- [ ] Exact final PR head: DS Backlog 027 Python regressions green.
- [ ] Exact final PR head: operation queue PostgreSQL/RLS job green.
- [ ] Exact final PR head: ticket/category/doctor/Channel Builder focused workflows green.
- [ ] Exact final PR head: full Dank Shield CI unit/static/compile suite green.
- [ ] Exact final PR head: Supabase preview/migration status green.
- [ ] Final stale-reference/dead-file and unresolved-review-thread inspection.
- [ ] Mark PR #182 ready only after every gate above passes.

## Definition of Done

This task is complete only when the six questionable issues are fully implemented or deliberately superseded with exact canonical evidence, obsolete shims are removed, dangerous mutations are protected by canonical queue/idempotency paths, inactive cleanup safely executes rather than only scans, join/approval attribution remains truthful through reconcile paths, permission repair is usable end-to-end, and the exact final PR head passes targeted/regression/static/compile/SQL/Supabase validation with no unresolved review threads.