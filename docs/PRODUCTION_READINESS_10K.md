# Stoney Verify — 10,000 Server Production Readiness Plan

This document is the source of truth for making Stoney Verify safe to run as a public multi-guild Discord bot at serious scale.

## North star

Stoney Verify must behave like a tenant-isolated SaaS product, not a single private server bot.

Every feature must answer these questions before shipping:

1. Does this operate on the correct guild only?
2. Does this avoid global single-server channel, role, or category IDs?
3. Does this survive Supabase/network errors without poisoning the event loop?
4. Does this avoid blocking Discord gateway heartbeats?
5. Does this degrade safely if a server has not completed setup?
6. Does this expose enough health/status information to diagnose production failures?

---

## P0 — mandatory before public scale

### 1. Tenant isolation / per-guild config

Current risk: public mode has multiple guilds, but some code paths still read single global IDs such as transcript, modlog, verification, ticket category, staff role, verified role, and unverified role.

Required rule:

- Global env IDs are allowed only as local/dev defaults.
- Runtime public mode must resolve guild config by `guild_id` first.
- Any channel/role/category ID must be validated as belonging to the active guild before use.
- If a configured ID belongs to another guild, the bot must refuse to use it and log a setup-health warning.

Required files/surfaces:

- `stoney_verify/config_new/` or equivalent shared resolver
- `stoney_verify/tickets_new/transcript_service.py`
- `stoney_verify/modlog.py`
- ticket category resolution
- verification panel/channel resolution
- VC verification channel resolution
- staff/verified/unverified role resolution
- startup status reports

Success criteria:

- A ticket in guild A can never post transcripts/modlogs into guild B.
- A role operation in guild A can never use a role ID from guild B.
- Startup reports clearly show missing setup per guild.

### 2. API health and deployment contract

Current risk: hosted deployment logs show public mode while API bind defaults may still be localhost in some branches/configs.

Required rule:

- Public deployment must bind health API to `0.0.0.0` unless the platform explicitly uses localhost container health checks.
- `/health` must remain unauthenticated and cheap.
- Mutating API routes must require auth.
- API startup must fail closed if auth is required but secret is missing.

Success criteria:

- Hosting platform can reach `/health`.
- No unauthenticated mutation route is exposed.
- Boot logs print bind host, port, auth mode, and public/private profile.

### 3. Startup stability

Current risk: startup has many background tasks and some DB calls can hit stale HTTP protocol errors.

Required rule:

- Startup must never block gateway readiness on optional maintenance.
- Maintenance tasks must run per guild with independent retry/timeout.
- Supabase connection/protocol errors must reset the client and retry.
- A failed guild task must not fail all guild tasks.

Required surfaces:

- stale verification ticket reconciliation
- panel bootstrap
- ticket sync/backfill
- member sync
- departed reconciliation
- metrics sync
- status reporter

Success criteria:

- No task can hang forever.
- No stale Supabase client can poison all guild startup work.
- SIGTERM logs are interpreted as platform lifecycle, not Python crashes.

### 4. Discord gateway scale

Required rule:

- Enable `AutoShardedBot` before serious public growth.
- Avoid guild-wide member cache dependence for large servers.
- Prefer fetch-on-demand and DB-backed state.
- Heavy scans must be rate-limited and resumable.

Success criteria:

- Bot can boot with many guilds without full guild scans blocking readiness.
- Shard logs show shard count and shard IDs.
- Background work is distributed, rate-limited, and safe to skip/retry.

### 5. Database reliability and schema discipline

Required rule:

- Every table that stores guild-specific data must include `guild_id`.
- Every lookup involving tickets/users/roles/channels must include `guild_id` when possible.
- Add unique constraints that prevent cross-guild collisions.
- Add indexes for all hot public-bot paths.

Required indexes/constraints to audit:

- tickets: `(guild_id, channel_id)`, `(guild_id, user_id, status)`, `(guild_id, ticket_number)`
- verification_tokens: `(guild_id, token)`, `(guild_id, user_id, used, expires_at)`
- guild_settings/config: `(guild_id)` unique
- member_joins: `(guild_id, user_id, joined_at)`
- guild_members: `(guild_id, user_id)` unique
- panels: `(guild_id, channel_id, message_id)`
- spam_guard/settings: `(guild_id)` unique

Success criteria:

- No public path relies on globally unique Discord channel IDs alone when guild context is available.
- Backfill/sync jobs are idempotent.
- DB failures are retried only when retryable.

---

## P1 — needed before large public release

### Observability

- Structured log prefix for guild, channel, user, task, shard.
- Health endpoint includes uptime, guild count, shard info, task count, API auth mode, DB health summary.
- Status reporter posts per-guild setup issues without spamming.
- Add diagnostic command for guild setup health.

### Rate limit and queue discipline

- Use queues for expensive Discord operations.
- Avoid burst editing/deleting many channels at once.
- Cap per-guild maintenance concurrency.
- Add jitter to recurring jobs.

### Product setup flow

- `/stoney setup` should create or discover required channels/roles per guild.
- Save discovered config in DB.
- Never require env vars per customer server.
- Env vars should be bot-wide infrastructure only.

### Safe defaults

For incomplete setup:

- ticket creation should fail with a clear setup message instead of guessing channels/roles.
- transcript posting should skip safely if no same-guild transcript channel exists.
- modlog should skip safely if no same-guild modlog exists.

---

## P2 — long-term scale

### Multi-process / horizontal scaling

- Move background jobs to a dedicated worker process.
- Use distributed locks for cross-process startup jobs.
- Use queue jobs for transcripts and ticket cleanup.
- Store idempotency keys for destructive actions.

### Data retention and privacy

- Define transcript retention.
- Avoid storing ID images in DB.
- Redact sensitive identity metadata.
- Add per-guild data deletion/export workflows.

### Abuse and safety

- Add global abuse controls.
- Add per-guild rate limits for ticket creation and verification submissions.
- Add suspicious invite/join burst handling without blocking event loop.

---

## Immediate engineering order

1. Create shared `guild_config` resolver.
2. Migrate transcript/modlog/category/role resolution to that resolver.
3. Harden startup DB maintenance with retry + timeout + per-guild isolation.
4. Fix production API bind/health contract.
5. Add setup-health command/report.
6. Add database index/constraint migration checklist.
7. Enable sharding strategy.
8. Split heavy workers from gateway process.

---

## Definition of done for 10k-server readiness

The bot is not considered ready for 10,000 servers until:

- no single-guild global IDs are used in public runtime paths;
- all startup maintenance is timeout-bound and per-guild isolated;
- all guild setup state is stored per guild;
- the API health contract is explicit and stable;
- sharding is enabled/tested;
- expensive jobs are queued or rate-limited;
- DB hot paths are indexed;
- every destructive action is idempotent and auditable.
