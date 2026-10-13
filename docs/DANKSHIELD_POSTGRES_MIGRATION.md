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

**Measured payloads:** The 920 retained config snapshots average ~30,953
bytes and reach ~443,571 bytes each (none is flagged `is_manual=true`).
The 4,710 activity-feed JSON records average ~11,636 bytes.

**Verified duplicate audit projection:** Exactly 3,369
`activity_feed_events` rows with `event_family='moderation'` point to
`related_table='staff_action_logs'`; all 3,369 have unique
`related_id` values and all join to an existing staff-action record.
Do **not** blindly import each as a second full event payload. A future
PostgreSQL feed adapter can display these actions through a constrained,
indexed join/view from the canonical `staff_action_logs` table. Archive the
entire original feed first; verify field and UI parity because a linked row
may contain additional display-only fields. Other feed families must be
classified individually and must not be discarded by this optimization.

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
  event forever. Preserve moderation/ticket evidence separately. In the
  migration source all 3,369 moderation-feed entries already reference
  `staff_action_logs`; use one canonical physical record and a derived
  activity-feed projection after verifying field-level parity.
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

## Phase 1 implementation: isolated, strictly bounded source staging

The one-shot `tools/stage_dankshield_core_data.py` command is now available
in the current implementation candidate. It is **NOT** a replacement for the
Supabase live repositories, a complete source backup or an automatic migration.

- Whitelist: 16 primary-keyed tables for live guild config, security
  settings, role rules, member activity state, ticket setup and ticket history,
  and community hub settings. Ambiguous `users`, `world`, old `/toke`
  sessions, all unrestricted event history, and voice-bot data are excluded.
- Default mode is source **dry-run only**, with at most 100 rows per REST
  page and a total 12 MiB source payload budget. It prints counts and
  SHA-256 digests, **not** source rows, secrets, or connection strings.
  It aborts on missing/duplicate keys, changed source data, bad source
  responses, size overruns or unexpected source project identity.
- The `--apply` mode is **explicit only**. It requires a known Supabase
  project reference, a confirmed private destination database name, and
  the **real Discloud disk storage allowance**. If storage budget is unknown,
  the transfer refuses to write. The current 512 MB RAM figure is not a disk
  allowance and must never be entered as such.
- PostgreSQL staging is in `dank_migration.staged_records`, not `public`
  or any voice-bot schema. Its migration is
  `postgres/migrations/202610130002_core_staging.sql` and must be
  explicitly applied by an approved private-network deployment command.
  No startup schema repair or automatic cleanup has been introduced.
- Every record is keyed by a hash of the source PK and has a canonical JSON
  payload hash. Conflicting old copies are rejected instead of overwritten.
  The stage transaction validates counts/hashes, rescans the source for drift,
  and rolls back on any discrepancy. This guards against common transfer
  mistakes, but paginated HTTP reads **cannot** guarantee a single
  repeatable-read Supabase snapshot while live writes continue.
- Before `--apply`, the tool checks allocated PostgreSQL bytes and reserves
  four times its estimated payload against a 70%-of-confirmed-disk threshold.
  It never assumes that `pg_database_size()` reports host free disk space.
  If the DB plan's disk allowance is unknown, leave staging unexecuted.

Private operator sequence, when Discloud can execute migration commands in
its VLAN (no passwords in GitHub, chat, screenshots, command history or logs):

```text
# Supabase bot environment already provides SUPABASE_URL and service-role key.
# Set DANK_MIGRATION_SOURCE_REF to the confirmed project's hostname prefix.
# Set DANK_MIGRATION_EXPECTED_DB to the existing Discloud DB's name.
# Set DANK_PG_STORAGE_BUDGET_MB to the VERIFIED disk storage allowance.
# Reuse the private PostgreSQL connection via DANK_CORE_POSTGRES_DATABASE_URL,
# or the already-established DANK_VERIFY_REMINDER_DATABASE_URL.

python -m tools.stage_dankshield_core_data
# Review only row counts and digest summary; dry-run changes nothing.

# After a full source backup, CI, DB privilege checks, and the storage preflight:
# psql -v ON_ERROR_STOP=1 -f postgres/migrations/202610130002_core_staging.sql
# python -m tools.stage_dankshield_core_data --apply
```

