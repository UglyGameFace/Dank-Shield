# Share Router video delivery: owner-selected and per-server

The video request feature has **three explicit delivery choices** and a pause
switch. It does not send videos to all members: it processes a single public link
posted by a member in a staff-configured video request channel. DM delivery is
not mandatory.

## Simple setup on Discord Android

1. Open **Share Router → Where Videos Go**. The screen shows the current request
   channel and saved delivery mode. Existing servers with a configured download
   channel but no delivery setting keep their original **Private DM** behavior.
   **Fast option:** If you want the channel you're currently using to handle
   both incoming video links and finished videos, choose **Requests + Results
   Here**. Review the confirmation, then choose **Save Requests + Results Here**.
   It saves one channel as both request and result, disables DMs, and does
   not touch forwarding rules. It refuses an active forwarding source rather
   than silently replacing that rule. No changes occur before confirmation.
2. Choose **Private DM**, **Post in Server Channel**, **Both DM and Channel**, or
   **Pause Downloads**. For channel delivery, choose **Use This Channel** or the
   existing Search-Safe **Choose Channel** picker. Styled names and emojis use
   the same shared picker as other Dank Shield features.
3. **Review and explicitly confirm** the choice. If no request channel exists,
   confirmation creates one ordinary `#video-downloads` channel, provided the
   owner and bot have Manage Channels. Alternatively, choose an existing request
   channel first. Selecting an existing request channel **pauses** delivery
   until the owner separately selects and saves the desired destination mode.
4. A member posts one supported public video URL in the request channel.
   Staff-defined destinations receive the actual attachment when the bot can
   upload it, or an expiring hosted Watch/Download link when hosting works.
   A source-only link or error is not counted as successful downloaded delivery.
5. The original request is removed only when **all configured destinations**
   successfully receive an attachment or hosted link and the bot has
   Manage Messages. If just one of the two destinations succeeds, the source
   stays and staff receive a non-mention warning in the request channel.
   If hosting or delivery fails, the source stays.
6. **Pause Downloads** stops handling new requests while preserving the chosen
   request channel, forwarding routes and Discord channel itself. Resume by
   explicitly choosing a delivery mode. **Advanced Tools → Download Settings**
   retains expiry, hosting checks and source diagnostics.

## Safeguards and compatibility

- All settings use the **existing per-guild Share Router JSON route owner**;
  no parallel database, migration, route listener or second downloader is added.
  Legacy per-guild download channel IDs default to DM-only so old working
  configurations are not silently altered. Absent or invalid explicit modes,
  missing destination IDs and paused channels never activate DMs.
- Server-channel delivery supports **any non-NSFW text channel the bot can
  access in this guild**, including a private channel inside the managed Share
  Router category and the **same channel used for video requests**. Bot posts
  do not recurse into member requests. The requesting member must be able to
  view and send there; Dank Shield needs View, Send and Attach Files. The
  confirmation and runtime recheck permissions. A failed destination never
  silently falls back to DMs.
- Request channels can be public or private server text channels. A channel
  already configured as an independent forwarding **source** cannot also be
  chosen as the video request source until that route is changed. The picker
  lists all cached text channels rather than hiding candidates based on their
  category or permissions; choosing a blocked channel explains what to fix.
  For more than 25 channels, use Next/Previous or Search by name, mention or ID.
  A channel absent from Discord's accessible guild cache cannot be listed until
  Dank Shield has permission to see it.
- Request channels are ordinary text channels and **not private inboxes**.
  Staff choose access using normal Discord roles and channel permissions.
  The sender must be able to view and post in the request channel. The bot
  must be able to read its message history for the durable download queue.
- Existing non-destructive ordinary-channel forwarding, private share
  channels, cleanup policies, provider resolver, 19 MiB conservative bot
  attachment limit, upload/hosting byte caps, cooldowns, queue accounting,
  no-mentions responses and failure handling remain in place.
- Hosted media links are bearer links: anyone who obtains the link may use
  it until expiration. Sharing them in a server channel is a conscious owner
  choice, not the same privacy model as a DM. Never use NSFW sources to
  publish into ordinary channels.
- A paused download request is checked both at intake and before queued jobs
  process; interrupted or stale jobs may be abandoned, never rerouted to a
  newly enabled DM by silent fallback.

## Original delivery infrastructure and HTTPS hosting

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
3. In Discord, open **Share Router → Where Videos Go**, choose a delivery mode,
   and confirm the request/destination channels. Existing forwarding routes stay intact.
4. In **Advanced Tools → Download Settings**, choose **1 hour**, **24 hours**, **7 days**
   (default), or **30 days** (maximum), then **Test HTTPS Hosting**. A passing
   test creates a tiny temporary private token, reads it through the configured
   HTTPS origin, and revokes it; the URL never appears in Discord.
5. Post one safe **public** video URL in the created channel. A small file
   arrives as an attachment in the owner-selected DM and/or channel. If hosting
   is online and size/storage budgets permit, larger files are staged and delivered as
   separate **Watch** (inline HTTP video) and **Download** (attachment HTTP)
   links. When hosting is not ready or budgets are exceeded, the bot sends an
   honest original-source link rather than claiming a file is hosted.
6. **Pause Downloads** stops the member workflow without deleting the
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
  it until expiry**. Anyone who receives a DM, sees a channel post, or copies the URL can
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
- Temp media is removed on failed staging, undelivered links, and after expiry.
  The live public-host canary is still required to verify actual ingress,
  storage persistence, bandwidth and watch/download accessibility.

Do **not** repoint `DANK_VERIFY_REMINDER_DATABASE_URL` or change PostgreSQL
for this feature; staged media never belongs in the reminder database.

## Live tests required before enabling

- Two guilds: only the staff-configured channel processes messages.
- A public progressive MP4 under the bot upload limit is delivered only to the
  destinations expressly chosen by server staff: member DM, a permitted server
  channel, or both. Channel-only mode must send **no DM**. Source cleanup requires
  successful delivery to all selected destinations.
- A separate-audio/video source and short HLS/DASH clip remain within time,
  temporary-file and byte budgets, or return honest links.
- A file over cap, failed downloader, failed DM (50007), missing channel or
  permission, API 413, no Attach/Manage Messages, paused feature, repeated posts,
  concurrent requests and partial 'Both' delivery preserve source posts.
- Expiration presets are saved per guild; token does not disclose source URL.
- HTTPS Test Hosting verifies the same external public origin that Discord
  and a user would reach. Check Watch and Download links in each chosen destination
  on Android and desktop Discord. Never interpret a passing
  HTTP test as guaranteed Discord inline preview.
- Test storage retention across a real Discloud restart/redeploy, outbound
  egress quota, concurrent streams, disk quota, and file/manifest expiry.
- Staff can configure an existing request channel, create one after an explicit
  delivery choice, select a destination with Search-Safe or Use This Channel,
  pause and resume on Android. Normal Share Router forwarding remains unchanged.
- Live Discloud CPU/RAM/egress and storage quotas are measured, not guessed.
