# Dank Shield public production environment

This bot is public multi-server software. Deployment-level Discord server IDs must stay blank. Per-server channels, roles, categories, and panels are saved through the guided **Setup & Settings** flow opened from `/dank home` into Supabase `guild_configs`.

## Required runtime values

Set these in Discloud or your host:

```env
DEPLOYMENT_ENV=production
DANK_DEPLOYMENT_MODE=production
DANK_PUBLIC_MODE=true
DANK_PRODUCTION_MODE=true
DANK_COMMAND_PROFILE=public
DANK_ALLOW_SERVER_ENV_IDS=false
DANK_SERVER_ENV_IDS_ENABLED=false
DISCORD_TOKEN=...
DISCORD_PUBLIC_KEY=...
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
BOT_API_SHARED_SECRET=...
BOT_API_REQUIRE_AUTH=true
BOT_API_ALLOW_INSECURE=false
```

## Keep these blank in public production

Do not set deployment-level Discord IDs for a public bot:

```env
GUILD_ID=
TICKET_CATEGORY_ID=
TRANSCRIPTS_CHANNEL_ID=
JOIN_LOG_CHANNEL_ID=
VERIFY_CHANNEL_ID=
VC_VERIFY_CHANNEL_ID=
VC_VERIFY_QUEUE_CHANNEL_ID=
UNVERIFIED_ROLE_ID=
VERIFIED_ROLE_ID=
RESIDENT_ROLE_ID=
STONER_ROLE_ID=
DRUNKEN_ROLE_ID=
STAFF_ROLE_ID=
VC_STAFF_ROLE_ID=
MODLOG_CHANNEL_ID=
RAIDLOG_CHANNEL_ID=
FORCE_VERIFY_LOG_CHANNEL_ID=
DANK_TICKET_OVERFLOW_CATEGORY_IDS=
```

## Supabase schema ownership

The bot runtime uses the Supabase REST API:

```env
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
```

Do not configure `SUPABASE_DB_URL`, `DATABASE_URL`, `POSTGRES_URL`, or `POSTGRES_PRISMA_URL` for bot-startup schema repair. Dank Shield startup is read-only with respect to database structure. Schema creation, repair, and upgrades are owned by committed files under `supabase/migrations/` and the Supabase migration deployment workflow/CLI.

Before deploying a bot version that contains new migrations, verify the migration chain and apply it through the deployment pipeline. If runtime schema health reports a missing table or column, treat that as a deployment/configuration error and apply the referenced committed migration. Do not expect the bot process to modify production schema.

## Startup health lines to expect

Healthy public startup should show the normal module-registration line first, then the final UI-first compactor before Discord global sync:

```text
public_server_env_id_guard public mode active; deployment-level Discord IDs are disabled
globals: startup summary: {'guild': 0, ...}
globals: supabase status: state=ready ... service_role_present=True
commands_ext registration complete. ... profile=public
public_command_surface_v2 compact UI installed roots=['View Dank Profile', 'captions', 'dank', 'mod', 'movie', 'role', 'ticket', 'tickets', 'toke', 'verify'] dank_children=['home', 'purge', 'setup', 'upload'] ...
```

The intentional **final** public global application-command surface is exactly **10** commands/items:

1. `/dank` — app-style Dank Shield entry group
2. `/captions` — ordinary-server Live Captions, session status, and personal voice consent
3. `/mod` — one moderation/member center doorway
4. `/movie` — Movie Night hub; optional authorized magnet or .torrent attachment starts/changes the room media
5. `/role` — smart Roles & Profiles doorway; optional member/role shortcuts reuse existing guarded surfaces
6. `/ticket` — one current-ticket controls doorway
7. `/tickets` — one ticket queues/setup/routing doorway
8. `/toke` — ping the configured opt-in crowd with optional short text or image/GIF media; Community & Pings starter role required
9. `/verify` — one verification status/repair doorway
10. `View Dank Profile` user context menu

`/dank` intentionally exposes only four direct children:

