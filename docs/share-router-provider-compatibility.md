# Share Router #390: provider compatibility and inline-playback acceptance

This is a **public-bot deployment canary**, not a claim that every external site
currently allows extraction or Discord-native playback.

## Ownership / what is already shipped

- `share_router_media_resolver.py` owns provider recognition, normalization,
  metadata-only yt-dlp extraction, bounded selection and safe fallback reasons.
- `share_router_media_network.py` owns public-only network boundaries.
- `share_router_manifest_proxy.py` owns bounded HLS/DASH remote fetch.
- `share_router_media_remux.py` owns optional FFmpeg **stream-copy** remux,
  without expensive general re-encoding.
- `share_router_runtime.py` alone owns Discord source permissions, native
  upload, fallback link, cleanup, live destination boost-tier cap and dedupe.

There is **no guaranteed inline player** for arbitrary provider links in
Discord. A native uploaded attachment, supported Discord embed, or intentional
link-only fallback is the supported outcome. Playback also depends on the
recipient's browser/client codecs. A successful upload does not prove all
clients can decode it.

## Operator test, on the actual Discloud deployment

Open `/dank home` → Community & Engagement / Share Router manager.
Staff may use:

- **Media Health**: process-local aggregate provider classifications, failures
  and remux counters. These reset on restart and are not per-guild history.
- **Test Provider**: paste a **public, non-authenticated** post/video URL. The
  result displays provider, classification, declared candidate byte size and
  stable reason without echoing the URL or any signed query token. It fetches
  **metadata only** and does not send a file or post a channel message.

Then actually share the same public sample link into a configured private proxy
source. Check **one** routed message in the correct destination, native video
attachment where available, no duplicate embed/preview, mention safety,
source cleaning only after success, and readable link fallback when native
upload is not supported. Check Android + a separate desktop/browser Discord
client. Re-test on destination guilds at base and boosted upload tiers, and
after a boost downgrade. Verify the bot still refuses private/prohibited or
oversized files without downloading them unboundedly.

| Provider / type | Required test | Conditions to distinguish |
| --- | --- | --- |
| X/Twitter | status video and alias URL | one post, normalized status identity |
| TikTok | public video + short link | redirect, no login requirement |
| Instagram/Reels | public reel | login gating vs playable media |
| YouTube/Shorts | public short and normal watch link | combined progressive vs separate audio/video |
| Reddit | public video post | audio+video merge and canonical post identity |
| Twitch | public clip or VOD | manifest remux when short/small; live is link-only |
| Facebook | public video | restricted/login video must fall back to link |
| Vimeo | public video | under-limit H.264/AAC MP4 preferred when available |
| Streamable | public clip | size and progressive format selection |
| Imgur/GIF | public post and direct GIF/MP4 | native attachment vs link |
| Tumblr, Bluesky, Pinterest | public native video post | extraction availability and safe fallback |
| Direct MP4/WebM/MOV/GIF | public URL | server-side content type, codec and size |
| HLS/DASH | short public manifest (non-DRM) | controlled remux, safe segment redirects and byte/time cap |

To keep tests repeatable, record **only** provider key, public source type,
classification, final delivery type, fallback reason, Discord client, and
result. Do not store signed URLs, member content, private identifiers,
passwords, DRM-protected sources or cookies in GitHub issues/logs.

## Classification interpretation

- **Progressive**: direct candidate URL; download/upload still must succeed.
- **Merge**: separate video+audio. Bounded FFmpeg stream-copy may produce MP4;
  unknown or incompatible codecs, missing tracks or over-budget outputs can
  still fail safely.
- **Manifest**: controlled HLS/DASH remux candidate; DRM, live-streams,
  unsupported playlist features and oversized content may require link fallback.
- **Link-only**: unsupported/login-required/unavailable provider or format.
  Never claim a missing player is a successful relay.

## Acceptance not yet proven

Repository unit tests are deterministic local metadata fixtures. A green CI
run does **not** prove live provider availability, authentication policies,
video decode support, source-specific rate limits, or Discloud network speed.
Complete the above canary against a live deployment before closing issue #390.
