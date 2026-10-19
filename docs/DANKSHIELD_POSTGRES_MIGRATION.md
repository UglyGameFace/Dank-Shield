> **CURRENT DIRECTION (2026-10-19): Aiven PostgreSQL 18.** This
> document below records the earlier Discloud PostgreSQL 17 design and
> independent Backup #9 restore history. It is not the current Aiven runbook.
> The Aiven guild-config staging/shadow now contains verified copies of
> 12 original guild configurations. The live bot remains on Supabase,
> production cutover has NOT been authorized, and source records are intact.
> For ongoing byte-exact, quota-bounded historical preservation, see
> docs/AIVEN_HISTORY_ARCHIVE_20261019.md. No new source history archive
> has been imported yet.

# Dank Shield: selective migration into the existing Discloud PostgreSQL 17

**Status: design and read-only preflight only. No existing data has been copied or
deleted. The current bot still uses Supabase. Do NOT switch the bot to this
database or remove the Supabase secrets until repositories are ported and tested.**

## Destination: reuse the database, do not provision another

The owner's existing **DankShield-DB** on Discloud is a PostgreSQL 17 service
with private VLAN access. At the screenshot checkpoint it used ~47 MB, with
512 MB RAM and 0.25 vCPU. **512 MB is RAM, not database disk capacity.**
Discloud support confirmed the owner's Diamond plan provides **10 GB of shared
storage across apps** (file dependencies excluded). That is **10 decimal GB**,
not a dedicated 10 GiB PostgreSQL quota. The current usage of other apps,
PostgreSQL logs/host allocation and actual free space remain unverified.

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

## Diamond 10 GB shared-storage operating budget (support-confirmed)

Discloud's developer confirmed the owner's Diamond plan has **10 GB shared
across apps** and excludes dependencies from its file accounting. For
conservative byte checks this migration uses **10,000,000,000 bytes**,
not 10 GiB; there is no evidence that 512 MB of RAM is a disk quota.

The manual-only migration entrypoints now validate their proposed PostgreSQL
allocation inside that shared plan before writing. The operator must supply:

- `DANK_PG_STORAGE_BUDGET_MB`: a deliberately assigned PostgreSQL **MiB**
  budget, including its present data and projected new records;
- `DANK_PG_OTHER_APP_STORAGE_MIB`: the **measured** consumption of other app
  files outside the destination database, also in MiB (zero only if verified).

The tools reserve another 1 GiB of unassigned shared storage, then retain the
existing 70% database-budget staging threshold, 4x staged-payload sizing,
transactional checks, and schema/row hash verification. If other-app usage
cannot be measured, manual `--apply` refuses to proceed. Neither the plan
limit nor the DB's own `pg_database_size` reports real host free bytes or the
WAL's exact usage. No Discloud database or existing voice-bot data was read,
written, restarted or migrated by this code change. For other service plans,
review the migration's operator-only Diamond assumption before reuse.

## Next gate: restored source staging candidates (Backup #9 only)

Before connecting to the live Discloud destination, the existing **manual**
Backup #9 independent-restore workflow now audits the *same fixed 16-table
allowlist* used by `stoney_verify.core_data_stage.SOURCE_KEYS`. After a fully
authenticated, transactionally successful restore into disposable Supabase
PostgreSQL 17, it reads exact row counts and checks each table's expected key
columns, NULL logical keys and duplicate composite keys. Absent tables,
missing key columns or unsafe keys are **reported separately**; they are not
silently treated as valid empty tables. Queries use only code-owned identifiers
and read-only SQL. The workflow emits counts/boolean results only, never
payloads, guild/user IDs, passwords or decrypted archives.

This is **historical snapshot evidence** from Backup #9, not proof that all
16 tables belong exclusively to Dank Shield, that Supabase has not changed
since the backup, or that Discloud has storage headroom. The result explicitly
keeps `cutover_authorized=false`. The legacy five critical-table counts and
full restore gate remain required.

