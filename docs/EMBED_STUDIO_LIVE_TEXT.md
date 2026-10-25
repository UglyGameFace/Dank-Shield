# Embed Studio: hourly Live Smart Text

This optional feature re-renders existing **Dank Shield-owned** announcements
when server information changes. It never sends a new message or pings roles.

## Staff workflow on Android
1. Publish an ordinary Embed Studio post with supported Smart Text, such as
   `{server.member_count}`, `{server.name}` or `{today.utc}`.
2. Open **/dank → Design & Branding → Embed Studio → Edit Posted → Live Text Updates**.
3. Choose the exact tracked bot-owned post and select **Enable Hourly Updates**.
4. Turn updates off from that menu at any time. The already published message
   remains unchanged after disabling updates.

Each refresh rechecks Discord channel/bot permissions, the moderator's current
Manage Server/Administrator privileges, bot authorship, and the exact
guild-scoped published-message record. A manual republish deliberately clears
the old Live Text enrollment, preventing background refreshes from overwriting
the new manual design.

Only text and embed fields are edited. Existing component views are preserved.
Role/self-assign panels, interactive forms and dynamic embed timestamps are
not eligible. Unknown Smart Text expressions are never evaluated.

## Resource and safety design
- Private service-role-only database metadata in
  `dank_embed_studio_live_posts`, protected by RLS.
- Every process claims only due rows owned by its actual Discord shard
  (Discord snowflake routing), with a partial due-time index.
- The worker begins after a 125-second startup quiet window, polls once a
  minute, claims up to four due records per batch, edits at most two in
  parallel, and schedules each future check one hour apart.
- A stable content SHA-256 digest prevents redundant Discord REST edits when
  values have not changed. No full-guild polling, member scans, new messages
  or unseen permission bypasses.
- Missing registry tokens, deleted messages or unsafe content disable the
  live job. Temporary API failures wait for the next hourly claim.
- If enabling the backend fails after guild registry update, that record
  alone cannot cause an edit. Worker requires both matching registry nonce
  and an active database row. Disabling the registry first revokes edits even
  if database acknowledgement fails.

## Deployment gate
Requires additive migration
`supabase_fresh/migrations/202610200033_dankshield_embed_studio_live_text.sql`
on pinned NEW DankShield Supabase. Both
`DANK_EMBED_STUDIO_V2_ENABLED=1` and
`DANK_EMBED_STUDIO_LIVE_ENABLED=1` must be explicitly enabled after backup,
restore test, SQL RLS smoke, Discord Android canary and route verification.
Defaults OFF. This is not a blanket rollout or a 10k-guild performance
guarantee.

## Limitations
This is hourly Smart Text refresh, not arbitrary live dashboards or custom
internet API data. Recurring announcement campaigns, full same-document
multi-moderator co-editing, indexed catalogs beyond the current UI limits,
automated quota enforcement and orphan cleanup remain separate work.
