# Share Router #390: opt-in Download channel and DM delivery

This is the limited, first phase of a user-requested **download-to-DM** feature.
It is not a general-purpose unlimited downloading or public file host.

## Member flow

1. Staff open Dank Shield Share Router and select **Create Download Channel**.
   The bot creates one ordinary text channel named `video-downloads` and saves
   that channel's ID alongside the guild's existing Share Router routes. Nothing
   is activated in unrelated guilds and existing proxy routes are untouched.
2. A member posts **one public supported media link** to that channel.
3. The existing, single canonical Share Router message listener dispatches
   that exact channel to the bounded Download handler. The handler uses the
   current provider-neutral resolver, bounded HTTP download, and optional
   safe HLS/DASH remux, without storing media in PostgreSQL or Supabase.
4. A verified/connected Discord bot DM receives the actual video attachment
   only if it is within the DM-safe limit. The handler uses a conservative
   **19 MiB** cap below Discord's documented 20 MiB default bot cap, which is
   unrelated to a server's boosted Level 3 upload allowance.
5. If the media is too large, unavailable, inaccessible or not remuxable, the
   member receives a **source link in DM**. This is NOT a downloaded attachment
   or proof that the browser offers a save-file flow. A server-boosted guild
   cannot raise the size limit of the bot's private DM.
6. If Discord rejects the DM upload as too large, the bot sends an honest
   fallback link. If the bot cannot DM the member, it leaves the original link
   in the channel and reports that privacy settings may be blocking DMs.
7. Only after the DM succeeds may the bot delete the original request. Source
   cleanup requires Manage Messages; a message may otherwise remain visible.

### Guild management and safety

- Opt-in and explicitly configured **per guild**, using the existing
  `data/share_routes.json` persistence owner. **Disable Downloads** turns
  off processing but does **not** delete a Discord channel behind staff's back.
- Two simultaneous transfers across the process maximum; never queue unlimited
  requests. A 120-second per-guild-member cooldown, with bounded memory, also
  prevents member-driven reply storms.
- Only safe public HTTP(S) media URLs are processed, with one link per message.
  Existing resolver/private-IP/redirect guards and byte/time limits still apply.
  No arbitrary HTML/script/filesystem URLs or torrent downloads are introduced.
- Media bytes are temporary and automatically closed/unlinked after sending.
  No retention of personal video or raw media payloads in the bot's databases.
- Intake channel is an ordinary text channel, **not a confidential inbox**.
  Members may see source links until the bot deletes them; use trusted community
  permissions and do not ask members to upload private files. DMs can be
  disabled by recipients.
- Never auto-enable this in all guilds or scrape/private-login media.

## True above-limit private file downloads: separate acceptance gate

A private **signed temporary HTTPS link hosted by an owner-controlled storage/
streaming service** is the recommended option when a file exceeds Discord's
DM attachment cap. Implementing this requires a confirmed **publicly reachable**
host/download route, bounded storage or safe passthrough, per-request token
expiry, authentication/authorization and abuse controls, accurate size caps,
bandwidth accounting, and cleanup. A query-token URL alone is a bearer secret,
not the same as Discord OAuth binding to the intended recipient.

Neither public HTTPS ingress capacity nor egress/storage limits of the current
Discloud application have been verified for this use, so the larger-file
service is **not** installed by this slice and may not be promised as unlimited.
A hidden boosted-server attachment with a Discord CDN link may allow a limited
download workflow up to a guild cap, but this also needs explicit review for
signed-URL privacy, link expiry and deletion semantics. Do not silently use a
server message as private storage or imply it lifts the DM attachment limit.

## Live tests required before enabling

- Two guilds: only the staff-configured channel processes messages.
- One public progressive MP4 under the bot DM limit reaches only the sender,
  is playable/downloadable in Discord Android and desktop, and cleans source
  only when DM delivery actually succeeded.
- A separate-audio/video source and short HLS/DASH clip remain within time,
  temporary-file and byte budgets, or return honest links.
- A file over cap, failed downloader, failed DM (50007), API 413, no
  Attach/Manage Messages, disabled feature, repeated posts and concurrent
  requests preserve expected data and state.
- Staff UI creation/disable is accessible on Android and does not interfere
  with regular Share Router proxy routes.
- Live Discloud CPU/RAM/egress and storage quotas are measured, not guessed.
