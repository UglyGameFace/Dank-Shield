# Dank Shield public production environment

This bot is public multi-server software. Deployment-level Discord server IDs must stay blank. Per-server channels, roles, categories, and panels are saved through `/dank setup` into Supabase `guild_configs`.

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

## Supabase DB URL

The bot can run with only Supabase REST values:

```env
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
```

`SUPABASE_DB_URL` / `DATABASE_URL` is only needed for automatic schema bootstrap. Many hosts cannot reach Supabase direct IPv6 Postgres, so leave it blank unless you are using the Supabase pooler or a reachable Postgres connection.

```env
SUPABASE_DB_URL=
DATABASE_URL=
```

Use the SQL files under `supabase/migrations/` to create or repair tables when direct DB bootstrap is not available.

## Startup health lines to expect

Healthy public startup should show:

```text
public_server_env_id_guard public mode active; deployment-level Discord IDs are disabled
globals: startup summary: {'guild': 0, ...}
globals: supabase status: state=ready ... service_role_present=True
commands_ext registration complete. final_global=9 final_guild=0 profile=public
```

Optional schema health should show either:

```text
optional_schema_health optional tables readable
```

or exact migration guidance for missing optional tables.

## Intentional public command surface

A healthy public build currently registers **9 global application-command surfaces**:

- `/dank`
- `/mod`
- `/ticket`
- `/tickets`
- `/ticket-intake`
- `/ticket-category`
- `/ticket-panel`
- `/verify`
- `View Dank Profile` (user context menu)

The count includes the context-menu command. A change from this list should be reviewed deliberately rather than treated as harmless command drift.

## Public setup flow

For each Discord server:

1. Invite the bot with the required permissions.
2. Run `/dank setup`.
3. Press **Start Setup**, choose what the server should use, then follow **Set Up This Step** until the automatic setup check is ready.
4. Use **Test & Launch** after required setup passes.
5. Spam Guard runtime protection defaults to **On** for new or missing settings rows. An owner may still explicitly turn it Off.
6. Inactivity cleanup remains review-only whenever Dank Shield cannot read required channel history; fix the exact permissions reported by the activity coverage warning.

Never fix a public server by putting that server's IDs into Discloud env. That creates cross-server leakage risk.
