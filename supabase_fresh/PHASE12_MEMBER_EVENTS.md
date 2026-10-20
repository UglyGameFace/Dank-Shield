# Phase 12: member event ledger parity (sole active migration)

The original shared Supabase has **632 original member_events records** (audit on 2026-10-20), all guild-scoped to currently known guilds, all original UUIDs distinct, total original JSON payload ~805 kB. This table is actually used by Dank Shield's member intelligence, verification and moderation feedback services. It was absent from the offline dedicated Supabase despite the activity_feed_events table and activity_feed_log function already existing.

The dedicated offline migration restores exactly the 13 source metadata fields and UUID primary key, indexed guild/user and guild/event chronology, service-role-only access, and the **original** member_events → activity_feed_log trigger mapping for NEW events. Original source DB has RLS disabled/public grants; dedicated uses backend-only private RLS.

Historical import must preserve all source fields without changing member evidence or IDs. It MUST disable only the member_events_to_activity_feed_ai trigger while backfilling, reenable it *inside the same transaction*, and compare all inserted rows before commit, to avoid retroactive public or moderator feed events. A rollback-only smoke confirms live trigger operation and muted historical imports.

No source records deleted, no production Discloud config change, no historical staff actor evidence dropped; source remains live. Every feature needs late-delta reconciliation before cutover. PR should merge only after CI and candidate smoke; the same phase still has old staff_action_logs, stickies, quiet notices, and owner-scoped queue classification pending.

**Up-to-date progress note:** 67 common tables plus 14 source-only tables exist. Table count parity does not guarantee full migration readiness. Active original-source pending voice verification states and old Cinema session resumes still need policy and Discord state checks. 210 old verification tokens are already expired and should not be reissued.
