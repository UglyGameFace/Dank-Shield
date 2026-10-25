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

## Discloud private PostgreSQL reminder ledger (opt-in)

Only the unverified-member reminder **delivery history** uses this PostgreSQL
instance. Supabase `guild_configs`, staff verification settings, role truth,
Simple Verify and the rest of Dank Shield are unchanged. Do not repoint the
generic `DATABASE_URL` or remove `SUPABASE_URL`.

1. On Discloud, create a PostgreSQL 16 service (e.g. `DankShield-DB`) and
   confirm its **private** Host, Port, Database, User and Password in its overview.
   The bot and database must be in the same supported private-network cluster.
2. Enable **Private Network (VLAN)** for the Dank Shield bot. The deployment
   `discloud.config` uses `VLAN=true` and `HOSTNAME=dankbot`. That hostname
   names the **bot**; it is not the DB hostname. The database's own private
   hostname is used in the connection URL.
3. Save the full **private** PostgreSQL URL in the bot's Discloud environment
   (not GitHub), as `DANK_VERIFY_REMINDER_DATABASE_URL`.
   Keep the password and full URL out of chat, screenshots, PRs and logs.
   Never enable external/public PostgreSQL access just for the bot.
4. Run the one-shot deployment operation **from a runtime that can access the
   same Discloud private VLAN**, with the above environment variable:
   `python -m tools.migrate_verify_reminders_postgres --apply`.
   This reads `postgres/migrations/202610110001_verify_unverified_reminders.sql`;
   it is intentionally **not run at bot startup**. If your Discloud account
   does not provide a private-shell/deployment command runner, arrange the
   approved private-network migration step first. Do not assume GitHub Actions
   can access the private DB or that saving the VLAN setting created the table.
5. Confirm the app can read the ledger. In Discord open
   **Verification Center → Unverified Reminders**. The Enable action is blocked
   unless the private PostgreSQL ledger is reachable and correctly migrated.
   Configure a small test server and verify cooldown/restart behavior before
   enabling bulk reminders across servers.

The PostgreSQL 16 integration workflow applies the standalone schema twice and
tests atomic competing claims. The existing Supabase migration
`20261011000100_verify_unverified_reminders.sql` was committed in PR #510 and
is retained as migration history; the reminder runtime no longer calls it and
does not switch back to Supabase if PostgreSQL becomes unavailable.

**Failure handling:** no private URL, wrong VLAN, database offline, missing
table, privilege errors and unknown transaction outcomes all block reminder
sending. Never interpret missing history as permission to send a fresh ping.

## Optional Community Hub external gaming sessions (The100.io)

The100.io supports read-only upcoming gaming session discovery with a personal
supporter API token. This integration does not post or join games on anyone's
behalf; the provider separately approves Discord bot write operations.

- Apply `supabase/migrations/20261018010000_community_hub_external_the100.sql`
  through the existing schema pipeline **before** attempting to enable it.
- Obtain an authorized API token using
  `https://www.the100.io/api-docs/index.html`. Store it privately on the bot
  host as `DANK_COMMUNITY_THE100_API_TOKEN`; never commit or paste the token.
- A server manager enables **Community Hub → Staff Dashboard → Settings →
  The100.io Discovery**. The setting is OFF for every existing and new server
  until explicitly enabled. Members then open **Community Hub → Find Players →
  External Groups** or **Community Hub → External Groups**.
- Every external result is an outbound source link, not a native Community Hub
  session, Quick Match candidate, or member role notification. External
  availability must be verified at the source before attempting to join.
- Missing API token, migration, authorization, or provider outage must fail
  safely without affecting normal Community Hub / HubLink sessions. The API
  response is bounded to 128 KiB and 25 normalized sessions; host-local
  successful responses are cached for 90 seconds to limit provider traffic.

Do not enable the provider in production before validating a real token and
response format on a staging host. No new Discord slash commands are needed.

## Startup health lines to expect

Healthy public startup should show the normal module-registration line first, then the final UI-first compactor before Discord global sync:

```text
public_server_env_id_guard public mode active; deployment-level Discord IDs are disabled
globals: startup summary: {'guild': 0, ...}
globals: supabase status: state=ready ... service_role_present=True
commands_ext registration complete. ... profile=public
public_command_surface_v2 compact UI installed roots=['View Dank Profile', 'captions', 'dank', 'mod', 'role', 'ticket', 'tickets', 'toke', 'verify'] dank_children=[...curated direct shortcuts, purge, setup, upload] ...
```