Discloud's July 2026 developer announcement also states Diamond and higher
receive automatic daily application backups **including databases**. This
is valuable independent protection, but their retention, recoverability and
how existing Watch Yo Mouf data would be restored must be verified before
relying on it for rollback. Do not treat automatic snapshots as a substitute
for the separately preserved, authenticated Backup #9 or a tested Discloud
target restore.

## Oct 14 live-source comparison (read-only, pre-cutover)

The merged PR #563 inventory succeeded in manual isolated Backup #9
restore run `38572982196`. All **16 existing first-slice source tables**
have available expected logical keys and no NULL or duplicate key
combinations. The snapshot contains **988** candidate rows; this does
**not** prove table ownership or authorize any target write.

A separate **read-only SQL** audit of the currently connected IdleGrowOp
Supabase project found **989** rows across the same 16 tables. The
`member_activity_ledger` count increased **289 → 290**; counts
for the other 15 tables are unchanged. Equal counts do not imply equal
payloads. This is evidence that an authenticated historical backup,
even when perfectly restored, is not a current-state migration plan.
Source-first staging, row-level digests, write reconciliation, and final
rollback checks remain required.

On this read-only inspection, the 16 candidate tables occupy **3,743,744
bytes** of PostgreSQL allocated space including their associated index
and auxiliary files, while the full **shared Supabase source** reports
**127,855,763 allocated bytes**. Neither is a measurement of destination
Discloud free SSD or a safe growth allowance.

The read-only destination storage audit, first-slice staging and
guild-config shadow import must use the **same shared-plan-aware budget
validator**. Reporting a raw, unchecked `DANK_PG_STORAGE_BUDGET_MB`
as "within_budget" without accounting for the other apps would be
misleading. If no budget has been assigned the audit may still report
`budget_unverified` without writing. If a budget is configured, the
audit rejects missing other-app usage and shared-plan overcommit before
connecting; the existing 1 GiB reserve is unchanged.

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
  conservatively earmarked from the shared plan allowance, not 512 MB RAM
  or the full 10 GB. At 70% flag a warning,
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
# Set DANK_PG_STORAGE_BUDGET_MB to a deliberately earmarked slice of the
# confirmed shared 10 GB plan (integer MiB, NOT the full 10,000 MB).
# Set DANK_PG_OTHER_APP_STORAGE_MIB to measured other-app file usage,
# excluding target DB bytes. Unmeasured shared usage blocks --apply.
# The operator tools reserve another 1 GiB for shared writes/WAL.
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

## Phase 5: verified encrypted source backup before any live transfer

`tools/backup_dankshield_supabase_source.py` introduces an **explicit-only**
PostgreSQL 17 `pg_dump` archive of the *confirmed shared Supabase source
project*. This step is higher priority than an actual Discloud migration.
The command's default mode merely prints prerequisites; it never runs on
Discord startup, reads the database, changes server settings, or connects to
the existing voice-bot/PostgreSQL destination.

- Requires a direct Supabase PostgreSQL **session** endpoint at
  `db.<SOURCE_REF>.supabase.co:5432` using `postgres`, or an official
  session pooler `*.pooler.supabase.com:5432` with username
  `postgres.<SOURCE_REF>`. Transaction pool port 6543, unrelated hosts
  and mismatched source refs are rejected. Use a password-authenticated
  PostgreSQL URL in `DANK_SOURCE_DATABASE_URL`, supplied privately.
- Uses PostgreSQL **17** `pg_dump --format=custom`, with encrypted output
  through a constant-memory stream. An AES-256-GCM encrypted `.dsbak`
  file is written directly, without a plaintext dump file. The strong
  passphrase from `DANK_BACKUP_PASSPHRASE` must be managed separately.
  A local directory with owner-only permissions (0700) and explicit free
  space is required; no GitHub artifact or Discord attachment is created.
