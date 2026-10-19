# Aiven PG18 auxiliary original staging: tickets, members, verification

**Status:** Code pending PR CI, exact-head merge, and owner manual workflow.
Nothing here is live PostgreSQL-backed Discord functionality. Source Supabase
remains authoritative. Discloud Auto Deploy is OFF.

## Source ownership and snapshot inventory

The source is a shared Supabase PG17 project that also supports Idle Grow Op.
The 18 chosen tables have reviewed PostgreSQL primary keys and belong to
Dank Shield's tickets, members, verification and supporting safety flows.
They do not overlap the already-copied 15 operational, four historical
or 12 guild-config source tables.

Read-only source audit on October 19:

| Area | Source tables | Recorded rows |
| --- | --- | ---: |
| Members/activity | guild_members, member_activity_batch_receipts, member_activity_notices, member_activity_scan_locks, member_events, member_joins | 1,617 |
| Tickets | ticket_automation_settings, ticket_automation_state, ticket_counters, ticket_macros, ticket_messages, ticket_panel_categories | 6 |
| Verification and account safety | verification_tokens, verification_flags, verification_kick_timers, vc_verify_sessions, identity_proofs, manual_alt_links | 220 |
| **Total** | **18 tables** | **1,843** |

The raw source JSON totaled **3,817,200 bytes** (approximately 3.64 MiB).
The source bot can continue writing after the audit, so later workflow counts
can differ. Every original must survive, even rows that have since expired.

**Sensitive token boundary:** verification_tokens and vc_verify_sessions
include credential-like primary keys and potentially time-bound challenge
payloads. They are preserved in a private staging table strictly for
recovery and audit. The bot MUST NEVER treat an offline staged token/session
as valid authentication, even when the data is otherwise lossless. Canonical
live conversion must honor current eligibility, expiry, replay, user/guild
isolation, and fresh source write catch-up.

## Source and destination safety

- Source is the approved project by ID and PostgreSQL server with a forced
  read-only repeatable-read transaction. No arbitrary table names, source
  writes, REST pagination or SQL supplied by workflow inputs.
- Only reviewed 18 primary-key mappings are allowed. All source PK fields
  must be present; duplicate logical keys, oversized rows or partial
  reads abort the run. Preserve original PostgreSQL JSONB text, including
  timestamp representation, numeric precision, NULLs and legacy guild IDs.
- Destination uses Aiven PG18 service identity and verify-full TLS, plus
  requires the previously staged operational originals and verified offline
  guild_config_state. New table: **dank_migration.aux_staged_originals**,
  completely separate from live/canonical tables and archived history.
- Private schema revokes PUBLIC grants; verify every PK, complete row text,
  digest, byte size and any existing originals before one destination
  transaction commits. Reread source on a separate transaction before
  committing to reject ordinary concurrent changes; not a global write
  freeze or a final delta.
- Bound to **20,000 rows, 32 MiB source JSON, 2 MiB/row**, with
  4x source-data provision against the conservative 70%-of-900-MiB
  database allocation threshold. This estimate does not replace checking
  the provider's actual free disk or WAL consumption.
- No automatic retries that clear rows, runtime configuration routes,
  public token access, credential logging, source deletions, artifact
  containing plaintext data, voice-bot changes, auto deploy or cutover.

## Manual Android workflow *after green PR CI and merge*

Open **Aiven Auxiliary Original Staging (manual, guarded)** on **main**.

1. Mode **preview**, confirmation blank: verify source table counts, table
   ownership gate, Aiven service identity, existing 15-table stage and
   offline configurations; no writes.
2. Mode **install**, confirmation **INSTALL_AIVEN_AUXILIARY_STAGING_SCHEMA_ONLY**:
   create only one private staging table and its index.
3. Mode **preview** again, blank confirmation: verify the new table is
   present and empty (unless it is a verified idempotent prior stage).
4. Mode **stage**, confirmation **STAGE_AIVEN_AUXILIARY_ORIGINALS_ONLY**:
   guarded point-in-time copy. Inspect exact source counts and
   **SOURCE-VERIFIED** plus Aiven allocated MiB. No live cutover.

The workflow never dispatches itself. Do not run a newly merged action
on a stale SHA, and do not rerun a failed job if main changed.

## What this DOES NOT finish

This slice preserves more source originals. It does not install canonical
application tables, constraints, RPC functions or runtime repository
adapters, replicate future writes, nor justify turning off Supabase.
Remaining feature tables (e.g. Cinema, Community Hub history, profile,
invite stats and operation jobs) need explicit ownership separation from
Idle Grow before further copies.

For final cutover: complete source owner inventory, canonical schema/RPC
port and real feature tests; verify all linked ticket/message/member/role
relationships and sensitive credential expiry; take a *fresh* full source
encrypted backup and verify its independent off-provider storage; quiesce
source writers, catch up changed rows with complete table+guild+PK+digest
parity; canary the actual Discord bot; define rollback and owner approve
Discloud deployment. Do not delete originals while rollback is needed.