- `/dank home` — the categorized Dank Shield control center. It exposes Setup & Server Settings, Onboarding & Access, Safety & Moderation, Members/Roles/Profiles, Community & Engagement, Tickets & Support, Design & Branding, Voice & Accessibility, Logs/Stats/Diagnostics, My Dank Shield, Utilities & Help, plus Find a Feature and All Features.
- `/dank purge` — the compact destructive-cleanup entrypoint that remains a direct command for explicit targeting and confirmation.
- `/dank setup` — the guided onboarding/setup entrypoint restored for discoverability while advanced setup tools stay inside the UI. Its **Live Captions** section can select/create the ordinary-server caption output, allow all VCs or selected VCs/categories/exclusions, and choose **Original**, **English**, or **Original + English** caption text. Gemini Live input defaults to automatic detection across its supported languages and code-switching.
- `/dank upload` — the compact **card-asset** attachment command for a Join Card background, Exit Card background, or custom card font. The only other approved attachment doorways are `/toke upload:` for optional Toke image/GIF media and `/movie torrent:` for Movie Night torrent metadata. Discord buttons still cannot provide attachment fields.

Former roots such as `/ticket-intake`, `/ticket-category`, and `/ticket-panel` are not public autocomplete commands anymore. Their implementation modules remain loaded and their actions are available inside `/tickets`. Likewise, former `/dank` shortcuts such as status/diagnostics/welcome are reached through `/dank home` rather than separate autocomplete entries.

Advanced repair/setup aliases such as direct `/dank setup-review`, `/dank db-check`, and `/dank setup-access` are also not part of the normal public profile. Their functionality belongs inside the guided mega-menu/diagnostics surfaces unless an explicit admin/development profile selects an advanced registrar.

Schema health should show either successful read-only readiness or exact migration guidance for missing tables/columns. It must never report that a direct database URL will auto-repair production schema.

## Movie Night catalog and provider search

Movie Night separates **movie identity** from **playback media**.

- `DANK_TMDB_READ_TOKEN` is the bot owner's TMDB **API Read Access Token**. Configure it once at deployment level. Guild owners and ordinary members never paste this token.
- `DANK_TMDB_WATCH_REGION` is the two-letter default country used for TMDB/JustWatch availability discovery. Production defaults to `US` when omitted or invalid.
- TMDB supplies exact title/year/poster/overview matching and legal provider availability. It never supplies the movie file.
- JustWatch availability returned through TMDB is displayed with JustWatch attribution.
- Internet Archive Feature Films remains the built-in no-key playable search provider.
- Authorized custom HTTPS JSON APIs remain optional advanced providers.
- Host-supplied magnet links and `.torrent` attachments remain direct playback paths and do not require TMDB or a custom provider.
- Movie Night never stores the TMDB token in guild config or exposes it in Discord UI.

Recommended production values:

```env
DANK_TMDB_READ_TOKEN=<TMDB API Read Access Token>
DANK_TMDB_WATCH_REGION=US
```

## Live Captions native audio runtime

Live Captions receive DAVE-decrypted Opus frames and then decode each speaker to 48 kHz stereo PCM before segmentation/transcription. `discord.py` requires a loadable native libopus for that PCM decoder.

Dank Shield pins `opuslib-next-bundled==0.1.1`, whose platform wheels contain the shared libopus binary. Runtime resolves that bundled file first and passes its full path to `discord.opus.load_opus()`. This avoids depending on incidental host packages. `DANK_OPUS_LIBRARY` is only an optional explicit operator override; normal Discloud production does not require it.

The Discloud `ffmpeg` APT option is not used as an Opus-runtime substitute. Discloud documents that option as installing the `ffmpeg` package, not a standalone libopus contract for Python `ctypes`.

Before a caption receiver is considered available, `discord.opus.is_loaded()` must be true. CI also launches a fresh Python process, loads the bundled library by full path, and constructs a real `discord.opus.Decoder()` so a missing/incompatible wheel fails before deployment.

### Gemini Live transcription and language policy

Live Captions use `GEMINI_API_KEY` with `gemini-3.5-transcribe-live`. Each opted-in Discord speaker owns a separate Gemini Live WebSocket so speaker isolation continues through transcription. Discord's 48 kHz stereo PCM is downmixed, low-pass filtered, and decimated statefully to 16 kHz mono signed-16 PCM. Production accepts each decoded Discord frame as it arrives, then micro-batches only about 100 ms of resampled PCM per Gemini audio message; this is bounded realtime transport batching, not multi-second utterance buffering. Short remainders are flushed before the local manual `activityEnd`. Automatic activity detection stays disabled and `audioStreamEnd` is not used in this manual-VAD path.

Leave `DANK_COMMUNITY_CAPTION_LANGUAGE_CODES` blank for the default **Auto / all supported languages** behavior and code-switching. Members may use **/captions → My Language** and select from the grouped dropdowns for a per-server recognition hint. The picker covers every BCP-47 code in Google's current Gemini 3.5 Transcribe supported-language table. That hint persists across caption restarts and bot redeploys, affects only that member in that server, and also applies to Community Hub captions. Auto remains the default.

