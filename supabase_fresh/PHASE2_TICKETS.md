# Phase 2: tickets, panel configuration and current audit functionality

**Target**: dedicated DankShield Supabase project `viamriafkbqhquvrsnua`.
**Source**: shared Supabase project, remains **live authority and sole bot writer**.
**Status**: offline/schema-only candidate with source-verified snapshot, **not cutover ready**.

## Schemas deployed (new project only)

- `202610200002_dankshield_ticket_core.sql`: 13 current ticket/panel tables,
  223 source-checked columns, 45 original constraints reviewed (including
  foreign keys) with duplicate historical indexes consolidated. Each table
  enables RLS and explicitly restricts service-role-only access.
- `202610200003_dankshield_ticket_audit.sql`: empty, RLS-protected
  `activity_feed_events`; `activity_feed_log` and its two SQL helpers;
  timestamp and ticket-compatibility triggers; message, note, ticket
  creation and update audit triggers. Imports **no old activity-feed events**.
- `202610200004_dankshield_ticket_allocator.sql`: original
  `reserve_ticket_number` PostgreSQL transaction-safe allocator
  (service-role-only) and four meaningful channel/category unique indexes.
  Exactly two legacy groups share historical ticket numbers; a global ticket
  number UNIQUE was deliberately **not** added. The allocator starts above
  the greatest historical guild ticket number and the persistent counter.

## Independently verified in source and destination

| Table | Matching full original rows |
|---|---:|
| ticket_categories | 194 |
| ticket_panels | 9 |
| ticket_panel_presets | 9 |
| ticket_panel_rules | 9 |
| ticket_panel_categories | 0 |
| ticket_counters | 4 |
| ticket_macros | 0 |
| ticket_automation_settings | 0 |
| ticket_automation_state | 0 |
| tickets | 115 |
| ticket_notes | 5 |
| ticket_messages | 2 |
| ticket_form_responses | 2 |
| **Total** | **349** |

Full-row source and destination content fingerprints matched for all 13,
using ordered per-record PostgreSQL `to_jsonb(row)::text` digests.
The offline 34 critical security/config rows from phase 1 remain intact.
Audit insert/update test verified message, note and close events are correctly
scoped to the ticket's guild, then rolled back. Allocator test proved
sequential claims and floor calculation, then rolled back. No simulated
ticket action persists.

## Intentionally remaining blockers

- The `capture_ticket_category_version` trigger depends on authoritative
  `guild_config_versions` history and must be restored when config-history
  schema is installed. We must not cut over with this audit dependency missing.
- `activity_feed_events` initially has no historical rows by owner request.
  Do not claim old activity history survived here; it is still retained in
  the prior source and the Aiven history archive.
- Ticket-specific staff/permission/managed-category RPC calls and production
  workload must pass a full integration review before cutover; this phase
  does NOT claim complete feature parity.
- Member verification, identity proof, role truth, member lifecycle, Cinema,
  Community Hub, alt detection and security worker data are separate migrations.
- Changes to the old project after this snapshot require a final delta
  reconciliation, including ticket statuses and counters, before any cutover.
- Do not merge older `supabase/migrations` blindly or change Discloud
  credentials until owner-authorized integration checks and rollback pass.

Test SQL: `supabase_fresh/tests/ticket_core_smoke.sql` runs in a transaction
that unconditionally rolls back. Never run its writes against production.