**There is no supported remote execution path into the Discloud private VLAN
in this chat.** The commands are staged for an authorized private runtime, not
claimed as executed. The PostgreSQL 17 integration check must pass against an
isolated test instance before touching `DankShield-DB`. This phase does not
create a finalized backup or switch the live bot to PostgreSQL.

## Non-negotiable: no monkey patches or hidden repair shortcuts

The owner explicitly requires a maintainable architecture, **not monkey
patches**. The production implementation must use a typed, intentionally
selected data-access interface and explicit PostgreSQL repositories with
versioned SQL migrations. Do not modify imported Supabase/discord.py/psycopg
functions or module globals at runtime, replace methods behind callers'
backs, wrap errors in a way that conceals failed writes, or create competing
fallback persistence paths. Mocking via fixtures in tests is not production
monkey-patching.

All source and destination changes must be reviewable as normal commits,
tested against isolated PostgreSQL 17 and source-contract fixtures, and
validated for ticket/voice-bot isolation, idempotency, restart recovery,
counts, read/write parity, and rollback. Migration schema is **never**
auto-repaired or upgraded on bot startup.

Self-maintenance must distinguish:
- PostgreSQL native autovacuum/analyze and approved bounded cleanup of
  genuinely temporary data;
- bounded connection recovery and backpressure, health checks, disk usage,
  safe index/statistics diagnostics, and independently tested backup restore;
- anything destructive or potentially locking, such as `VACUUM FULL`,
  `REINDEX`, orphan deletion, role/schema rewrites, or restoring corrupted
  rows, **requires explicit controlled maintenance and verified backups**.

Storage alerts and safety caps must never silently drop server configuration,
tickets, moderation/security history or consent/activity evidence. Database
corruption cannot be declared automatically repaired without verification.
An undersized disk is not fixed by deleting valuable evidence.

## Phase 2: guild-configuration shadow repository (NOT live)

The first direct PostgreSQL repository is deliberately limited to a
**read-verified shadow of `guild_configs`**. It does not intercept or replace
`stoney_verify.guild_config`, does not patch any Supabase/discord.py methods,
and does not create a second live configuration authority. Configuration
writes, compare-and-swap, revision history, and protected key semantics remain
on the existing backend until independently implemented and proven equivalent.

- An explicit PostgreSQL 17 migration creates
  `dank_core.guild_config_shadow` in an isolated schema, with a primary key
  for each guild, the complete original JSON payload, the original primary
  key and a canonical SHA-256 digest. Voice-bot/reminder tables are untouched.
- `stoney_verify.postgres_guild_config_shadow` provides a typed, explicit
  `inspect_staged_config`, `import_config_shadow`, and
  `read_shadow_config` path. The import validates the staged record hash,
  payload byte count, guild/source PK, duplicate guilds, existing target
  rows, destination identity and real disk headroom. It is all-or-nothing.
- `python -m tools.materialize_dankshield_pg_config` is **read-only by
  default**. The private operator must explicitly request `--apply`,
  supply the known DB identity and verified DISK capacity, and have
  previously installed both migration SQL files in order. It prints counts
  and digests, not passwords or configuration contents.
- The first import has an explicit maximum of 500 guilds and 12 MiB.
  These are first-slice safety ceilings, **not** 10,000-guild production
  throughput limits; lifting them requires a bounded keyset/streaming
  implementation and 10,000+ guild load tests.
- Its tests currently include injected fake transactions and tampered-record
  cases. Those are legitimate **test doubles**, not production monkey
  patches. **Real PostgreSQL 17 integration remains unverified** until the
  isolated SQL migration and import/replay tests actually execute on a
  PostgreSQL service with the expected privileges.

Do not put the shadow table on the bot's live read/write path. The next
slice is an explicitly versioned PostgreSQL `guild_configs` repository
with one transactionally correct compare-and-swap/patch mechanism and
tested parity for sensitive permissions, verification, roles, and setup.

## Phase 3: atomic PG17 guild settings writes (offline repository only)

`stoney_verify.postgres_guild_config_repo.GuildConfigPgRepository`
provides explicit `read` and row-lock-serialized `patch` operations over
`dank_core.guild_config_state`. Its versioned schema is
`postgres/migrations/202610130004_guild_config_atomic_state.sql`, with one
current JSON row and monotonic revision per server, not a bulky new snapshot
on every settings update.