Members may also explicitly enable **Auto-Caption My Voice** once per server. That remembered consent is stored in the existing per-guild member settings record. On future caption-session starts or when the member joins the active captioned VC, Dank Shield restores runtime admission automatically. Turning it off persists the revocation and immediately purges active/buffered/in-flight audio. Raw audio is never persisted.

`DANK_COMMUNITY_CAPTION_CUSTOM_VOCABULARY` is optional and should contain only a short comma-separated set of genuinely likely domain terms. It is not a replacement for speaker language selection.

The shared per-server caption output policy applies to both ordinary-server and Community Hub captions:
- **Original language** — publish the finalized transcript as spoken.
- **English** — translate finalized non-English transcript text only.
- **Original + English** — publish both.

English conversion uses `gemini-3.1-flash-lite` on finalized text only. Audio is never submitted a second time for translation. If Live Transcribe already reports an English BCP-47 language code, the translation request is skipped entirely.

Gemini Live sessions are rotated before the documented 10-minute maximum and on `goAway` before the next utterance. Translation failure must not stop original-language captions. Dank Shield publishes finalized `inputTranscription`, not speculative interim transcripts. If an explicit speaker language hint conflicts with Gemini's reported language family, the utterance is shown as `[unclear audio]` rather than an unrelated-language caption.

Discord voice control-plane health is not treated as proof that media is flowing. If an opted-in user's voice gateway speaking signal arrives but no PCM follows while the reader, DAVE session, and SSRC mapping still report ready, Dank Shield classifies the UDP media path as stalled and rebuilds only the receive transport. The existing caption engine stays alive, the exact opted-in users are restored to a fresh in-memory bridge, and recovery is limited to two attempts per rolling minute with a 20-second cooldown.

## Public setup flow

For each Discord server:

1. Invite the bot with the required permissions, including Manage Threads for authoritative activity coverage.
2. Run `/dank home`, open **Setup & Server Settings**, then choose **Setup & Settings**.
3. Choose a setup plan and follow **Set Up This Step** (or **Continue Setup** for Choose Core Features) until Setup Check runs automatically.
4. Fix any required blocker, then use **Test Your Setup**. When the enabled features work, press **Finish Setup**.
5. SpamGuard defaults to ON for new/missing settings rows unless an owner explicitly turns it off.

Never fix a public server by putting that server's IDs into Discloud env. That creates cross-server leakage risk.

## External uptime watchdog

For true bot-down alerts, configure a Healthchecks.io ping URL in the host environment:

```bash
DANK_HEALTHCHECKS_PING_URL=<your private Healthchecks.io ping URL>
DANK_HEALTHCHECKS_TIMEOUT_SECONDS=5
```

Keep the ping URL private. Do not commit it to GitHub. Dank Shield sends an immediate success ping after Discord `on_ready`, then another ping from the process-health loop every `DANK_PROCESS_HEALTH_INTERVAL_SECONDS` (120 seconds by default). A 5-minute Healthchecks.io period with a 10-minute grace window is compatible with the default interval.


## Movie Night media capacity (1.46 GB Discloud target)

The current Dank Shield production host has about **1.46 GB / 1495 MiB RAM**. Normal
bot RSS observed before Movie Night load is roughly **340–390 MB**, so Movie Night
uses dynamic admission instead of treating installed guild count as media load.

The checked-in Discloud profile for this task is:

```ini
TYPE=site
MAIN=main.py
RAM=1495
```

The same process still owns the Discord bot. `TYPE=site` is required only so the
media-only HTTP server can be reached through the Discloud Site proxy. The private
structured/admin API remains on its existing loopback listener.

Use these production environment values:

