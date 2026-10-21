# Phase 17: read-only source/dedicated parity and cutover blocker audit

## Scope
Original source project `hyncpbvqyjojxhjzcdzg` is still the ONLY production writer. Dedicated project `viamriafkbqhquvrsnua` is an OFFLINE staging candidate. The user completed the manual activity-feed copy (2026-10-20). Independent source/new Supabase SQL confirmed **4,756 / 4,756** preserved activity feed rows with byte-equivalent full PostgreSQL JSONB digests, including **1,825** old `meta` vs `metadata` differences; **3,369** staff links and **632** member-event links. The original database was not modified.

The source and candidate contain matching important data, but **matching yesterday does NOT certify tomorrow's live cutover**. The new `tools/audit_dankshield_cutover_delta.py` is an explicit OWNER-ONLY, manual, read-only cross-project freshness gate. No production database or Discord credentials are modified.

### Exact read-only audits
- Confirm both credential URLs refer to the PINNED separate Supabase projects using the existing proven validator. Both PostgreSQL connections are transaction-level `REPEATABLE READ, READ ONLY` with `default_transaction_read_only=on`. The two database snapshots are not globally atomic, so stop/quiesce source writers and repeat immediately before owner-approved cutover.
- Compare **34 selected durable and privacy-sensitive tables** using database-side original `to_jsonb(row)::text` in primary-key order and streaming SHA256 with per-row framing. No JavaScript numeric parsing or source member IDs in outputs. Include guild configurations, role/security/member state, invites blocked cumulative statistics, tickets, private warnings/profiles, Hub metrics/settings, Cinema durable media, staff moderation evidence and full activity-feed history.
- Require identical PK and column contracts and target-only RLS/service-role privacy. One documented schema normalization removes the two *target-only* `staff_metrics.created_at/updated_at` fields and permits five source nullable metrics to be NOT NULL on target **only if no source staff records contain NULL**. All other unrecognized column changes block.
- 1 MiB max row, 150 MiB max table, 350 MiB per source+destination combined budget and 100,000 rows per table. Results print only table names/counts and blocker counts, not rows, credentials, member IDs or actor data.
- Detect unfinished original bot operation jobs, pending dashboard commands, live verification tokens, voice-verification sessions, recent Cinema sessions and unclosed Community Hub sessions. Require candidate operation/command queues EMPTY to prevent source history replay.
- The source has **2 PENDING + 1 STAFF_ACCEPTED** voice-verification sessions on this audit date. Report these as actual blockers; DO NOT mark complete/cancel/reissue them automatically. Original user/Discord state must be reconciled by staff/owner via existing authoritative bot feature.
- Some original tables remain intentionally excluded: `dank_invite_block_events` (owner opted against copying), old automatic guild config versions, `audit_events` and `audit_logs` from original shared project (unproven Dank Shield ownership), expired tokens and old sessions. The owner still needs retention/audit lineage decision where applicable.

### Safety and interpretation
- CI is **credential-free** Python unit/static audit guard. Manual workflow is owner-only `main`, no schedule, no production writes, no trigger changes or Discloud changes.
- A workflow returning `readiness=BLOCKED` is **an audit executed successfully**, NOT a successful cutover gate. A result of `DATA_SNAPSHOT_ONLY` is also NOT an authorization. We require: (1) authoritative voice/Cinema session decisions; (2) explicit source writer freeze and identical final re-audit; (3) independent encrypted snapshot of relevant original data **and independent off-host restore verification**, not just `pg_dump --list`; (4) production credential/permission validation and Discord guild canary; (5) a safe rollback route; (6) explicit owner approval for live Discloud credential switch.
- Free Supabase quotas/egress, 10k/500k guild scalability and runtime Redis/provider capacity have NOT been proven by a source/target SQL snapshot. Keep current production working.

**Never switch Discloud as a side effect of this audit.**
