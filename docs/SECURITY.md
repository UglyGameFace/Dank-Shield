# Dank Shield Security Policy

_Last updated: 2026-04-27_

> This document defines how Dank Shield handles security reporting, secret management, API authentication, Discord permissions, database access, and incident response. It is an operational security policy, not legal advice.

## 1. Security contact

Before public/beta launch, publish a private security contact.

Required placeholders to replace:

- Security email: `REPLACE_WITH_SECURITY_EMAIL`
- Support server: `REPLACE_WITH_SUPPORT_SERVER_URL`
- Status page/channel: `REPLACE_WITH_STATUS_CHANNEL_OR_PAGE`

Do not ask security reporters to post vulnerabilities publicly.

## 2. Supported versions

Public security support applies only to actively deployed public/beta versions of Dank Shield.

| Version / branch | Supported |
|---|---:|
| Production/public deployment | Yes |
| Current beta deployment | Yes |
| Old test branches | No |
| Local developer forks | No |

## 3. Reporting a vulnerability

Report security issues privately through the official security contact.

Include:

```txt
Issue summary:
Affected feature/API/command:
Steps to reproduce:
Affected guild ID, if applicable:
Approximate time:
Impact:
Screenshots/log snippets, if safe:
```

Do **not** include:

- Discord bot tokens
- Supabase service-role keys
- `BOT_API_SHARED_SECRET`
- User passwords
- Payment information
- Private Discord tokens
- Sensitive personal information not needed to reproduce the issue

## 4. Responsible disclosure rules

Security researchers and users must:

- Test only servers they own or have explicit permission to administer.
- Avoid accessing, copying, deleting, or modifying other users' data.
- Avoid disrupting the bot, Discord servers, or infrastructure.
- Avoid spam, phishing, malware, token theft, social engineering, or harassment.
- Give reasonable time to investigate and fix before public disclosure.

The bot operator may block abusive testing or report abuse to Discord or relevant providers.

## 5. High-risk issues

Treat these as urgent:

- Discord bot token exposure
- Supabase service-role key exposure
- `BOT_API_SHARED_SECRET` exposure
- API auth bypass
- Unauthenticated API endpoint can create/close/delete tickets or modify users
- Cross-guild data exposure
- One guild can use another guild's config
- Command permission bypass
- Role hierarchy bypass
- Mass action bug affecting channels, roles, bans, kicks, or timeouts
- Stored XSS or unsafe HTML in dashboard/transcripts
- Data deletion/export given to an unauthorized requester
- Secrets committed to GitHub
- Secrets printed in logs or Discord messages

## 6. Secret handling rules

### 6.1 Never commit secrets

Never commit these to GitHub:

```txt
DISCORD_TOKEN
SUPABASE_SERVICE_ROLE_KEY
BOT_API_SHARED_SECRET
DISCORD_PUBLIC_KEY private values, if any
Payment provider secrets
OAuth client secrets
Webhook secrets
Database passwords
```

Use environment variables only.

### 6.2 Never expose service-role keys to browsers

`SUPABASE_SERVICE_ROLE_KEY` must only be used server-side.

It must never be sent to:

- Browser JavaScript
- Discord messages
- Logs
- Public API responses
- Client-side dashboard bundles
- GitHub issues or screenshots

### 6.3 Bot API shared secret

Public/beta deployments must use:

```env
BOT_API_REQUIRE_AUTH=true
BOT_API_ALLOW_INSECURE=false
BOT_API_SHARED_SECRET=<strong random 32+ character value>
```

The shared secret must be rotated if:

- It appears in logs
- It is pasted into Discord
- It is committed to GitHub
- It is shared with an untrusted person
- A dashboard/API compromise is suspected

### 6.4 Token rotation checklist

If a secret leaks:

1. Revoke or rotate the secret immediately.
2. Redeploy every service using the old secret.
3. Search logs, GitHub commits, Discord messages, screenshots, and deployment env history.
4. Invalidate old sessions/webhooks where applicable.
5. Review access logs for abuse.
6. Document the incident.
7. Notify affected users/server owners if data or actions were exposed.

## 7. API security rules

### 7.1 Structured Bot API

The structured bot API must require authentication for every sensitive route.

Allowed unauthenticated routes:

- Public health route only, if it reveals no secrets and no sensitive guild/user data

All other routes must require auth, especially routes that:

- Create tickets
- Close/reopen/delete tickets
- Assign or move tickets
- Sync guild/member/role data
- Trigger moderation or verification actions
- Read private ticket/moderation data
- Update guild configuration

### 7.2 Legacy API

Legacy unauthenticated APIs must be disabled in public deployment.

Required public env:

```env
STONEY_DISABLE_LEGACY_API=true
```

If a legacy API has to remain enabled temporarily, it must not expose sensitive actions publicly.

