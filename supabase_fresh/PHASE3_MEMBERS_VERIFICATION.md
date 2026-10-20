# Phase 3: members, roles, verification, activity and config history

**New Supabase:** Dedicated DankShield project `viamriafkbqhquvrsnua`.
**Original source:** Shared Supabase project, still the bot's only live writer.
**Status:** Offline candidate ONLY. This PR does not change Discloud or credentials.

## Schema and security

- `202610200006_dankshield_member_verification.sql`: 16 production-shaped tables,
  322 original source columns, original primary/unique/check constraints and
  focused indexes. Includes `guild_members`, `guild_roles`, `guild_role_rules`,
  `member_joins`, identity evidence, direct/VC verification, member activity,
  durable tracker state, lock/notice/receipt *structure*.
- `202610200007_dankshield_member_audit_triggers.sql`: verification timestamp,
  voice-session timestamps and member-join audit triggers, enabled after the
  member state snapshot to avoid fabricated events.
- `202610200008_dankshield_config_history.sql`: isolated config-history table
  with a **BY DEFAULT identity** version ID, 4 comparison/capture functions
  with fixed search paths and restricted service-only execution.
- `202610200009_dankshield_enable_config_history.sql`: config and ticket-choice
  capture triggers installed **after** seeding the 24 selected restore points.
- `202610200010_dankshield_member_activity_rpcs.sql`: 7 original member-activity
  RPCs, including idempotent batch ingestion, acknowledgement, tracker
  heartbeat/start/failure/resumption. SECURITY DEFINER functions have
  hardened search paths and execution restricted to service_role.

All 34 tables in the new database have RLS. There are no public or ordinary
authenticated table grants or corresponding RPC grants. Security advisor has
no WARN/ERROR, only informational no-policy notices (intentional).

## Verified source snapshots

| Contract | Original rows matching destination |
|---|---:|
| guild_members | 815 |
| guild_roles | 306 |
| guild_role_rules | 10 |
| member_joins | 165 |
| member_activity_ledger | 307 |
| member_activity_tracker_state | 11 |
| Latest version per guild + config kind | 24 |
| **Phase 3 total** | **1,638** |

All imported rows match their source PostgreSQL JSON fingerprints, including
the 449 KB latest configuration version. Historical history source has
920 entries; only 24 latest restore points were copied into live candidate.
The original full versions remain in the prior source and offline Aiven
backups. New version IDs are allocated above the imported source maximum.

Phase 1 + 2 + 3: **2,021** verified records across 34 tables, about
16 MB allocated at this checkpoint. The live source may advance after
these snapshots, so these figures are NOT final cutover parity.

## Deliberate non-imports

- All **210 verification tokens were past their expiry** at inspection.
  Their table exists and works for new tokens; none were copied or
  reactivated. Future source changes need a final active-token reconciliation
  at canary time.
- Ten old voice-verification sessions were not copied. A final active-session
  review and reauthorization process is necessary; never auto-replay tokens
  or grants from expired or canceled sessions.
- Member scan locks and batch receipts are intentionally left empty because
  they belong to the prior runtime process. New worker starts must handle
  stale tracker heartbeat/process IDs safely.
- Empty identity evidence, verification flags, reminder timers and member
  activity notices tables are kept with their original contracts.
- Full 920-row config history, historical activity feed, invite-block event
  ledger and unrelated Idle Grow Op data were not imported.

## Tested on the new project

- Full old/new digest parity for six durable member/role/activity tables.
- Latest source/destination config-history snapshot parity (12 server config
  and 12 ticket-category versions).
- Idempotent `record_member_activity_batch` dedupes a repeated batch ID;
  `ack_member_activity_batches` clears its receipt.
- Configuration and ticket-category changes create new versions via triggers.
- A synthetic member join produces one scoped activity feed audit entry.
- All smoke writes ran inside explicit `BEGIN` / `ROLLBACK` transactions.

## Blocking work before ANY cutover

Review/restore complete runtime RPC and trigger dependencies, notably ticket
category restore and managed category reconciliation, member verification
commands and moderation workflows. Bring over Community Hub, Cinema, profiles,
security/Invite Shield counters, ticket staff/config relationships, active
reminders and current member identity/proof states. Test real Discord features,
database rate/egress budgets, fault tolerance and rollback. Reconcile source
updates at cutover, reset old process ownership and reissue any valid auth
state safely. This migration does not imply a live production switch.
