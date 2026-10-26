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

## Expiring private HTTPS links: implementation and easy setup

The public video delivery endpoint is now owned by the independent Shield
Share Router listener in `stoney_verify/share_router_public_server.py`.
Its `/share-video/...` handlers live in `share_router_external_links.py`.
**Neither file imports Dank Cinema, a torrent client, or a media-seeding
worker.** The HTTPS site uses the existing Site port, without a second web
listener or another database. It never provides public directory listings.

1. Make sure the existing Discloud **TYPE=site** media app is publicly
   reachable over HTTPS. In the application's Discloud environment, reuse the
   Share Router-specific
   `DANK_SHARE_VIDEO_PUBLIC_BASE_URL=https://YOUR-SITE.discloud.app`.
   The historical `DANK_MEDIA_PUBLIC_BASE_URL` remains a transitional fallback
   only until the live Shield cutover is verified; it does **not** restart Cinema.
   Do not use the database private VLAN host. A public URL must be configured
   because
   guessing the public site address or accepting an untrusted request Host
   would create unsafe signed links.
2. After the guarded Shield deployment, restart the running bot/site if an
   environment value changes. Confirm `/health` responds with
   `service: "dank_share_router_public"` and both old `/cinema` and
   `/media/torrent/stream/not-a-token` return 404. Do not restart the old
   Cinema-enabled ZIP.
3. In Discord, open `/dank home` → Share Router → **Create Download Channel**.
   The feature is per guild; existing Share Router proxy routing is untouched.
4. Press **Download Settings**, choose **1 hour**, **24 hours**, **7 days**
   (default), or **30 days** (maximum), then **Test HTTPS Hosting**. A passing
   test creates a tiny temporary private token, reads it through the configured
   HTTPS origin, and revokes it; the URL never appears in Discord.
5. Post one safe **public** video URL in the created channel. A small file
   arrives as a normal DM video attachment. If hosting is online and size/storage
   budgets permit, a larger file is staged privately and delivered in a DM as
   separate **Watch** (inline HTTP video) and **Download** (attachment HTTP)
   links. When hosting is not ready or budgets are exceeded, the bot sends an
   honest original-source link rather than claiming a file is hosted.
6. **Disable Downloads** stops the member workflow without deleting the
   Discord channel or already-created links. Existing links continue until
   their own expiry; if immediate revocation is required, remove the staged
   object or stop the service and invalidate storage.

### Actual limits and confidentiality

- Default per hosted file: **128 MiB**, configurable via
  `DANK_SHARE_DOWNLOAD_FILE_MAX_BYTES` up to the **256 MiB hard safety cap**.
- Default total staged storage: **512 MiB**, configurable via
  `DANK_SHARE_DOWNLOAD_STORAGE_MAX_BYTES` up to **2 GiB**, with a
  **256 MiB free-disk reserve**. Storage-full conditions block *new* staged
  files, rather than deleting other members' unexpired videos.
- Default staging directory: `data/share_downloads`, configurable via
  `DANK_SHARE_DOWNLOAD_STORAGE_DIR` on the same public bot/site container.
  **The current Discloud disk persistence and quota are unverified.** Restart
  and redeploy can have different retention behavior: a 7/30-day URL cannot
  outlive a deleted/ephemeral storage volume. Confirm a persistent disk and
  test reboot/redeploy retention before promising long-term access.
- Each signed link has a **2 GiB default lifetime transfer budget** across
  normal GET and partial Range requests, persisted in its metadata and enforced
  even after restart. This can be adjusted with
  `DANK_SHARE_DOWNLOAD_LINK_EGRESS_BYTES` up to a hard **16 GiB** safety cap.
  HEAD requests do not consume transfer bytes. Once exhausted, the link
  refuses new media requests even if its time-to-live has not ended; aborted
  transfers may still count to prevent an indefinite retry loophole.
- The URL contains an unguessable random bearer token, with only its hash
  retained in a small sidecar manifest. **Anyone with the link can access
  it until expiry**. Anyone who receives a forwarded DM or copies the URL can
  therefore watch/download without Discord login. Do not use for private,
  restricted, illegal or rights-unlicensed material.
- The media endpoint serves only pre-staged local video with safe filename
  identifiers; credentials or source-provider URLs are not embedded in the
  token/manifest. GET and HEAD support byte ranges for browser seeking;
  the Watch route uses inline media type, Download uses attachment disposition.
- Every request checks expiration and the token again. Background cleanup
  runs every 15 minutes (bounded, rotating scan); no valid request can open
  a link after its configured expiration, even before physical cleanup.
  CDN/Discord previews cached before expiry cannot always be recalled.
- The endpoint limits concurrent active transfers; capacity and egress are
  host-account dependent. A signed URL is **not an unlimited-file-size hack**,
  and Discord-native previews remain best-effort across clients.
- Temp media is removed on failed staging, failed DM, and after expiry.
  The live public-host canary is still required to verify actual ingress,
  storage persistence, bandwidth and watch/download accessibility.

Do **not** repoint `DANK_VERIFY_REMINDER_DATABASE_URL` or change PostgreSQL
for this feature; staged media never belongs in the reminder database.

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
- Expiration presets are saved per guild; token does not disclose source URL.
- HTTPS Test Hosting verifies the same external public origin that Discord
  and a user would reach. Check DM Watch and Download links with the member,
  then test Android and desktop Discord clients. Never interpret a passing
  HTTP test as guaranteed Discord inline preview.
- Test storage retention across a real Discloud restart/redeploy, outbound
  egress quota, concurrent streams, disk quota, and file/manifest expiry.
- Staff UI creation/disable is accessible on Android and does not interfere
  with regular Share Router proxy routes.
- Live Discloud CPU/RAM/egress and storage quotas are measured, not guessed.