- On success verifies AES authentication and checks that `pg_restore 17`
  can read the archive's table of contents. Tampered, wrong-key, truncated,
  oversized and failed dumps are rejected. Only a verified archive is moved
  to its final filename. Backup creation reads source data, **never writes
  or deletes source tables**.
- The backup contains the complete database objects visible to its PostgreSQL
  backup role, potentially including non-Dank-Shield tables from the shared
  project. Treat the encrypted output as sensitive and confidential.
  Supabase-managed or excluded roles/privileged objects may not be covered.
  `--no-owner --no-privileges` means server-wide roles and grants are
  **not** backed up. Independent recovery of roles, policies, external
  Storage objects, secrets and platform configuration requires additional
  verified exports.
- A passing TOC check **does not demonstrate a complete restore**. A
  source-independent encrypted copy and full restore drill are required
  before the source can be retired. The isolated PostgreSQL 17 CI test
  exercises a full dump/decrypt/restore of a disposable local database,
  **not** a restore of the live shared Supabase project.
- Never store the sole copy in an ephemeral Discloud application directory,
  inside the existing database's limited disk, in a public Git repository,
  or in bot log output. Retain Supabase source access until all tables,
  dependent functions and remaining feature histories are recovered and
  per-guild parity is verified.

Private operator commands (only after provisioning a secure durable target;
do **not** paste database credentials or passphrases into the chat):

```text
python -m tools.backup_dankshield_supabase_source
# Requirements include isolated: pip install -r requirements-backup.txt
# Existing private environment must provide:
#   DANK_SOURCE_DATABASE_URL    (direct PostgreSQL URL, not SUPABASE_URL)
#   DANK_MIGRATION_SOURCE_REF   (confirmed 20-character project ID)
#   DANK_BACKUP_PASSPHRASE      (unique strong passphrase, kept separately)
# Backup directory must exist with mode 0700 and durable off-host storage.
python -m tools.backup_dankshield_supabase_source --create --output-dir /path/to/private/durable
python -m tools.backup_dankshield_supabase_source --verify /path/to/private/durable/created.dsbak
```

No live backup has been run through this chat: there is no authenticated
execution route into the Discloud VLAN or private source PostgreSQL server.
Do not claim migrated, backed up or cutover-ready until the real backup,
independent restore and source/destination checks pass.

## Mobile-only source backup handoff: GitHub Actions (manual)

To make a real backup practical for a repository owner working from
**Android only**, the first party
`.github/workflows/manual-encrypted-supabase-backup.yml` adds a
`workflow_dispatch` action. This is a **manual backup**, not a recurring
worker, a database migration, a direct write to DankShield-DB or permission
to retire the shared Supabase project. It can run only from `main`.

### Owner setup in GitHub's mobile browser

1. On `UglyGameFace/Dank-Shield`, open **Settings → Secrets and variables
   → Actions → New repository secret**. Add
   `DANK_SOURCE_DATABASE_URL`: the private PostgreSQL **session** connection
   URL obtained from the existing *IdleGrowOp* Supabase project's Connect /
   Database connection details. Use the **session pooler port 5432** if
   GitHub runners lack IPv6 access to the direct database address. This is
   **not** `SUPABASE_URL` and not the service-role API key.
2. Create another repository secret `DANK_BACKUP_PASSPHRASE`: a unique,
   randomly generated passphrase at least 24 characters. Keep the only
   recovery passphrase safely **outside GitHub** as well (a password manager
   or secure offline record). Losing it makes the archive unrecoverable.
3. Under the repo's **Actions** tab select
   **Manual Encrypted Supabase Backup → Run workflow** on branch `main`.
   Enter the actual 20-character Supabase project reference for *IdleGrowOp*
   and choose `BACKUP_ONLY_NO_DELETE`. No raw passwords or connection
   strings belong in any workflow input, issue, chat or screenshot.
