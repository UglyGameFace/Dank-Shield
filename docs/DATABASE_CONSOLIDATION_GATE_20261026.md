# Database consolidation gate: IdleGrowOp → Discloud archive, DankShield primary, Aiven retirement

Status: **AUDIT ONLY. No source deletion, data transfer, credential rotation, or production cutover authorized by this document.** 2026-10-26.

## Owner-selected architecture
- **Dedicated DankShield Supabase** (`viamriafkbqhquvrsnua`): intended live primary, pending runtime identity confirmation.
- **IdleGrowOp Supabase** (`hyncpbvqyjojxhjzcdzg`): old shared source. Keep intact until independent archive and complete parity verified.
- **Existing Discloud PostgreSQL**: independent backup/recovery target. Store IdleGrowOp snapshot in an isolated namespace; do not overwrite existing bot or voice data. Verify physical capacity, isolation, credentials, and actual restore before claiming a backup.
- **Aiven PostgreSQL 18**: retirement candidate, not yet approved for deletion. Inspect its staged 12 guild configurations and any archive rows; prove all unique records preserved first.

## Verified live read-only counts (2026-10-26)

| Table | IdleGrowOp | DankShield |
| --- | ---: | ---: |
| guild_configs | 12 | 12 |
| tickets | 115 | 116 |
| guild_members | 820 | 844 |
| member_activity_ledger | 309 | 331 |
| staff_action_logs | 3369 | 3369 |
| activity_feed_events | 4760 | 4817 |
| guild_config_versions | 920 | 171 |
| verification_tokens | 210 | 0 |
| dank_community_sessions | 1 | 0 |

**Counts are not parity.** The 749 fewer configuration-history rows in DankShield require a lossless archive and reconciliation. Verification tokens may be ephemeral: establish ownership, expiration and security semantics before moving or expiring them. The LFG session must be evaluated for validity/expiry, not blindly copied. Greater target counts may represent legitimate newer writes; never replace target tables with source snapshots.

## Execution gates, in order
1. **Identify active runtime:** confirm Discloud production `SUPABASE_URL` project ref using a secret-safe identity probe; confirm every feature's datastore, not just the main client. Record current rollback config without printing credentials.
2. **Ownership + schema inventory:** classify every old shared table by application; compare schemas, migrations, policies, functions, indexes, FK and logical keys. Explicitly review LFG missing schema and migration failures. No generic-table bulk copy.
3. **Independent, restorable source capture:** snapshot the entire IdleGrowOp source before filtering. Encrypt, verify checksums, schema, roles/policies as appropriate, and prove restore into an isolated disposable database. Protect sensitive member/moderation data.
4. **Discloud preflight:** confirm actual available shared SSD, existing database roles/schemas, backup retention and private VLAN. Create a *separate* archive namespace only after reviewing grants and an explicit write gate; never overwrite existing voice-bot records. Validate capacity for WAL, indexes, and future growth.
5. **Archive transfer:** use a consistent source snapshot and resumable idempotent import; verify per-table counts, primary-key sets, canonical row digests, FK integrity, and sampled restoration. Preserve immutable original history and moderation evidence. Keep original IdleGrowOp live until verified.
6. **Reconcile primary:** compare per-key payloads and revisions, distinguish source-only, target-only, and conflicting records; resolve conflicts deliberately. Never overwrite newer target state. Run delta catch-up after a bounded write freeze or consistent synchronization gate.
7. **Disaster recovery drill:** restore Discloud archive into a disposable target, prove completeness and application-relevant retrieval. Test scheduled incremental backups, monitoring, alerting, bounded retry, and no global deletion of security records. Do not claim Discloud backup is operational until this passes.
8. **Aiven retirement:** inspect Aiven read-only, reconcile all staged/config/history records with independently recoverable copies, verify no production client or scheduled job uses it, then obtain explicit owner approval to terminate service and revoke credentials. Do not delete it just because it is no longer preferred.
9. **IdleGrowOp retirement:** only after dedicated primary production canary, feature tests (LFG, verification, tickets, moderation), parity, rollback rehearsal, and Discloud recovery drill; explicit separate authorization required.

## Known constraints
- Existing Aiven migration documentation reports 12 guild configs staged but no confirmed history archive import as of 2026-10-19; current Aiven contents **not inspected**.
- New DankShield contains additional tickets/members/feed rows, and fewer history/token/LFG rows; do not treat source as authoritative for every table.
- The Discloud database is not accessible through the currently connected tools; **no storage capacity, backup writes or restore can be verified here**.
- Existing migration scripts/workflows must be reviewed and reused, not replaced by a new untested bulk-copy tool.
- No destructive cleanup, Aiven termination, or DB writes are part of this PR.
