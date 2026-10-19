# Aiven PostgreSQL 18: operational first-slice staging (15 tables)

**Status (2026-10-19):** Offline code and workflow pending exact-head CI,
review/merge, and manual preview/install/stage. Nothing in this PR routes a
running Discord command to PostgreSQL; Supabase is still authoritative.
Discloud Auto Deploy must remain OFF. No auto-pruning or source writes.

## Why this slice, and what has already been verified

- The original 12/12 guild_configs shadow and first-stage records are already
  verified. The separate offline guild-config state-seed workflow exists; an
  accepted production state seed has NOT been independently established here.
- Compressed Aiven history archive import #5 succeeded and printed
  SOURCE-VERIFIED for 15,674 config-history/moderation/feed/invite events.
- A live Supabase read-only audit on October 19 found exactly **997 rows**
  across the 15 SOURCE_KEYS tables except guild_configs; raw JSON totals
  approximately 682.4 KiB. All primary keys are non-NULL and unique.
- All checked ticket category/note/form relationships resolve in the current
  Supabase snapshot. There is 1 ticket outside the known closed/resolved/deleted
  states. These facts can change while the bot keeps writing.
- **Legacy record exceptions requiring later feature-owner reconciliation:**
  10 guild_role_rules have non-snowflake guild_id strings; 5 ticket_notes have
  NULL guild_id. They are NOT dropped, rewritten or silently assigned to another
  guild. Their original source PK, JSON text and NULL/legacy scope remain intact.

### Source tables preserved without transformation

| Category | Tables |
| --- | --- |
| Security/roles | guild_security_settings, guild_role_rules, guild_roles |
| Member state | member_activity_ledger, member_activity_tracker_state, member_cleanup_settings, member_verify_timers |
| Tickets | ticket_categories, ticket_panels, ticket_panel_presets, ticket_panel_rules, tickets, ticket_notes, ticket_form_responses |
| Community Hub | dank_community_hub_settings |

The existing source allowlist stoney_verify.core_data_stage.SOURCE_KEYS is
the authority. Any difference from the reviewed 15 names fails closed.

## Implementation and controls

The new table is **dank_migration.core_staged_originals**, never the active
guild config state, old migration staging, history archive or public schema.
It preserves exact PostgreSQL 17 to_jsonb(t)::text, including numeric
precision, guild_id (even NULL/legacy), source PK JSON, a deterministic hashed
key, original-byte SHA-256, byte count and read-only staging timestamp.
The source snapshot is repeatable-read and uses direct, READ ONLY PostgreSQL,
not client-side paginated Supabase REST reads.

The manual workflow has only preview/install/stage choices and distinct exact
confirmations. Every staged row, plus every pre-existing row, must match the
source's original JSON text, primary key and SHA before a single transaction
commits. The importer performs a separate fresh source reread to catch
ordinary intervening writes and reject a stale snapshot. Neither step
constitutes an exclusive writer lock or a final cutover.

Limits are 20,000 records, 24 MiB total original source JSON, 2 MiB maximum
per row, and a conservative 4x source-size allocation check against 70% of a
900 MiB Aiven database budget. Actual provider disk/WAL usage requires a
separate read-only capacity check. Import and current source may subsequently
diverge if production writes continue.

### Mobile GitHub workflow after exact-head CI and merge

1. Open **Aiven Operational Core Staging (manual, guarded)** on the main
   branch, mode **preview** (blank confirmation). This checks source records
   and destination identity without writing.
2. Choose mode **install** and confirmation
   INSTALL_AIVEN_CORE_STAGING_SCHEMA_ONLY. This creates one private staging
   table/index only, with no source copied.
3. Repeat **preview** and check schema plus source row counts.
4. Choose mode **stage** and confirmation
   STAGE_AIVEN_CORE_OPERATIONAL_ONLY. This copies and verifies all
   997 current source rows, or the updated count at run time.
5. A successful job must print per-table counts, total payload size, and
   SOURCE-VERIFIED. This is a point-in-time snapshot, not production routing.

**Do not run either write choice until exact-head CI is green.** Secrets
remain in GitHub Actions; never paste PostgreSQL DSNs or certificates in
Discord/chat, and never upload plaintext staging content to GitHub.

## Shared-project table ownership, not automatically included

A read-only enumeration found 87 public source tables. The current 15
operational + 4 historical + 1 guild_configs names account for **20**
distinct table names, not all 87. A green staging run never authorizes
dropping Supabase or claiming all bot features have been migrated.

Important candidate tables OUTSIDE this staging set include:

- Tickets: ticket_messages, ticket_counters, ticket_macros,
  ticket_panel_categories, ticket_automation_settings,
  ticket_automation_state, and possible ticket-related operation jobs.
- Verification/members/security: guild_members, member_events, member_joins,
  member_activity_notices, member_activity_batch_receipts,
  member_activity_scan_locks, vc_verify_sessions, verification_tokens,
  verification_flags, verification_kick_timers, manual_alt_links,
  identity_proofs, guild_security_actor_reputation, raid_events,
  warns, audit_events, audit_logs, and staff_metrics.
- Invite shielding and community: dank_invite_block_stats,
  dank_community_* session/notification/event/partner/report/metric tables,
  dank_quiet_notices, dank_stickies, dank_sticky_polls.
- Member profiles and media: dank_profile_*,
  dank_live_profile_cards, dank_cinema_*.
- Operations: bot_operation_jobs and other bot-specific configuration data.

This is an inventory, NOT a classification that all rows belong to Dank
Shield. The source is shared with **Idle Grow Op**. In particular
global_accounts, guild_profiles, guild_worlds, world, and users may be
other-application data. Do not bulk-copy or delete other bots' records.
Use explicit feature owners, source reference/call-site tracing and
dependency mapping before assigning a table to the final Dank Shield schema.

## Next migration slices and cutover gates

- Reconcile owner scope and possible legacy schema for source role rules
  and ticket notes without inventing guild IDs or removing the records.
- Convert canonical live guild config (including staged offline state)
  and ticket/member/verification/settings repositories on the isolated
  Aiven PG18 side, preserving original IDs and transactional behavior.
- Inventory/migrate remaining non-first-slice feature tables, including
  persistent ticket messages, detailed community/cinema data, reputation
  records and moderation/security counters; the 15-table slice is NOT a
  complete inventory or a full database backup.
- Build per-feature full source/target parity, relational consistency,
  atomic write behavior and rollback tests; run late-write catch-up before
  freezing Supabase writers for a controlled production canary.
- Keep the independent Oct 19 encrypted full-source backup off-platform
  with its passphrase separately, and take a newer validated source backup
  before final cutover. Recovery drill success alone is not independent
  long-term storage or a license to delete anything.
- No bot runtime changes, Supabase deletions, live dual writes, voice bot
  changes, Discloud deployment, secrets removal or cutover are authorized.
