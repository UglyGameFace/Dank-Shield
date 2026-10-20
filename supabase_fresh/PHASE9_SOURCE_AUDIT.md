# Phase 9: Source metadata and missing moderation-history discovery

**Status:** one read-only diagnostic step in the existing dedicated Supabase migration task. No live source data was changed, and no additional candidate database schema is created in this phase.

Source project ref `hyncpbvqyjojxhjzcdzg` is already pinned by the repository's previous Aiven staging code and independently verified against the URL through `validate_source_dsn`. The owner-only manual workflow `Dedicated Supabase Source Metadata (manual, read-only)` calls the original shared Supabase PostgreSQL service privately through the **existing GitHub Actions secret `DANK_SOURCE_DATABASE_URL`**. Never paste this connection string into ChatGPT or a GitHub issue.

## What the audit actually reports
- Preapproved Dank Shield table **presence**, including `warns`, `raid_events`, `audit_events`, `audit_logs` and `staff_metrics`, Community Hub's 16 tables, profile 3 tables, Cinema 10 tables and other documented shared-source dependencies.
- A capped record probe per table (up to 2,000), with `count_at_least_cap` rather than falsely representing capped counts as exact.
- Schema fingerprints from *column names/types/nullability/identity/default presence* and PK/unique/FK column names; **only five currently missing moderation/staff tables** include sanitized columns and key metadata in the log, to guide source-compatible migrations.
- No row values, raw member IDs, voice evidence, ticket bodies, credentials, email addresses, OAuth state, source DSN, check-expression literals, SQL defaults or other applications' table scans.

Connection is read-only at the server session and transaction levels, inside repeatable-read isolation, with a 4-second statement timeout, 1-second lock timeout and 45-second idle transaction limit. Queries are allowlisted, parameterized or use safely quoted PostgreSQL identifiers. Failure outputs are redacted. This is **not** a complete data parity scan, snapshot import, or license to delete data.

## Run from Android GitHub after this PR merges

1. Open the GitHub Actions workflow **Dedicated Supabase Source Metadata (manual, read-only)** on **main**.
2. Choose **Run workflow**, confirmation `AUDIT_DANKSHIELD_SOURCE_METADATA_ONLY`.
3. Read the `Inventory original shared Supabase table metadata only` step. If the job says the source DB secret is missing or cannot connect, correct the private repository secret or connectivity in GitHub Settings, **not in chat**.
4. Report only the high-level counts/presence and schema metadata (no raw user data). The output can guide the next missing `warns` and moderator event schema slice, followed by separate selective source-row reconciliation.

**Blocked until independently proven:** original Profile Cards privacy settings, member-caption consents, Cinema watch history and watch progress, Community Hub user opt-ins/settings/active sessions/managed resources, staff stats, source Invite Shield count catch-up, moderation evidence, current member/ticket deltas, backup+restore, and runtime canary. The first 66 candidate tables are schema capability, not source parity.

The old shared Supabase remains production. No Discloud deployment, credentials change, other-bot record copy, new shard, or cross-guild enforcement is permitted. Guarded post-merge cleanup remains scheduled; the current branch inventory is above target and deletion has not yet been observed.
