# Dank Shield: selective migration into the existing Discloud PostgreSQL 17

**Status: design and read-only preflight only. No existing data has been copied or
deleted. The current bot still uses Supabase. Do NOT switch the bot to this
database or remove the Supabase secrets until repositories are ported and tested.**

## Destination: reuse the database, do not provision another

The owner's existing **DankShield-DB** on Discloud is a PostgreSQL 17 service
with private VLAN access. At the screenshot checkpoint it used ~47 MB, with
512 MB RAM and 0.25 vCPU. **512 MB is RAM, not database disk capacity.**
The storage limit and free physical disk are not established by the screenshot.

The DB already supports a small `public.dank_verify_reminder_delivery`
PostgreSQL integration in Dank Shield. Voice-bot-owned data must not be
overwritten or scanned as if it belonged to Dank Shield. Prefer distinct
`dank_core` and `voice_bot` schemas with distinct least-privilege DB roles,
both in the existing PostgreSQL *instance*. Do not create or migrate any
schema automatically at bot startup. Database connectivity is private via VLAN.

## Source inventory: shared IdleGrowOp Supabase (read-only)

2026-10-13 snapshot: 87 public tables, ~107 MB allocated; about 19,144
estimated live rows across public tables. This is not a complete backup.

| Table | PostgreSQL allocated | Evidence |
| --- | --- | --- |
| `activity_feed_events` | ~40 MB | ~4,710 live event rows; ~14 MB indexes and ~22 MB TOAST/aux |
| `guild_config_versions` | ~38 MB | ~920 live versions; ~17,525 inserts, ~16,297 deletes; ~37 MB TOAST/aux |
| `staff_action_logs` | ~11 MB | ~3,369 logged actions; potentially important moderation evidence |
| `guild_configs` | ~2 MB | 12 live rows, >41,000 updates in cumulative stats |
| `member_activity_ledger` | ~200 kB | 288 aggregated member rows, ~9,300 historical updates |

Stats are **estimates and PostgreSQL allocated size**, not proof of disk
capacity, missing data, or Supabase billing root cause. The Supabase banner
said that an organization exceeded a previous billing-cycle quota; the
quota category is unverified. Do not guess it was database disk usage.

## Preservation contract

1. First identify Dank Shield-owned tables in the shared source. Ambiguous
   generic tables (such as `users`, `world`, `global_accounts`) stay out of
   any automatic migration until ownership is proven.
2. Take and verify a recoverable **complete source snapshot** plus schema,
   indexes, sequence positions, policies, and functions before filtering the
   destination. A partial REST list or screenshots are not a backup. Never
   delete Supabase source records while rollback is needed.
3. Copy canonical/current guild configuration, permissions, tickets, ticket
   forms, verification controls, member preferences, moderation/security
   evidence, aggregated activity, community settings and Cinema libraries
   exactly. Preserve guild IDs, role/channel IDs, foreign keys, status and
   expiry fields. Verify per-table and per-guild counts plus row hashes.
4. For high-volume `activity_feed_events` and config version history:
   first **archive all historic records losslessly** to a separately backed-up
   artifact, confirm restore/replay works, then import the recent searchable
   history agreed with the owner. Do not silently discard old snapshots or
   security-relevant events. Manual backups and important staff incident
   evidence are exceptions to aggressive expiration.
5. Carry over existing verification reminder claims from the Discloud DB;
   do not replace or truncate the table.
6. Run a staged catch-up with idempotent keys; freeze writes or reconcile a
   documented delta during cutover. A mere count match cannot prove consistency.

## Storage controls required *before* porting high-volume writers

- **Separate responsibility.** Durable security/audit/tickets/settings must
  remain writable. Telemetry/media caches/duplicated derived data should not
  share their retention policy. No global `DELETE WHERE created_at < N` job.
- **No per-action full JSON snapshot.** Store one canonical live config;
  take a normalized/deduplicated snapshot only on meaningful changes, with
  per-guild revision quotas and periodic verified encrypted archives. Keep
  manual restore points, and cap cleanup batches. Existing Supabase triggers
  may have produced excessive TOAST despite a 50-row retention policy.
- **Small activity history.** Keep authoritative per-member last activity and
  per-type counts in upserted/aggregated rows. For analytics, roll up
  low-value repeated events by guild/date/type rather than persisting every
  event forever. Preserve moderation/ticket evidence separately.
- **Bound DB writes and memory.** Limit connection-pool size, concurrency,
  RPC/transaction batches and retry budgets. Retry idempotently. When an
  outage occurs, never turn missing activity history into permission for
  automatic member removal.
- **Indexes only for measured queries.** Multi-column indexes consume disk.
  Benchmark `EXPLAIN` for top reads and cap text/JSON payloads; use paging
  and indexes for retention candidates, not full scans.
- **Observability and headroom.** Run `stoney_verify.postgres_storage_budget`
  through `tools/audit_discloud_postgres_storage.py` as a read-only,
  explicitly invoked check. Supply a real `DANK_PG_STORAGE_BUDGET_MB`
  from the database service plan, not 512 MB RAM. At 70% flag a warning,
  at 85% flag critical. These are alerts, **not** automatic deletes or disk
  reservation; `pg_database_size` cannot tell remaining host disk space.
  Protect required writes by reclaiming only previously authorized ephemeral
  records; preserve evidence and fail closed when DB is unavailable.

## Deployment sequence

1. Confirm the real disk limit/backups for the existing Discloud DB and the
   voice bot's current tables/roles without disclosing credentials.
2. Build a private-VLAN, one-shot staging connection test to DankShield-DB.
   Refactor Supabase's many `.table().select/upsert()` and `.rpc()` callers
   to explicit PostgreSQL repositories. The existing
   `DANK_VERIFY_REMINDER_DATABASE_URL` is **only** the reminder feature's
   current connection, not a verified drop-in replacement for `SUPABASE_URL`.
3. Add migrations and independent PG17 CI with reference data, then run an
   isolated restore, permissions and rollback drill. Keep batch receipt
   idempotency and authoritative activity fail-closed checks.
4. Measure disk growth, query latency, autovacuum and RSS at 1, 10, 100 and
   larger guild test loads. This 0.25-vCPU database is not established as
   a 10,000-server production datastore. Upgrade resources based on
   measured write/load demand, not the number of installed servers alone.
5. Only after migrated records, read/write parity, backup, restore and
   rollback pass: merge the Postgres compatibility work, change Discloud's
   connected bot branch to `main`, rebuild one instance, then run the
   two-shard canary. PR #545 was merged but that alone is **not** a
   database-migration acceptance.
