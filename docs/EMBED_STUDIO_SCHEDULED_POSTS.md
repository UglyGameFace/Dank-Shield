# Embed Studio: scheduled announcements (owner-gated)

This adds **one-shot scheduled posts** to the canonical Discord editor.

## Staff workflow on Android
1. Open **/dank → Design & Branding → Embed Studio**.
2. Design a regular announcement; use existing Media Library, Smart Text,
   multiple embeds, and link buttons. Pick the destination channel.
3. Choose **Preview & Publish** to see the private rendered message.
4. Select **Schedule for Later**. Enter an ISO date/time with UTC offset,
   e.g. `2026-11-01T18:00-05:00`. A timezone offset is required; the bot
   never guesses a staff member's region. The job must be between 2 minutes
   and 90 days in the future.
5. Confirm privately. Nothing is posted until the scheduled time. Use
   **Scheduled Posts** to review pending entries and cancel with confirmation.

## Security and behavior
- **Only new posts** may be scheduled, not edits of an existing post.
- Unattended role controls are blocked. Link buttons are permitted.
- Smart Text is resolved at actual send time, not when queued.
- Scheduler rechecks creator's Manage Server/Administrator permissions, bot
  membership, channel existence and send/embed permissions at delivery.
- Uses a **due-row database index**, 8 claims per poll, and 2 concurrent sends,
  with a 125-second post-startup quiet window. In sharded deployments, a
  process only claims rows routing to its owned Discord shard IDs, so one
  process never consumes another process's scheduled posts. If shard ownership
  is unknown the scheduler does not claim anything. No full server scans.
- The database marks each claimed job *before* a Discord HTTP request.
  Crashes and ambiguous send timeouts are **never automatically replayed**,
  avoiding duplicates. Unsent jobs more than 10 minutes late become
  `missed`; confirmed errors become `failed`.
- Public content and link controls use `AllowedMentions.none()`. No pings,
  self-role grants, or arbitrary Smart Text evaluation.
- Claim/finalize requires the pinned new DankShield Supabase database. RLS
  denies public access to stored drafts or scheduled content.

## Rollout
- Apply additive migration
  `supabase_fresh/migrations/202610200030_dankshield_embed_studio_scheduling.sql`
  after successful disposable PG17 smoke tests.
- **Both** `DANK_EMBED_STUDIO_V2_ENABLED=1` and
  `DANK_EMBED_STUDIO_SCHEDULING_ENABLED=1` must be present in the authorized
  canary deployment. Default is disabled. An existing database or merge
  does not trigger scheduled sends on its own.
- Verify an isolated canary send and cancel on Android and Discord API safety
  logs, and confirm no duplicates after a restart or a rejected send.
- Scale using sharding and indexed claims, but the present bounded worker is
  a canary implementation, **not a 10,000-guild load-test guarantee**.

## Known boundaries
Recurring campaigns, dynamic refresh of already published messages,
moderator collaboration/co-editing, and advanced forms are separate
Embed Studio slices requiring additional state machines, security checks
and tests. A scheduled message is currently at-most-once, not guaranteed
exact-time delivery under an outage.
