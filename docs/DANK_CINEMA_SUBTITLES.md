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
3. **Optional SubDL:** The operator can configure a properly licensed
   paid API key. The backend searches by the room's authoritative TMDB movie
   ID, or TV **series** ID and exact season+episode, never by untrusted
   browser title input. The backend validates result IDs and recent discovery,
   and only offers raw text subtitle downloads. A result that is a ZIP pack,
   unreadable text, or temporarily unavailable fails independently of video.

## Operator-only configuration (Discloud .env)

```ini
# Optional. Keep both unset to disable the external provider.
DANK_CINEMA_SUBDL_API_KEY=<your-authorized-paid-API-key>
DANK_CINEMA_SUBDL_COMMERCIAL_OK=true
```

Never put keys in GitHub, browser JavaScript, Discord messages or URL query
parameters. The operator must follow SubDL's paid API and copyright terms;
free keys must not be pooled/shared to serve a commercial/public bot.
SubDL's published free API tier has daily limits and is not unlimited.
The optional integration **fails closed** on an unverified paid entitlement,
quota exhaustion, inaccessible provider, unsafe result or unsupported archive.

The provider is optional. Its absence must not affect embedded, local
subtitles, Cinema playback or access control.

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

The fallback provider uses an authenticated server-side request with bounded
timeouts and small rate-saving caches. Provider downloads are not a substitute
for release-accurate subtitle matching. TV season packs that cannot be
resolved to one text file are declined.

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
