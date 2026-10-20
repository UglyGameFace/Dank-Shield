# Phase 13: private Community Tools state (one migration slice)

Live original shared Supabase (2026-10-20) has 4 original `dank_stickies` configurations, **2 enabled**; **0** poll rows or configured poll-mode stickies; and 3 `dank_quiet_notices` configurations, all enabled. Each maps to a known guild. Their active bot implementations use `stoney_verify/community_tools_service.py` and `stoney_verify/community_quiet_notice_service.py` with private table operations, plus `save_dank_sticky_bundle`, `record_dank_quiet_notice_activity`, and `clear_dank_quiet_notice_delivery`.

The source is the four original canonical migrations, copied without modifying runtime semantics, followed by tighter backend-only grants. It preserves `bigint` Discord IDs, original DB constraints, message tracking fields, and atomic RPC behavior. Rollback-only isolated SQL smoke covers actual service_role writes, sticky save, monotonic quiet notice heartbeat and stale-message no-clear, plus private RLS.

**Only the offline dedicated candidate** `viamriafkbqhquvrsnua` may receive this SQL and privately reconciled source settings. The original source, Discloud production bot, and its Discord messages are not modified. Configuration history and tracked last-message IDs must remain intact for review; after final source delta and bot-authority validation, no replay, duplicate messages, or bot-origin guessing is permitted. No poll history existed.

Outstanding blockers remain: 3,369 original staff_action_logs and 4,756 activity_feed_events (moderation evidence/retention), durable bot queue schema with 531 terminal jobs that must NOT reexecute, completed bot_commands, and voice/Cinema sessions requiring lifecycle policy. Production cutover is NOT authorized. Backup, off-host restore, measured canary and final source delta still mandatory.

This branch should auto-retire only after exact-head merged PR under the guarded daily branch workflow. Never remove the linked Discloud branch or rollback refs.