4. Wait for a successful workflow, then download the
   **dankshield-encrypted-supabase-source** artifact from the workflow
   run's **Artifacts** section. It contains only a `.dsbak` ciphertext
   inside the artifact ZIP. On Android extract the `.dsbak` to private
   durable storage, retain the archive's SHA-256 reported by the backup
   command, and keep an independent copy. **Github deletes the artifact
   after three days**, so leaving it in Actions is NOT a backup strategy.
5. A green run authenticates the encrypted dump and makes `pg_restore`
   read its table of contents. It does **not** prove that the actual
   production source is restorable. Before cutover run a controlled
   independent restoration and verify the source manifests and important
   Dank Shield records. Keep the shared source unchanged until signed off.

### Preserved boundaries and warnings

The project currently has **87 public tables**, and a separate `idle`
schema with five tables, alongside Supabase-managed `auth` and other
schemas. The SQL `storage.buckets` and `storage.objects` tables currently
have **zero rows** (2026-10-13 read-only inventory), but this does not
guarantee that external file/blob storage or platform secrets are covered
by `pg_dump`. Those require their own inventory/export if used. Because
the source project is shared, treat the archive as sensitive to **other
apps**, and **never restore the entire dump directly into the existing
Discloud database**. Use a separate disposable recovery environment,
then selectively import only verified Dank Shield-owned data into isolated
target schemas.

The workflow does not take or publish a backup without the owner's manual
action and the two GitHub Secrets. It does not read the existing Discloud
database or the voice bot's records. The temporary GitHub runner performs
the export and produces an AES-256-GCM encrypted artifact only, using
PostgreSQL 17 clients in ephemeral Docker. Production bot dependencies,
RAM, Discloud branch, Supabase API secrets and SQL records remain unchanged.

## Manual backup failure diagnosis (no credentials in logs)

In the manual GitHub Actions workflow `Create authenticated, encrypted
source archive`, failing PostgreSQL 17 `pg_dump` previously emitted only
the generic `RuntimeError` class and dropped the underlying error. A
failed login could appear as an empty archive before its real cause was
reported. The diagnostic implementation now categorizes `pg_dump` stderr
into **fixed safe codes** only and never outputs the raw connection string,
hostname, username, password, error traceback or SQL payload.

A failed backup may now report one of:

- `database_authentication_rejected`: Verify the password in the
  `DANK_SOURCE_DATABASE_URL` GitHub secret privately. Remember to URL-
  encode reserved password characters. Do **not** reset the shared Supabase
  database password just because this workflow failed.
- `pooler_project_or_user_rejected`: Re-copy this project's **Session
  pooler** PostgreSQL URI with the `postgres.<project-ref>` user.
- `database_dns_lookup_failed` / `database_network_unreachable`:
  Use the project's IPv4-compatible session pooler, port 5432, rather than
  the direct Supabase IPv6-only hostname where unsupported.
- `database_dump_permission_denied`: Do not escalate to an unrelated
  database superuser or skip tables silently. Review shared-project role
  access and perform a separate planned export for restricted objects.
- `encrypted_archive_verification_failed`: Keep source unchanged;
  investigate source dump, encryption/TOC verification, and restore contract.
- `pg_dump_failed_unclassified`: Operator must review the private source
  connection or an isolated repro; do not echo unsanitized stderr to Actions.

The classifications describe **failures**, not a successful backup or
permission to switch Discloud. Pending/corrupt encrypted files are erased
on failure; only an authenticated archive with verified TOC can be uploaded.
The GitHub Actions workflow remains manual, read-only on Supabase, and has
no connection to the live voice bot or existing Discloud database.

## Backup #6 root cause is NOT confirmed: evidence-only diagnostics

The actual manual backup #6
(`https://github.com/UglyGameFace/Dank-Shield/actions/runs/38496328485`)
returned `backup_failed_unclassified`, about 40 seconds into the archive
step. No traceback, dump completion marker or archive verification status
was captured. This **does not prove** a broken pipe, password, network or
database failure. A separate unmerged PR #555 explored one possible
`pg_restore --list` early-close condition, but its passing tests do
**not** establish it caused backup #6. Do not merge that behavioral change
on hypothesis alone.

