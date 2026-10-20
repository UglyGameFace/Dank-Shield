# Dedicated Supabase: bounded retention and FK-index slice

Project: `viamriafkbqhquvrsnua`. The shared source remains the live bot's only database. Do not apply these migrations to the shared source or deploy new credentials.

## Required order

1. Phase 3 member/history migrations and data snapshot must already be installed.
2. Apply `202610200011_dankshield_retention_and_indexes.sql` to the new project.
3. Run `tests/retention_smoke.sql` on the **new project only**: every test write is enclosed in BEGIN/ROLLBACK, and any error blocks approval.
4. Apply `202610200012_dankshield_retention_schedule.sql` on the **new project only**; inspect `cron.job` and future `cron.job_run_details`.
5. Repeat Supabase security and performance advisors, plus the prior ticket/member rollback-only tests. Confirm no source/destination operational data changed beyond intended schema/index modifications.

## Exact behavior

- The original **two** history triggers still detect meaningful config/category changes. Their obsolete, duplicated raw pruning blocks now call one private helper. Only the newest **50 automatic versions per guild/config type** are kept. All `is_manual=true` restore points survive. The Python manual-backup cleanup path is updated to match. Old manual snapshots cannot be silently swept merely because staff changes configuration repeatedly.
- `dank_maintenance.purge_expired_verification_tokens(500)` removes at most 500 tokens per run, only if their `expires_at` is **more than 72 hours past**. It covers used and unused tokens and takes row locks with SKIP LOCKED; results disclose only a deletion count, not tokens. Invalid batch sizes fail. The private schema is not exposed through Supabase REST, and anon/authenticated execute is revoked.
- After the explicit scheduling migration, pg_cron runs the token purge **hourly at minute 17 UTC**; its own completed job-run records older than 14 days are pruned **weekly**. Both jobs must be monitored for errors and backlog; a cap is not a throughput guarantee.
- Reverse FK lookup indexes cover `tickets.category_id` and `ticket_form_responses.ticket_id`. The existing guild/category index remains for guild queries. The unused-only token expiry index is replaced with an all-token expiry index to support the new purge. Leave other recently unused indexes alone until realistic traffic measurement.

## Important exclusions / why

No automatic age-based deletion of tickets, transcripts, moderation/AntiNuke evidence, member/role state, activity feed, security actor reputation, configuration, private proof records or session/worker receipts. Their lifecycle, legal and rollback semantics need separate explicit policy and test coverage. No full-table `VACUUM FULL`; standard autovacuum remains on. Deleted rows normally free pages for reuse, not immediately shrink allocated files.

The phase 3 snapshots may already be stale while the old bot keeps writing. Final delta reconciliation, complete feature/RPC parity, source backup/restore, egress/latency/load checks, Discord/Discloud canary, and **owner-authorized cutover** remain blocked. The runtime Python change is safe for source compatibility but is not a production deployment instruction.

Rollback if needed: first `select cron.unschedule('dankshield-expired-verification-tokens');` and `select cron.unschedule('dankshield-cron-run-log-retention');` **on the dedicated candidate only**. Keep existing pg_cron extension if other consumers exist. Do not blindly restore the old manual-deleting pruning functions; changes should be reversed deliberately from a verified prior schema backup.