- The repository accepts an existing private PostgreSQL connection; there
  is **no global client replacement, monkey patch, startup schema DDL, or
  automatic production routing**. The existing Supabase `guild_config.py`
  remains the current runtime authority.
- A patch uses `SELECT ... FOR UPDATE` inside the same transaction as its
  `UPDATE ... WHERE revision=old_revision RETURNING revision`. Concurrent
  workers cannot overwrite sibling keys from stale reads; optional revision
  and per-key compare-and-swap expectations fail closed.
- Existing protected roles, channels, setup-sensitive keys and fill-only
  modes have explicit tests against the canonical key list.
  Unknown modes, reserved/base columns, oversized JSON or missing seeded
  guilds abort. Explicit clears are distinct from a JSON null patch.
- Only changed settings trigger a write and revision increment; unchanged
  input does not write another full row or create configuration-history
  churn. This slice **does not add an automatic full-snapshot trigger**.
- `seed_from_verified_shadow` requires explicit manual execution after
  the phase-2 shadow is independently verified. It checks source SHA-256,
  target DB identity, configured disk budget, row identity, existing state
  conflicts, memory limits and final exact row parity. It rejects missing or
  already-modified live state rather than resetting configuration values.
- The first seed is intentionally limited to 500 guilds/12 MiB. Full-scale
  migration will need bounded streaming and measured PG17 capacity tests.
  Nothing here proves 10,000-guild runtime readiness.
- A separate complete, restorable source backup and cutover delta strategy
  remain **mandatory**. A staged REST scan is not a transactional source
  backup. The current Discloud service's disk allowance and private runner
  are still unverified, so no live seed/apply has been performed.

A separate, opt-in real PostgreSQL 17 integration test now lives in
`tests/test_postgres_guild_config_pg17_integration.py`. It accepts only a
local `postgres` database, checks the server is PG17, runs the versioned
migration in that disposable test environment and exercises two concurrent
CAS writers. It is **skipped** in ordinary CI without the explicit
`RUN_PG_GUILD_CONFIG_INTEGRATION=1` and `DANK_TEST_DATABASE_URL`
localhost settings. Authoring the test is not evidence that it has run.

**Still required before any live cutover:** a successful isolated PG17
integration/concurrency run, setup/role permission validation across the actual guild
config API, revision/history/restore parity, operational secret provisioning,
remaining tickets/community/Cinema/member/moderation repositories, full source
backup and restore tests, size/load soak, and a reversible switch. Do not
update bot routing, remove Supabase secrets, or redeploy Discloud on main yet.

## Phase 4: explicit cross-database configuration parity gate

`stoney_verify.postgres_guild_config_parity` is a **read-only audit** between
the current `public.guild_configs` source and the offline
`dank_core.guild_config_state` target. Run manually inside the authorized
private VLAN with `python -m tools.audit_dankshield_guild_config_parity`.

- The command requires both the exact Supabase hostname project reference
  (`DANK_MIGRATION_SOURCE_REF`) and the exact existing Discloud PostgreSQL
  database name (`DANK_MIGRATION_EXPECTED_DB`). It uses current application
  credentials without logging or displaying them.
- PostgreSQL reads execute in `REPEATABLE READ, READ ONLY` mode, with a
  connection timeout and statement timeout. Source pages use an explicit,
  stable `guild_id` + `id` order.
- Reports only source/target row counts, canonical JSON content digests and
  an unchanged-source flag. Missing, additional, duplicate, oversized or
  changed data does not pass. Reads source twice to catch common changes
  during the check. Never prints the actual server IDs, settings, role or
  channel IDs, or connection strings.
- Its initial memory/row envelope is 500 guilds / 12 MiB, carried forward
  from the small shadow-seed slice. This is **not** a 10,000-server ceiling:
  a separate streaming/delta migration and throughput profile are required.
- **Critical:** Even a matching report has
  `cutover_authorized=false`. Supabase REST paging is not a repeatable-read
  source snapshot, and writes can occur immediately after the scan. Backups,
  cutover write-fencing / delta replay, all other feature repositories,
  identity/privilege boundaries and rollback remain release blockers.
  Never auto-flip backend selection because this audit is green.

The existing `stoney_verify.guild_config` read and write paths remain
Supabase-only. No monkey patches, automatic DB client switching, hidden
fallback writes or startup schema updates were introduced. The test suite
includes an isolated real PostgreSQL 17 check detecting JSONB drift.

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