The intentional **final** public global application-command surface is exactly **9** commands/items:

1. `/dank` — app-style Dank Shield entry group
2. `/captions` — ordinary-server Live Captions, session status, and personal voice consent
3. `/mod` — one moderation/member center doorway
4. `/role` — smart Roles & Profiles doorway; optional member/role shortcuts reuse existing guarded surfaces
5. `/ticket` — one current-ticket controls doorway
6. `/tickets` — one ticket queues/setup/routing doorway
7. `/toke` — ping the configured opt-in crowd with optional short text or image/GIF media; Community & Pings starter role required
8. `/verify` — one verification status/repair doorway
9. `View Dank Profile` user context menu

`/dank` exposes a curated set of short, discoverable direct children; the underlying Home UI remains the complete feature catalog:

- `/dank home` — the categorized Dank Shield control center. It exposes Setup & Server Settings, Onboarding & Access, Safety & Moderation, Members/Roles/Profiles, Community & Engagement, Tickets & Support, Design & Branding, Voice & Accessibility, Logs/Stats/Diagnostics, My Dank Shield, Utilities & Help, plus Find a Feature and All Features.
- `/dank purge` — the compact destructive-cleanup entrypoint that remains a direct command for explicit targeting and confirmation.
- `/dank setup` — the guided onboarding/setup entrypoint restored for discoverability while advanced setup tools stay inside the UI. Its **Live Captions** section can select/create the ordinary-server caption output, allow all VCs or selected VCs/categories/exclusions, and choose **Original**, **English**, or **Original + English** caption text. Gemini Live input defaults to automatic detection across its supported languages and code-switching.
- `/dank protection` — protected server safety center (AntiNuke, Spam Guard, invite/Automod).
- `/dank voice-safety` — staff configuration of disclosed AI voice monitoring; **not** a member opt-out command or an activation bypass.
- `/dank welcome` — canonical Welcome, Join & Exit manager.
- `/dank design` — canonical server/channel design studio.
- `/dank share` — Share Router and inline-media manager.
- `/dank pings` — community and notification ping-role manager.
- `/dank community` — Community Hub and LFG.
- `/dank logs` — logging and activity setup.
- `/dank stats` — live server-stats center.
- `/dank status` — live bot service status.
- `/dank diagnostics` — read-only health and configuration diagnostics.
- `/dank profile` — personal profile studio.
- `/dank help` — built-in feature guide.

- `/dank upload` — the compact **card-asset** attachment command for a Join Card background, Exit Card background, or custom card font. The only other approved attachment doorway is `/toke upload:` for optional Toke image/GIF media. Discord buttons still cannot provide attachment fields.



### Cinema extraction

Dank Cinema now owns its source in [UglyGameFace/Dank-Cinema](https://github.com/UglyGameFace/Dank-Cinema).
Dank Shield does not register `/movie` or `/cinema` or host any Cinema/torrent route.
Existing Cinema messages are safely retired instead of launching Cinema.
Share Router's `/share-video` listener remains independent.

Former roots such as `/ticket-intake`, `/ticket-category`, and `/ticket-panel` are not public autocomplete commands anymore. Their implementation modules remain loaded and their actions are available inside `/tickets`. Advanced and obsolete `/dank` shortcuts remain hidden. The curated shortcuts above and `/dank home` both open the same canonical features.

Advanced repair/setup aliases such as direct `/dank setup-review`, `/dank db-check`, and `/dank setup-access` are also not part of the normal public profile. Their functionality belongs inside the guided mega-menu/diagnostics surfaces unless an explicit admin/development profile selects an advanced registrar.

Schema health should show either successful read-only readiness or exact migration guidance for missing tables/columns. It must never report that a direct database URL will auto-repair production schema.

## Share Router-only site listener

Dank Shield's public Site listener provides `/`, `/health` and signed `/share-video` routes only.
No movie, torrent, watch, feed or Cinema routes are mounted. Configure
`DANK_SHARE_VIDEO_PUBLIC_BASE_URL` as a secure HTTPS URL, with
`DANK_SHARE_VIDEO_BIND_HOST=0.0.0.0` and `DANK_SHARE_VIDEO_PORT=8080`.
Old `DANK_MEDIA_*` URL/bind variables are temporarily accepted for
Share Router compatibility, but they do not enable Cinema.
Remove old Cinema/torrent credentials and stored media from the live host
using the platform's approved administrative controls after preserving
necessary incident diagnostics. The repository migration alone does not stop
existing production processes or guarantee policy compliance.