```env
DANK_MEDIA_PUBLIC_BASE_URL=https://YOUR-DISCLOUD-SITE.discloud.dev
DANK_MEDIA_SERVER_ENABLED=true
DANK_MEDIA_BIND_HOST=0.0.0.0
DANK_MEDIA_PORT=8080
DANK_CINEMA_DISCORD_CLIENT_ID=
DANK_CINEMA_DISCORD_CLIENT_SECRET=<DISCORD APPLICATION CLIENT SECRET>
DANK_CINEMA_DISCORD_REDIRECT_URI=https://YOUR-DISCLOUD-SITE.discloud.dev/cinema/auth/callback
DANK_TORRENT_STREAM_SECRET=<NEW RANDOM SECRET, DO NOT REUSE ANOTHER TOKEN>

# Register this exact same redirect URI in Discord Developer Portal > OAuth2:
# https://YOUR-DISCLOUD-SITE.discloud.dev/cinema/auth/callback
# OAuth redirect matching is exact. Do not substitute .discloud.app for .discloud.dev.

DANK_PROCESS_MEMORY_LIMIT_MB=1495
DANK_MOVIE_NIGHT_MEMORY_RESERVE_MB=350
DANK_TORRENT_ESTIMATED_SESSION_MB=96
DANK_TORRENT_SOFT_SESSION_LIMIT=2
DANK_TORRENT_MAX_SESSIONS=4
DANK_TORRENT_MAX_UNIQUE_PER_GUILD=1
DANK_TORRENT_ALLOW_BURST=false

DANK_TORRENT_MAX_METADATA_BYTES=4194304
DANK_TORRENT_MAX_FILE_BYTES=26843545600
DANK_TORRENT_MAX_TOTAL_BYTES=53687091200
DANK_TORRENT_DISK_RESERVE_BYTES=68719476736

DANK_TORRENT_READAHEAD_BYTES=16777216
DANK_TORRENT_MIN_READAHEAD_BYTES=4194304
DANK_TORRENT_MAX_READAHEAD_BYTES=67108864
DANK_TORRENT_TARGET_BUFFER_SECONDS=30
DANK_TORRENT_MAX_BUFFER_SECONDS=75
DANK_TORRENT_MIN_ESTIMATED_PLAYBACK_BYTES_PER_SECOND=524288
DANK_TORRENT_BOOTSTRAP_BYTES=8388608
DANK_TORRENT_TAIL_PROBE_BYTES=4194304
DANK_TORRENT_BUFFER_WAIT_SECONDS=20
DANK_TORRENT_METADATA_WAIT_SECONDS=30
DANK_TORRENT_IDLE_TTL_SECONDS=1800

DANK_TORRENT_CONNECTION_LIMIT=80
DANK_TORRENT_DOWNLOAD_RATE_BYTES=16777216
DANK_TORRENT_UPLOAD_RATE_BYTES=524288
DANK_TORRENT_LISTEN_INTERFACES=0.0.0.0:6881,[::]:6881
```

### Admission behavior

A new **unique** torrent is admitted only when all of the following remain healthy:

- current process RSS leaves the configured 350 MiB core-bot reserve intact;
- the conservative unique-session soft limit is not full;
- the hard unique-session limit is not full;
- free disk remains above the 64 GiB safety reserve after accounting for
  already-committed selected files and the new selected file.

Identical torrents are deduplicated by canonical torrent identity/info-hash. If
multiple Movie Night rooms choose the same release, they reuse one libtorrent
session and one disk cache while keeping independent room clocks and viewer sync.
Each room owns a tracked lease. Releasing one room does not delete the torrent
while another room still holds a lease.

`DANK_TORRENT_ALLOW_BURST=false` is intentional for this host. It keeps the live
production target at 2 unique torrents while still exposing a hard ceiling of 4.
After real production telemetry proves the extra headroom is safe, burst can be
enabled or the soft limit raised without changing `/movie`.

### Movie Night Setup telemetry

The setup panel reports:

- process RSS / configured memory limit;
- protected memory reserve and remaining headroom;
- active unique torrents;
- total room leases and number of shared torrents;
- currently available admission slots;
- soft/hard unique-session limits;
- free disk, protected disk reserve, and committed media bytes;
- per-movie and per-torrent size ceilings.

The controller fails closed on new unique media when memory or disk safety cannot
be verified. Reusing an already-active identical torrent remains possible even
when the unique-session soft limit is full.


The media-only Site listener stays up for `/health` even if the public URL or
stream-signing secret is temporarily missing. In that state all signed
Movie Night/watch access remains fail-closed, while Discloud can still see a
healthy Site process and `/movie → Setup` can report the missing configuration.
Set `DANK_MEDIA_SERVER_ENABLED=false` only if you intentionally want to disable
the Site listener.


Per-guild fairness defaults to one unique torrent per guild
(`DANK_TORRENT_MAX_UNIQUE_PER_GUILD=1`) so one guild cannot consume every
unique-torrent slot on the current host. Reusing an already-active identical
torrent does not create another global unique torrent. The next-session memory
estimate begins at 96 MiB and is adjusted conservatively from clean,
non-overlapping production RSS deltas; current RSS remains the authoritative
admission guard.
