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

The intentional public global application-command surface is exactly **9** commands:

1. `/dank`
2. `/mod`
3. `/ticket`
4. `/tickets`
5. `/ticket-intake`
6. `/ticket-category`
7. `/ticket-panel`
8. `/verify`
9. `View Dank Profile` user context menu

Advanced setup aliases such as direct `/dank setup-review`, `/dank db-check`, and `/dank setup-access` are not part of the normal public profile. Their functionality belongs inside the guided `/dank setup` and diagnostics surfaces unless an explicit admin/development command profile selects the advanced registrar.

Optional schema health should show either:

```text
optional_schema_health optional tables readable
```

or exact migration guidance for missing optional tables.

## Public setup flow

For each Discord server:

1. Invite the bot with the required permissions, including Manage Threads for authoritative activity coverage.
2. Run `/dank setup`.
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
