# Aiven PostgreSQL 18: lossless, quota-bounded history archive

**Status (2026-10-19):** Implementation awaiting PR review, merge, owner-only
manual preview and actual Aiven parity checks. This is NOT a production
migration, runtime repository, archival deletion policy or full independent
database backup. Discloud Auto Deploy must remain OFF. Supabase stays intact.

## What was actually measured (source PostgreSQL, read-only)

The shared IdleGrowOp Supabase has 87 public tables and 112,812,032 bytes
of allocated space (~107.6 MiB), including indexes and auxiliary storage.
This is NOT Aiven usage or data-transfer billing.

Four confirmed Dank Shield history streams, as of source inspection:

| Table | Source rows | Original primary key | Archive treatment |
| --- | ---: | --- | --- |
| guild_config_versions | 920 | version_id bigint | Every original JSON row, compressed |
| staff_action_logs | 3,369 | id bigint | All moderation evidence preserved |
| activity_feed_events | 4,756 | id UUID | Full original feed retained, including display-only fields |
| dank_invite_block_events | 6,629 | event_hash text | Every original invite-block event |

Exactly 3,369 moderation-family activity feed rows referenced
staff_action_logs, all distinct and linked to a real action. They are
**duplicate representations, not identical records**. Never delete original
feed rows or invent a replacement feed before proving actual UI parity.
The private compressed archive preserves both originals while a future
canonical live feed may use a link/view to avoid duplicate hot storage.

Guild configuration history has 920 rows but 912 distinct snapshots:
plain snapshot deduplication is insufficient. Delta checkpoints are future
work and MUST be losslessly reversible and backed up before activation.

## Storage boundaries

The original rows use source PostgreSQL JSONB text (without numeric rounding) and
per-row zlib compression, stored as
bytea in private schema dank_archive.original_rows with SHA-256 and exact
raw length. Source row PK, guild, timestamp and type are indexed metadata.
Exact recovery uses unpack_original_json(), which verifies compression framing,
bounds and SHA-256 before returning the unmodified source JSON text.
unpack_record() provides a decoded convenience view.

The imported bytes are **not automatically a backup**. Aiven disk usage,
index allocation, future runtime tables, transaction WAL and provider storage
limits must be budgeted separately. The importer bounds sources to 30,000
rows and 180 MiB aggregate raw JSON, compressed payload to 90 MiB, max row
to 1 MiB, and checks Aiven against a 900 MiB internal budget at 70%
headroom with 4x compressed overhead allowance. Disk checks are conservative
but not a host-filesystem free-space guarantee.

## Source and destination security

- Source must be the previously verified Supabase PostgreSQL project;
  connection is direct/session PostgreSQL and defaults to READ ONLY.
- A repeatable-read snapshot is held throughout source reads. It is
  point-in-time parity, **not** a write freeze or live-change catch-up.
- Destination is the pinned Aiven PostgreSQL 18 service with verify-full
  TLS and an existing populated guild-config shadow.
- No public schema tables can exist. A dedicated private dank_archive
  schema is installed only with an exact confirmation. No schema changes
  occur on bot startup.
- Existing archive PK conflicts are never overwritten; every record,
  including existing rows, is decompressed and verified against the source
  snapshot before the single destination transaction commits.
- Migration logs contain table names, row counts and aggregate source size
  only. No JSON payload, member IDs, channel IDs, credentials, DSNs,
  sensitive payload hashes or downloadable archives are published.
- Four source tables only. Ticket relationships, activity ledger, live
  configurations, moderation retrieval, voice-bot records and Cinema
  features remain untouched by this slice.

## Mobile-only execution order after exact-head CI and merge

In GitHub Actions, select **Aiven Compressed History Archive (manual, guarded)**:

1. Run mode **preview**. Both databases are read-only. Confirm the
   four expected source counts and verified destination identity.
2. Run mode **install**, with exact confirmation
   INSTALL_AIVEN_HISTORY_ARCHIVE_SCHEMA_ONLY. This only creates the
   private archive schema/table/index on Aiven, not source data inserts.
3. Run **preview** again to confirm schema exists.
4. Run mode **archive**, with exact confirmation ARCHIVE_AIVEN_HISTORY_ONLY.
   It transfers original rows and verifies transactional destination parity.
   Failed/uncertain imports never authorize release. Repeat safely after
   diagnosis; conflicting original rows fail closed.
5. Capture counts in GitHub logs. Successful import is not current-source
   parity forever: source events that occur later need catch-up before cutover.

The workflow requires exact main checkout, explicit owner dispatch,
source and Aiven repository secrets and independent focused tests.
It never creates a downloadable data artifact or deploys Discloud.

## Still required before Supabase can be retired

- A current **encrypted, independently restorable** source backup.
  Previous Backup #9 was dated October 13; it cannot establish recovery
  of new October 19 data off provider.
- Migrate live ticket records, panels, notes, form responses, staff access,
  member activity, reputation and other feature-owned source tables,
  preserving original keys and foreign relationships.
- Build lean canonical, queryable PG18 repositories and equivalent RPCs,
  with read/write permission checks, owner scoping and actual Discord tests.
  Archived rows are not suitable as direct live API tables.
- Verify cross-database counts/hashes, late-write catch-up, retention
  policies, performance, connection limits, rollback, live Discord/Discloud
  canary and explicit runtime cutover authorization.
- **No automatic deletion or pruning anywhere.** Retention changes require
  verified off-database backup and restore behavior, not just fewer bytes.
