# Dedicated Dank Shield Phase 5: Invite Shield runtime contracts

Project: `viamriafkbqhquvrsnua` (new offline candidate). The original shared Supabase and Discloud are **still live** and unchanged.

The canonical source migration `supabase/migrations/20260802225500_durable_invite_stats.sql` and runtime `stoney_verify/durable_invite_stats.py` require `dank_invite_block_stats`, `dank_invite_block_events`, and `record_dank_invite_block_event(text,text,integer,bigint,text)`. Before this slice all three were missing on the candidate.

The source-verified config snapshot already holds **8,317 cumulative blocked-invite counts across 3 of 12 guilds**. Migration 015 seeds monotonic high-water counts from these saved config buckets (4 JSONB locations plus legacy direct forms), preserving the totals while **not importing old event history**, which the owner explicitly elected to omit. It imports no message content, tokens, member identifiers or previously deleted messages.

An empty future-facing hash ledger is required for the atomic de-duplication contract: one message may be seen by Discord create, edit and recovery listeners, even across shard restarts. Only a hash, guild, blocked-code count, source label and timestamp are stored. Abolishing the ledger outright would silently double-count retries. Do **not** implement blind automatic hash TTL yet: expiring hashes can admit old Discord recovery replays, and their accurate exactly-once statistics requirement needs a safe expiry/replay-window design. We will measure/define a bounded hash-retention contract separately before 10K/100K growth; no perpetual-event-retention promise or production canary has been made.

The replacement RPC uses `SECURITY INVOKER`, private service-role privileges, restricted `search_path`, per-guild stat floors, and the same 5-argument client contract as the old SQL. It is additive only, with no Discord bot code changes. Both tables have RLS enabled and public/anon/authenticated privileges revoked.

Rollback-only `tests/invite_stats_smoke.sql` tests positive increments, replay idempotency, two-guild isolation, seed floors, validation and authorization. The pre-apply and post-apply SQL checks must both succeed. Existing guild config, role, member and ticket counts must remain unchanged.

**Remaining blockers:** we have NOT connected/read the old Supabase `dank_invite_block_stats` rows, so the new 8,317 snapshot total is a *floor*, not a claim of exact source parity. Production may advance after the snapshot. Before any cutover, verify old source totals for each guild, import the new higher values (never reset to 0) and prove delta parity, without old event history. Remaining moderation/AntiNuke, Community Hub, Cinema, profile contracts, recovery, load/egress testing and owner-approved canary remain blocked.

Branch hygiene: one `migration/dankshield-*-YYYYMMDD` PR, eligible for the nightly exact merged-SHA retirement. No production/backup branches are deleted.
