# Dank Cinema subtitles

This feature is isolated from the movie/torrent source selector, audio track
selection, host authority, Watch Party synchronization, and fullscreen geometry.

## Sources, in priority order

1. **Embedded text:** Verified source metadata advertises SubRip/SRT, ASS/SSA,
   mov_text, or WebVTT text tracks. The signed `/movie/{room_id}/subtitle`
   endpoint uses a bounded FFmpeg extraction from a signed **localhost-only**
   media URL to create WebVTT. Bitmap subtitle codecs (PGS/VobSub) are not
   advertised as selectable text tracks. No third-party API is needed.
2. **Local file:** Any authorized viewer can select their own SRT or WebVTT
   file smaller than 1 MB. It is converted in that viewer's browser into a
   local `blob:` subtitle track, not uploaded to the bot or shared with the
   room. Available even if the torrent contains no embedded subtitles.
3. **External multi-source search:** SubSource, Wyzie Subs, and licensed
   SubDL use a single authorized search surface. Cinema collects relevant
   results from each available provider and can continue with the others when
   one is offline, rate-limited, or yields no result. All credentials remain
   server-side. An opaque result token is bound to the viewer, guild and
   room's media session, so clients never see provider keys or raw download
   URLs. Requests are cached and bounded.
4. **Source-specific safety:** SubSource resolves its own internal title ID
   before subtitle search, then filters the precise TV episode. Wyzie uses
   TMDB + exact season/episode and accepts only its signed /c/ download URLs.
   SubDL requires a validated paid entitlement for shared public bot use.
   ZIP archives only supply one safe, matching SRT/VTT entry or are rejected.
   Unsupported formats and provider failure never stop video playback.

## Operator-only configuration (Discloud .env)

Create API keys from the official provider dashboards:
- SubSource: https://subsource.net/api-docs (7,200/day; 1,800/hour; 60/minute)
- Wyzie Subs: https://store.wyzie.io/redeem (1,000/day; one free key
  per address and network; no free-account farming)
- SubDL: https://subdl.com/developers (for shared public/commercial bot use,
  use an eligible paid plan and confirm its licensing).

The following format supports multiple **separately authorized** accounts
attached to different guilds. Put it on ONE line in Discloud's .env and
replace placeholders with real keys and actual Discord guild IDs:

```ini
DANK_CINEMA_SUBTITLE_ACCOUNTS_JSON=[{"provider":"subsource","name":"community-a","key":"YOUR_SUBSOURCE_KEY","guild_ids":["111111111111111111"]},{"provider":"wyzie","name":"community-a","key":"YOUR_WYZIE_KEY","guild_ids":["111111111111"]},{"provider":"subdl","name":"paid-operator","key":"YOUR_PAID_SUBDL_KEY","scope":"operator","licensed_commercial":true}]
```

The `guild_ids` entries must contain actual guild IDs authorized to use
those accounts. `scope:"operator"` explicitly authorizes the account for
every guild installing the bot; use it only when the credential owner and
provider permit shared use. `name` is a unique internal identifier within
each provider.

You can add multiple accounts from the same provider for **different guilds**.
If two accounts of the same provider accidentally target the same guild,
the deterministic first named account wins. A 429/403/402 **never rotates**
to the next key on the same provider to bypass limits. Cinema falls back to
another independently licensed subtitle provider instead.

Older `DANK_CINEMA_SUBDL_API_KEY` +
`DANK_CINEMA_SUBDL_COMMERCIAL_OK=true` configuration remains compatible,
and is treated as an explicitly operator-wide licensed paid key.

Provider calls are charged to the account pinned to the guild; free tiers
remain restricted by the provider's real limits. In-process protective budgets
are deliberately below advertised ceilings; actual provider 429 and
Retry-After responses take precedence. Restarting Discloud does not reset
provider quotas, so never use restarts to increase capacity.

**No external keys:** Embedded text and browser-local SRT/VTT still work.
Do not commit the JSON with real keys to GitHub, log it, send it in Discord,
or expose it to any browser endpoint.

## Auth and synchronization

All subtitle GET APIs enforce the same signed room/UID/expiry and live guild
membership checks as the Theater page. Private Sessions require a host or
invitation, standalone is host-only, and Watch Party viewers must be eligible
for the room. The player's subtitle choices are per viewer, with a
per-user/per-guild language preference. Selecting subtitles never changes
the host's shared room clock, media session, seek, or audio output.

## Safety and throughput

Embedded extraction is rate-limited to two concurrent FFmpeg jobs per
process, limited to 35 seconds and ~1MB SRT/WebVTT output. At most eight
extracted text files are retained in short-lived memory cache; concurrent
requests for a cached track do not each run a new encoder. A failed subtitle
is non-fatal for movie playback.

The router uses authenticated server-side requests with bounded timeouts,
rate-saving caches, host allowlists for download links and per-key budgets.
External provider downloads are not a substitute for release-accurate
matching. Ambiguous TV season packs are declined. Credentials are never
sent to viewers, and every download requires a fresh signed and scoped
viewer choice. Provider fallback never changes the active movie stream.

## Acceptance checklist

- SRT/VTT local file: choose, turn on/off, then select another language.
- Embedded SRT/ASS: select subtitles in portrait, fullscreen, and landscape.
- Embedded PGS-only release: menu still allows local file; no dead track choice.
- Three modes: standalone, invited private viewer, and 20-person Watch Party
  with independently selected subtitle languages.
- Host pause/resume/seek: text cues follow the **local video timeline**, while
  room synchronization and individual audio remain unchanged.
- No API key: embedded and local choices work; external provider is disabled.
- Auth: forged user/room, expired link, uninvited viewer and missing membership
  cannot retrieve subtitles.
- Optional API: valid licensed key, rate-limit/404/ZIP/error scenarios leave
  the player working and show appropriate messages.
- Run all exact-head CI checks and test at least Android + desktop browsers
  before merging to the Discloud production branch.

## Known limitations before live acceptance

The implementation and unit tests alone do not prove a provider key is
configured, that a torrent has complete subtitle bytes, or that 20 concurrent
devices render the same cues. Browser-local files need re-selection after a
page reload. Provider availability, license, rate limits and subtitle-file
accuracy are external conditions. Real-device playback verification remains
required.