### 7.3 API logging

API logs may include:

- Timestamp
- Route name
- Guild ID
- Status code
- Latency
- Request ID

API logs must not include:

- Auth headers
- Shared secrets
- Service-role keys
- Discord tokens
- Full private ticket content unless explicitly needed for a protected audit log

## 8. Discord permission security

### 8.1 Minimum permission principle

Grant only permissions needed for enabled features.

Common required permissions:

- Manage Channels
- Manage Roles
- Send Messages
- Read Message History
- Attach Files
- Moderate Members
- Kick Members, if kick features are enabled
- Ban Members, if ban features are enabled
- View Audit Log, for audit-context logging

### 8.2 Role hierarchy

The bot must refuse unsafe actions when:

- Bot role is below the target role
- Bot role equals the target role
- Target user is owner/admin and action is unsafe
- Actor lacks required Discord permission
- Actor's highest role is not high enough for the action

### 8.3 Public command permissions

Public commands must check permissions server-side, not only in Discord command visibility.

Dangerous commands must require appropriate permissions, such as:

- Administrator
- Manage Server
- Manage Channels
- Manage Roles
- Kick Members
- Ban Members
- Moderate Members

## 9. Database security

### 9.1 Row-level security

Tables that can be accessed from client-facing services should use RLS.

The bot backend may use the Supabase service role, but browser/client apps must not.

### 9.2 Per-guild isolation

Every guild-scoped query must filter by `guild_id`.

High-risk tables include:

- `guild_configs`
- `tickets`
- `ticket_notes`
- `ticket_messages`
- `activity_feed_events`
- `member_joins`
- Verification/session tables
- Modlog/audit tables
- Spam/raid/security tables

### 9.3 Schema changes

Schema changes must be applied through reviewed SQL migrations.

After schema changes:

- Restart services if Supabase/PostgREST schema cache is stale.
- Watch for `PGRST204` and `PGRST205` errors.
- Verify `/stoney health` still works.
- Verify ticket create/close/reopen still works.

## 10. Event-loop safety as security

Discord heartbeat blocking can cause downtime and missed moderation events.

These must not run directly in gateway event handlers:

- Synchronous Supabase requests
- Long HTTP requests
- Full guild/member scans
- Ticket transcript generation
- Large channel scans
- Expensive identity/risk lookups
- Blocking file I/O

Use queues, background jobs, timeouts, and graceful degradation.

## 11. Logging and screenshots

Before sharing logs publicly, redact:

- Bot token
- Supabase URL if paired with keys
- Supabase service-role key
- API shared secret
- User private messages
- Payment information
- Email addresses, when not needed
- Full ticket content, unless the user consented and it is necessary

Safe IDs may still be sensitive in context. Share only what is needed.

## 12. GitHub security checklist

Before public repo or public launch:

- [ ] `.env` is gitignored.
- [ ] No secrets exist in commit history.
- [ ] Public docs do not contain real secrets.
- [ ] Example env files use placeholders only.
- [ ] Security contact is published.
- [ ] Privacy Policy and Terms are published.
- [ ] Dependencies are reviewed.
- [ ] Dangerous debug scripts are not exposed.
- [ ] API auth cannot be disabled accidentally in public deployment.

## 13. Deployment security checklist

Required public/beta env:

```env
STONEY_DEPLOYMENT=public
STONEY_COMMAND_PROFILE=public
BOT_API_REQUIRE_AUTH=true
BOT_API_ALLOW_INSECURE=false
BOT_API_SHARED_SECRET=<strong random 32+ character value>
STONEY_DISABLE_LEGACY_API=true
```

Recommended runtime limits:

```env
STONEY_RUNTIME_JOBS_MAX_CONCURRENT=8
```

For large scale, review separately before enabling:

```env
DISCORD_AUTO_SHARD=true
```

## 14. Incident response reference

For security incidents, follow:

```txt
docs/SUPPORT_AND_INCIDENT_RESPONSE.md
```

SEV-1 examples:

- Token/key leak
- Cross-guild data leak
- Auth bypass
- Mass destructive bot action
- Active exploit

## 15. Current beta security requirements

Do not invite untrusted public servers until:

- [ ] `public_startup_guard` shows `blockers=0`.
- [ ] Structured Bot API logs `SECURE`.
- [ ] Public command profile is active.
- [ ] Slash command budget is comfortably below 100.
- [ ] `/stoney health` has no blockers in the owner server.
- [ ] `guild_configs` migration exists in Supabase.
- [ ] `/stoney setup-*` stores per-guild DB config.
- [ ] Privacy Policy placeholders are replaced.
- [ ] Terms placeholders are replaced.
- [ ] Security/support contact placeholders are replaced.
- [ ] No heartbeat-blocking traces appear during normal voice/member/ticket events.
