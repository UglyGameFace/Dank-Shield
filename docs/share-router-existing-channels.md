# Share Router: existing channels and proxy sources

Share Router now has two deliberately separate, per-server source modes.
Both use the **existing Search-Safe channel picker**, including styled names,
old aliases, channel mentions, and IDs. All routing rules remain in the
existing per-guild Share Router route file; this adds no database or bot
slash command.

## Use Existing Channel (Keep Original)

Staff with Manage Server, Manage Channels, or Administrator can open
**/dank → Community Tools → Share Router → Use Existing Channel**, then
choose an **ordinary existing text channel** and a destination. Choose
**Search Destination** or **Browse Channels**. A successful selection
saves a `delete_source: false` route.

- Every qualifying, human-authored post in the source can be forwarded to
  the destination. This is **not** a selective per-message share button.
  The original message and all its attachments remain in the source.
  No channel is created, renamed, hidden, or deleted.
- The human sender must have their ordinary Discord View Channel and
  Send Messages permission in the destination. Dank Shield needs
  View Channel and Read Message History in the source, and View Channel
  and Send Messages in the destination. **Manage Messages is not required**
  for this keep-original option.
- An age-restricted source can only forward to another age-restricted
  destination. Both selection and every incoming message recheck this rule.
  A normal public channel does **not** need proxy-only hidden and
  non-age-restricted restrictions.
- The destination must not be a Share Router managed proxy source, and the
  source/destination must be different channels in the same guild.
  Saving is revalidated under the existing per-guild lock.
- Existing deduplication means the same content forwarded repeatedly in a
  short interval may appear only once at the destination, but its **original
  posts are never deleted**. A failed target send leaves its source intact.
- The usual member-facing route failure notice and staff modlog are still
  used. Route health distinguishes public existing channels from temporary
  proxy sources. Remove Route disables routing, not the channels.

## Per-route content filters

Before selecting the destination, the Share Router setup screen now shows a
**Content filter** selector. Select it, then **Search Destination** or
**Browse Channels**. The rule is saved for the **source→destination route**.
The saved routes list shows which filter is active.

| Filter | Forwarded messages |
| --- | --- |
| **Videos Only** | Video attachments, playable video embeds, direct video URLs, and recognizable video-oriented links (such as YouTube/Shorts, TikTok, Instagram Reels, and Facebook watch) |
| **Images Only** | Photo or GIF attachments, direct image links, and standalone image embeds; unrelated link-preview thumbnails are ignored |
| **Links Only** | Human-authored web links or embedded post links, including video links, but not attachment-only posts |
| **Everything** | All previously qualifying message types, including ordinary chat |

**Default for NEW existing-channel routes: Videos Only.** This prevents
ordinary conversations in shared community channels being mirrored by accident.
**Default for NEW proxy routes: Everything.** Existing saved routes without
a `content_filter` continue to forward **Everything** unchanged. Updating an
existing route's filter replaces only that route's settings and preserves the
same source and destination rules.

Filters are evaluated without remote media downloads or new provider requests.
Mixed messages forward if **any** permitted content is present, and the
entire qualifying message is forwarded by the existing Share Router sender.
Video classification is intentionally conservative: generic X/Twitter posts,
Instagram photo posts, Reddit text posts, and arbitrary web pages with a
preview thumbnail **do not automatically count as video**. A real playable
embed or attached video does count. GIFs count as **Images**. Unsupported
or ambiguous media can still be forwarded with Links Only or Everything.

When content fails a filter, **nothing gets sent to the target**, no
torrent/media resolver is invoked, and no source message is deleted, even
in temporary proxy mode. A filtered-out post may remain in a private
proxy channel and require staff cleanup. This is deliberate to avoid
silently deleting user content that wasn't forwarded.

## Create Proxy + Add / Change Route (temporary drop-off)

Staff can continue using **Create / Repair Hub**, **Create Proxy**, then
**Add / Change Route**. The managed `share-...` channel is a hidden,
non-age-restricted mobile share-sheet drop-off. Successfully forwarded
source posts **are deleted from that proxy** as before, including the
existing bounded cleanup mechanism when a delivered cleanup initially fails.
Its safety/permission rules are unchanged.

**Do not choose a managed proxy as an existing source.** The UI and runtime
reject that combination rather than silently convert it to non-destructive
mode. The existing Add / Change Route selector still operates on registered
sources, and automatically selects non-destructive mode for historical
ordinary sources outside the managed Share Router hub.

## Facebook video grabber: known limits and fixes

The bot uses the pinned stable `yt-dlp==2026.10.21` metadata extractor
with pre-existing direct Discord/Facebook CDN fast paths, one guarded
provider-declared alternate CDN for an unsuccessful Facebook transfer, and
safe link fallback. This is an upgrade from the previous `2026.8.19`
pin, **not** a claim that upstream Facebook Reel extraction is repaired.

- Facebook mobile-share **`/share/v/`** and **`/share/r/`**, along with
  `/share/video/`, `/share/reel/`, `fb.watch`, `watch/?v=`, and
  `video.php?v=` now qualify for the **Videos Only** filter. Previously,
  some would be dropped before the extractor could examine them.
- `/share/p/` is an ordinary photo/post link and is *not* classified as
  video without a playable video embed. Private group posts also cannot be
  assumed public videos based on their URL alone.
- Facebook can serve login-restricted, age-restricted, group-only or
  changing video metadata. Even currently published yt-dlp versions
  report "Cannot parse data" for some public Reels. We keep the link
  rather than promise native media or use account cookies/CAPTCHA bypass.
- Staff can test one problematic public link privately through
  **Share Router → Media Health → Test Provider**. The Outcome field
  distinguishes parser incompatibility, no downloadable format, bot check,
  login restriction, rate limiting, and HTTP refusal. This metadata-only
  probe does not upload content or display the URL/token to server members.
- Inline transfer can also fail separately due to a too-large file, an
  expired signed CDN URL, incompatible HLS/DASH formats, or missing Discord
  Attach Files permissions. A metadata probe alone does not guarantee
  playback. Diagnostic counters are limited to aggregate non-sensitive
  statuses.

## Limitations and validation

This option makes normal text-channel forwarding possible; it does not turn
Share Router into a per-message consent mechanism, cross-guild bridge, or
unlimited video download service. Existing provider limits and Discord
upload constraints still apply. The implementation adds focused tests for
mode selection, guild isolation, target permissions, age boundaries,
successful/failed sends, deduplication, and original-message preservation.
