# Phase 21: Guarded final 34-table offline catch-up

**Status: implementation prepared only. Original Supabase remains the sole production writer. Candidate stays offline.**

The 2026-10-21 independent read-only 34-table inventory found only 25 of the 34 durable table contents matching after source production resumed. Nine changed tables include configurations, member and role state, community metrics, activity state, joins, blocked-invite stats and quiet notices. Matching row counts are NOT matching records.

## Manual-only workflow

GitHub Actions: Dank Shield Offline Final Delta Catch-up (manual).

- Run **preview** first. It checks schema/PK/RLS/privileges across all 34 tables and reports only count differences. It uses read-only database transactions on both projects. Running preview does NOT synchronize anything.
- The separate **stage** mode is for a genuine maintenance freeze only: stop the existing Discloud bot AND all original project writers, maintain an independently restored off-host candidate backup, and enter all three owner inputs: RECONCILE_OFFLINE_DANKSHIELD_ONLY, I_PAUSED_ALL_SOURCE_WRITERS, and the privately retained candidate backup receipt ID. These acknowledgements are not evidence of a real freeze or restorable backup; owner must verify both.
- Source connectivity is explicitly pinned to hyncpbvqyjojxhjzcdzg and ALWAYS read-only even in stage. The only writable connection is pinned to OFFLINE viamriafkbqhquvrsnua.
- Stage locks the 34 offline candidate tables in one transaction, checks column/PK contracts and sensitive permissions, inserts missing source records and updates differing source records by primary key. Candidate-only records block the stage and are NEVER silently deleted. It also blocks unsupported generated column contracts and enforces row/byte limits.
- After every changed table, destination rows must match the original snapshot. Before any candidate transaction commits, a separate fresh read-only source snapshot must still be identical. Any failure rolls the entire candidate stage transaction back.
- No original database mutations, Discord actions, production credential changes, automatic trigger, cron or Discloud deployment occur.

The new workflow does not validate network access from Discloud. The already-existing independent Discloud candidate REST shadow probe remains necessary.

## After stage, not before

Run the separate existing Dedicated Supabase Final Delta Audit from main with source writers still stopped, and inspect its exact 34-table streaming hash parity and live-work blockers. Validate the Discloud candidate REST probe, independently restorable backups, role/verification safety, and rollback. Complete the owner-authorized reversible cutover only after those gates are satisfied.

This PR DOES NOT dispatch a stage, change source data, deploy a bot, or authorize a global Supabase credential switch.


## Failed Oct 21 Stage: exact-config trigger incompatibility and diagnostic hardening

An owner-run Stage stopped rapidly with only `OFFLINE_DELTA_CATCHUP_STOPPED: RuntimeError`.
That generic message cannot distinguish a source writer race, trigger mutation,
record mismatch or authorization issue. Following the failure, a fresh read-only
comparison still found the candidate missing one guild-member row and three
guild-role rows, so successful catch-up is NOT established.

Read-only inspection of the pinned candidate's actual `guild_configs`
triggers identified a concrete parity incompatibility: the candidate's
`trg_guild_configs_updated_at` fires on every UPDATE and overrides the original
record's `updated_at` with `now()`. The same table's
`trg_capture_guild_config_version` writes **excluded** version history on
migration edits. Six existing `guild_configs` rows differed before Stage.

The focused fix allows ONLY these two exact, catalog-verified candidate
USER triggers to be disabled for the exact snapshot copy of
`public.guild_configs`. It verifies the disabled state, re-enables both
before leaving the same candidate PostgreSQL transaction, and verifies their
normal enabled state. Any exception rolls the transaction back, restoring
both data and trigger state. No other trigger, foreign-key constraint,
source database trigger, or production project is modified. If the candidate
schema differs, the stage fails closed, rather than relaxing parity checks.

The stopped workflow now reports a **code-owned allowlisted error category**
instead of a bare `RuntimeError`, but never arbitrary SQL errors, input
values, member IDs, passwords, DSNs or role names. The original exactness,
final 34-table parity, source writer freeze and fresh source snapshot checks
are unchanged. Permission errors changing candidate triggers are NOT bypassed.

The failure alone does not prove which code-owned RuntimeError occurred.
Review focused CI and only retry Stage after a real writer freeze, independent
off-host backup, and explicit owner approval. No Stage retry is automatic.
