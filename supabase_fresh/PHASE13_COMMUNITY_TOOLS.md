# Phase 13: private Community Tools state (one migration slice)

Live original shared Supabase (2026-10-20) has 4 original `dank_stickies` configurations, **2 enabled**; **0** poll rows or configured poll-mode stickies; and 3 `dank_quiet_notices` configurations, all enabled. Each maps to a known guild. Their active bot implementations use `stoney_verify/community_tools_service.py` and `stoney_verify/community_quiet_notice_service.py` with private table operations, plus `save_dank_sticky_bundle`, `record_dank_quiet_notice_activity`, and `clear_dank_quiet_notice_delivery`.

The source is the four original canonical migrations, copied without modifying runtime semantics, followed by tighter backend-only grants. It preserves `bigint` Discord IDs, original DB constraints, message tracking fields, and atomic RPC behavior. Rollback-only isolated SQL smoke covers actual service_role writes, sticky save, monotonic quiet notice heartbeat and stale-message no-clear, plus private RLS.

**Only the offline dedicated candidate** `viamriafkbqhquvrsnua` may receive this SQL and privately reconciled source settings. The original source, Discloud production bot, and its Discord messages are not modified. Configuration history and tracked last-message IDs must remain intact for review; after final source delta and bot-authority validation, no replay, duplicate messages, or bot-origin guessing is permitted. No poll history existed.

Outstanding blockers remain: 3,369 original staff_action_logs and 4,756 activity_feed_events (moderation evidence/retention), durable bot queue schema with 531 terminal jobs that must NOT reexecute, completed bot_commands, and voice/Cinema sessions requiring lifecycle policy. Production cutover is NOT authorized. Backup, off-host restore, measured canary and final source delta still mandatory.

This branch should auto-retire only after exact-head merged PR under the guarded daily branch workflow. Never remove the linked Discloud branch or rollback refs.


## Verified offline installation and original-state reconciliation

As of October 20: versioned `202610200022_dankshield_community_tools` was successfully applied to the new offline candidate. The committed migration and rollback-only SQL smoke were first executed together inside a single rollback transaction. The SQL smoke also passed after installation.

Using the two connected Supabase projects, a separately bounded **transactional** transfer populated all four original `dank_stickies` rows, all zero `dank_sticky_polls` rows and all three original `dank_quiet_notices` rows. Source-snapshot-to-target identity of guilds and the full JSONB rows was verified **inside the transaction before commit**, then rechecked from the independent target. Import uses the original PostgreSQL `jsonb::text` payload as a string, never decodes bigint Discord IDs to JavaScript Number; original long Discord snowflakes and dates are preserved exactly. Prior to commit, a rollback copy passed. Source records, configured enables and last-message ownership fields were retained unchanged.

**Related precision repair:** an earlier direct transfer of `dank_cinema_users` (1 row) and `dank_cinema_user_media` (10 rows) used JavaScript Number JSON decoding, which risks rounding 64-bit Discord IDs. A guarded empty-target-equivalent 1+10 replacement was performed on the **offline dedicated candidate only**, preserving source JSONB full text and passing equality checks before COMMIT. A fresh independent query of both databases returned exactly matching serialized JSONB user and media records. No source mutations, Discloud secrets or runtime changes.

**Not yet certified:** Discord message ownership and channel existence at cutover, late writes to source config, production canary and Discord behavior, off-host restore. No assertion that the original source's public tables are adequately RLS-protected.
