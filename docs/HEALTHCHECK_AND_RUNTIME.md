# Stoney Verify Healthcheck and Runtime Contract

This document defines how the bot should behave in hosted/public production.

## Health endpoint

The structured bot API must expose:

```text
GET /health
```

Rules:

- `/health` must not require auth.
- `/health` must be cheap and never perform heavy Discord or database scans.
- `/health` should return quickly even if Supabase is degraded.
- Mutating API routes must still require auth.

Recommended response fields:

```json
{
  "ok": true,
  "status": "ready",
  "bot_user": "stoney-verify-helper#8082",
  "guild_count": 3,
  "shard_count": 1,
  "uptime_seconds": 123,
  "api_auth_required": true,
  "bind_host": "0.0.0.0",
  "port": 8081,
  "supabase_configured": true
}
```

## API bind host

Public hosted containers normally need:

```text
BOT_API_BIND_HOST=0.0.0.0
BOT_API_PORT=8081
BOT_API_REQUIRE_AUTH=true
BOT_API_SHARED_SECRET=<strong random secret>
```

`127.0.0.1` is allowed only when the platform health check runs inside the same network namespace or when running local development.

## SIGTERM interpretation

If logs show:

```text
SIGNAL_RECEIVED signal=SIGTERM exiting_cleanly=true
PROCESS_EXIT ...
```

that is not a Python traceback crash. It means the host/orchestrator told the process to stop.

Common causes:

- platform health check failed;
- deployment rolling restart;
- memory/CPU quota recycle;
- manual restart/redeploy;
- app did not bind expected port.

## Gateway process responsibilities

The Discord gateway process should prioritize:

1. maintaining gateway heartbeat;
2. handling interactions/events quickly;
3. scheduling optional work in bounded background tasks;
4. failing optional maintenance safely.

The gateway process should not:

- block startup on every guild sync;
- scan every member of every guild synchronously;
- run unbounded transcript generation on the event loop;
- use a single poisoned Supabase client across all guild startup tasks.

## Background tasks

All startup/background tasks must have:

- timeout;
- retry only on retryable errors;
- per-guild isolation;
- logs with guild/task context;
- no uncaught exception that can kill the gateway process.

Recommended log fields:

```text
task=<name> guild=<guild_id> attempt=<n>/<max> status=<ok|failed|skipped> elapsed_ms=<ms>
```

## Public readiness checks

Before expanding beyond private/beta servers:

- `/health` reachable from host platform;
- mutating API routes reject unauthenticated requests;
- `BOT_API_BIND_HOST=0.0.0.0` in hosted public deployment;
- startup logs show shard/guild/task health;
- every guild setup issue is reported per guild instead of silently falling back to another guild's channels/roles.