This diagnostics-only slice adds an optional fixed-name phase reporter to
`create_backup`. The operator CLI prints the **last operation reached**
and a fixed exception category if a future backup fails. Phases distinguish
preflight, private file opening, PostgreSQL dump launch, streaming
encryption, dump exit status, fsync, archive TOC verification and commit.
Known controlled PostgreSQL failure codes remain intact. Only fixed
program-authored labels and error kinds reach GitHub Actions logs. The
source URI, password, user/host, stderr, exceptions, schema rows and
plaintext archives are never printed.

The new tests intentionally simulate a source-authentication rejection and
a large encrypted archive whose TOC-only consumer closes early, proving
their diagnostic output is **different** and contains no source secrets.
No bot runtime hook, monkey patch, destructive cleanup or automatic data
migration has been added. Even after these tests pass, one new explicit
manual workflow execution is needed to find the real operational failure
before applying a targeted fix.

## Confirmed archive pipe failure in manual source backup #8

Manual encrypted backup #8 was launched with the merged phase diagnostics
and failed at `archive_toc_validation_started` with `error_kind=broken_pipe`.
The exact GitHub Actions run:
`https://github.com/UglyGameFace/Dank-Shield/actions/runs` (select
`Manual Encrypted Supabase Backup #8`). This is direct operational
evidence of a pipe closure during `pg_restore --list`, **not** another
database password rejection or proof that the archive was fully verified.

`pg_restore --list` may finish reading the **TOC** and exit with status 0
before consuming a large custom-format dump's data segment. Our previous
verifier kept writing decrypted bytes to closed stdin and raised
`BrokenPipeError`, rejecting a potentially valid encrypted archive.
The fix now stops writing to that input if it closes, but always:

- finishes decrypting **all** encrypted source bytes and verifies the
  final AES-256-GCM authentication tag, rejecting altered trailing data;
- requires the non-executing `pg_restore --list` process to exit 0;
- rejects malformed archive / nonzero process exit and prevents partial
  ciphertext files from being uploaded.

Tests reproduce the actual early-close behavior via the manual backup CLI,
authenticate >7 MiB ciphertext through early close, detect trailing
ciphertext tampering, and reject a consumer that exits nonzero.
This repair does not change any database schema, credentials, bot runtime
or Discloud voice-bot data; it **cannot** prove the production source
backup was completed. A brand new `workflow_dispatch` on the
merged **main** is needed to establish actual success. Rerunning a
prior failed job reuses the old commit and is not an acceptance test.

## Recovery gate: independent restore of the owner's encrypted source Backup #9

On 2026-10-13 the manual source backup workflow **#9** completed with a
valid authenticated encrypted archive. The artifact's SHA-256 is
`84acce40549855387ad04ddaaec41cf845fe12cf73a5e5e88282422eddb1f1c0`
and its original artifact is
`https://github.com/UglyGameFace/Dank-Shield/actions/runs/38501909516/artifacts/11866606300`.
The owner separately verified the downloaded `.dsbak` SHA-256 matched.
**This establishes intact encrypted bytes and readable archive TOC, NOT
a successful full restore.**

The new `.github/workflows/verify-source-backup9-restore.yml` provides a
**manual-only** independent restore drill on a fresh GitHub-hosted runner
with an ephemeral Docker PostgreSQL 17 database. It downloads only this
one pinned encrypted archive directly from the successful workflow
using the read-only Actions token. It requires no Supabase connection
credentials and never connects to the existing Discloud PostgreSQL
`DankShield-DB` or the voice-bot runtime.

Security and fidelity requirements:

- `workflow_dispatch` on `main` with the deliberate
  `VERIFY_BACKUP_9_ISOLATED_ONLY` confirmation.
- Only fetches encrypted Backup #9 (run `38501909516`) and checks
  its **exact SHA-256** before decryption. Requires the existing
  `DANK_BACKUP_PASSPHRASE` repository secret; it is not logged.
