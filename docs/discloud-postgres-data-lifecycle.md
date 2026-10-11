# Dank Shield: Discloud PostgreSQL data lifecycle contract

This file governs **future** Discloud PostgreSQL tables and aligns with
`docs/PRIVACY_POLICY.md` section 7. It does **not** authorize deletion from
existing Supabase data, Discord channels, tickets, moderation logs, or
backups. These have their own authoritative owners and retention obligations.

## Before a feature may create durable records

Every new PostgreSQL table must be registered in
`postgres/data_lifecycle.json` and pass
`tests/test_postgres_data_lifecycle.py`. Record its owning subsystem,
purpose, retention class, justified historical reuse, review cadence, exact
stored fields, personally identifying data exposure, retention/expiry rule,
indexes, cleanup interval/batch size, and preservation conditions. Prefer storing IDs, expiry/counters and state, not full messages,
attachments, streaming payloads, or redundant member profiles.

**Feature-owned cleanup:** each table has exactly one lifecycle authority.
Use the smallest available bounded query, index its expiry/scan key, and
remove records only when the owner can establish they're no longer needed.
If the evidence is missing or the database/API is unavailable, preserve
data and retry later rather than guessing. Do not run DDL, mass delete,
VACUUM FULL or unbounded scans on bot startup.

## Keep valuable history, not just more rows

A record can be **durable without being permanent or automatically punitive**.
Future migrations must classify each table as temporary, operational control,
durable security evidence, moderation audit, guild configuration, support
record, member preference, or aggregated analytics. The table registry must
state its specific historical reuse, an owner and a periodic policy review
interval (`review_interval_days`). Review means evaluate necessity and
correctness, **not** automatically delete a row when the review date arrives.

**Existing bad-actor authority:** `stoney_verify/anti_nuke_hostile_actor_runtime.py`
already persists confirmed, guild-scoped AntiNuke hostile-actor reputation
to `guild_security_actor_reputation` in Supabase and a local record. It
tracks classifications, incident counts, first/last seen times and explicit
staff clearance. Its owner must remain authoritative until a separately
approved, validated migration exists. Do not add a parallel PostgreSQL
offender registry or treat a missing Simple Verify response as misconduct.
Its cleared/inactive records should remain distinguishable from current
hostile evidence; no generic PostgreSQL cleanup should affect those data.

**Future security intelligence:** documented enforcement events can be useful
for detecting repeat abuse. A proposed history feature should retain
authenticated Discord IDs, server scope, evidence provenance, incident and
review timestamps, action/reason, source/version, confidence, correction/
appeal outcome and authorized expiry or removal. Preserve verified incidents
according to a documented security-retention policy, not a global 30/90-day
TTL. Mere reports, name similarity, new accounts and other low-confidence
signals must never become confirmed identity or automatic punishment on their
own. Prefer bounded metadata or references over collecting every message or
private content. Cross-server sharing or enforcement needs a separate
authorization/privacy design and cannot be inferred from one server's report.

**Other useful future history:** keep owner-approved moderation/ticket audit
trails for accountability, config/permission revision history for rollback,
member consent/preferences that must survive restarts, verified lifecycle
transitions for restoring access, and privacy-conscious aggregated community
statistics for trend comparisons. Preserve only fields with a concrete
feature or recovery purpose; short-lived sessions, logs, spam windows and
media caches still expire.

No collection, cross-guild reputation network, new security enforcement,
new historical data table, Supabase migration or bulk legacy deletion is
authorized by this retention policy alone.

## Retention classes

- **Temporary sessions, job state and one-off tokens:** expiry-based,
  usually minutes to days, with a bounded grace period for recovery.
- **Operational counters and deduplication:** retain for the duration of
  their enforced limit, then remove only if deleting them cannot reset
  rate limits or duplicate work. For Simple Verify, old rows are purged
  after 90 inactive days **only when Discord confirms the member has left**.
- **Server configuration, permissions and verification authority:** keep
  while the bot is installed and settings apply, until an explicit owner
  action or a verified removal workflow. Never auto-delete merely for age.
- **Moderation, ticket records, exports and audit logs:** follow the server
  owner's retention policy and safety/legal requirements. A fixed global
  TTL is **not** appropriate. Deletion needs authorization.
- **Temporary media, thumbnails and logs:** short bounded TTL, with
  disk and storage quotas appropriate to the owning feature. Database
  columns should not become a media-file cache.

## Capacity and recoverability

A `46 MB used` indicator is not the database's allocation or unlimited
storage. Capacity limits, free space, backup/PITR retention, and restore
capability must be verified in Discloud's dashboard or with Discloud support.
Before migrating irreplaceable records, test a backup and restore. Define
operational warning thresholds (for example 70%/85%/95% of confirmed
allocated capacity); these are **policy targets, not deployed alerts**.

Periodic monitoring should eventually report, per feature, table/row
sizes, dead tuples, oldest unswept expiry, cleanup errors, and available
storage without dumping user data. Never delete historical data solely
because a disk-usage threshold was crossed. Log the cleanup count and
failure reason without exposing passwords, connection URLs or private
content. Keep cleanup cursors bounded and avoid duplicate cleanup workers.

## Change and removal review

For every future schema change, include a migration, registry update,
targeted retention tests (active/expired, disconnected, restart and
concurrent writes), an access/permission review, and an explicit rollback
and data-loss explanation in the PR. Changes to existing records are
opt-in and must be separately authorized. Verify no alternate/legacy
worker is also deleting the same data.
