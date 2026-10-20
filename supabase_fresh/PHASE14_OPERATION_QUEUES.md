# Phase 14: backend-only operation and dashboard command queues

**Sole task:** complete Dank Shield dedicated Supabase migration. The shared original database `hyncpbvqyjojxhjzcdzg` remains live. Offline candidate `viamriafkbqhquvrsnua` is the ONLY target for this schema.

## Source-confirmed need and safety
Read-only source inspection on 2026-10-20 found `bot_operation_jobs` has 531 terminal rows: 498 succeeded, 27 cancelled, 3 failed, 3 expired; no current job leases. `bot_commands` has 50 completed rows and zero pending actions. The destination MUST NOT replay these old Discord operations. **No historical queue rows are imported by this migration.** The old source still holds their audit history. At final cutover, recheck active queue state and external submission/worker authority so old actions aren't submitted a second time.

## Runtime schema
`202610200023_dankshield_operation_queues.sql` includes the canonical existing base and security-hardening operation queue migrations exactly, including tenant-scoped unique `(guild_id,idempotency_key)`, status/source/risk checks, active recovery indexes and backend `service_role` privileges.

`bot_commands` restores the source-confirmed 11 columns, UUID PK, four allowed statuses `pending/processing/completed/failed`, and a composite `(guild_id,status,created_at)` worker-poll index. The source project had `bot_commands` RLS **disabled** with anon/authenticated access. The candidate intentionally fixes this: both queue tables have RLS enabled, no anonymous or authenticated table CRUD, only backend service_role CRUD and no TRUNCATE grant. No production access or original source permissions were changed.

## Verified installation
The exact committed migration plus rollback-only smoke passed in one offline candidate transaction. Then `202610200023_dankshield_operation_queues` was successfully applied to dedicated Supabase, and the smoke passed again. A final read confirmed **73 public tables**, `bot_operation_jobs=0`, `bot_commands=0`; the preexisting 4 sticky configurations and 3 quiet notices remain intact. The smoke inserts only synthetic IDs under BEGIN/ROLLBACK and tests guild isolated command status transitions, operation-job idempotency, private grants, absent historical replay and state transitions. Dedicated PostgreSQL 16 CI reruns these checks without production credentials.

## Still blocked before production switch
The original database still contains 3,369 staff-action moderation logs (>90 days old), 4,756 activity feed rows and 6,629 invite-block events needing deliberate retention or private archival, plus 7 other source-only tables needing ownership and runtime classification. Old `verification_tokens` 210/210 expired and must NOT be carried into live security state. Three voice-verification sessions remain pending/accepted and old Cinema session progress requires safe lifecycle handling. Source-to-target late-delta checks, independent backup and off-host restore, canary for normal 5–30 guilds, capacity monitoring, approval for production Discloud database reconfiguration and guarded branch retirement remain mandatory.

Neither 500K server capacity nor 100% cutover readiness is established by creating empty queue tables. Do not put the target Postgres URL in Discloud until the coordinated cutover.