- Authenticates the entire archive's AES-GCM tag and checks the TOC
  **before opening any SQL restore connection**. The disposable
  PostgreSQL 17 target is `127.0.0.1:5432` and a newly created,
  named `dank_source_recovery_drill` database. Existing DB detection
  aborts. Repository identity and explicit disposable-runner gates
  abort use outside authorized CI.
- Streams plaintext through a PG17 subprocess directly to the isolated
  DB, never to a runner artifact or plaintext dump file.
  `--single-transaction --exit-on-error --no-owner --no-privileges`
  prevents partial-restore approval. PostgreSQL service is destroyed
  after the GitHub job completes.
- On success, prints aggregate table counts in `public`/`idle`
  and row counts for five critical Dank Shield tables only. On failure,
  prints **only a fixed failure code** and never raw SQL, passwords,
  private filenames, connection strings, schema contents or rows.
- The whole source DB is shared with other applications. Therefore
  the encrypted archive is strictly for **isolated recovery testing**;
  never restore it wholesale into the existing Discloud DB. A
  compatible source environment may be required if Supabase-managed
  roles, extensions or policies are unavailable on stock PostgreSQL 17.
  Such a missing dependency is a recovery blocker, not grounds to
  skip failures or claim a successful full restore.

**GitHub Actions workflow, full visible URL:**

`https://github.com/UglyGameFace/Dank-Shield/actions/workflows/verify-source-backup9-restore.yml`

After merging the workflow on main, manually run it soon: GitHub's
original Backup #9 artifact expires around Oct 16 10:37 PM EDT (2026).
The owner already has their separately downloaded, checksum-matching
backup and must keep a private durable copy and backup passphrase
separately. A lost ephemeral artifact does **not** justify weakening
source verification.

Even a successful independent source restore does not verify the
existing target DB size, table-by-table mapping, 10,000-guild scaling,
delta writes, rollback or migration of all bot-owned features. Supabase
must remain authoritative until a separately approved cutover with
source/destination record parity and recovery is complete.

## Backup #9 restore attempt: concrete Supabase extension blocker (Oct 13)

The first manual independent restore (GitHub Actions run
`38505374227`) **failed** in the disposable `postgres:17` container.
The real PostgreSQL service logs showed
`extension "supabase_vault" is not available`. The previous Python
streamer reported `pg_restore_closed_input_early` because the failing
`pg_restore --exit-on-error` closed stdin; this was an effect, not the
root cause. Backup retrieval, exact SHA-256 verification and encrypted
archive TOC authentication had passed. No source or Discloud data was
modified, and **the real source has NOT yet been restored**.

The recovery candidate now uses the published self-hosted Supabase
PostgreSQL 17 image `supabase/postgres:17.6.1.136` **only in the manual
disposable restoration workflow**. It authenticates the exact same
archive before restoring, requires the Supabase Vault extension to be
available, and uses the container's dedicated `supabase_admin` role
for necessary extension/schema operations. The verified fixture-only
plain PostgreSQL 17 integration path remains unchanged. Errors
and early pipe closures are classified by the underlying process's
captured stderr, returning a fixed failure code, never raw SQL. The
ephemeral PostgreSQL service disables error SQL statement logging
before the source archive is streamed because GitHub publishes
service-container logs after job teardown.

Do not weaken `--exit-on-error`, skip failed objects, delete Vault
statements from the dump, or report a partial restore as success.
The first new manual workflow run on merged `main` must demonstrate
source-schema and required Dank Shield row counts, or its first
real incompatibility must be diagnosed explicitly. Even if SQL
restores, Supabase-managed roles, extensions and Vault/pgsodium
encryption keys, platform credentials, external Storage and secrets
require separate inventories/exports; restoring rows **does not prove**
encrypted Vault secrets can be decrypted. Supabase remains authoritative,
and the existing Discloud database must never receive the entire
shared-source archive.

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
