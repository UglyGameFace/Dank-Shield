# Dank Shield Support and Incident Response

_Last updated: 2026-04-26_

> This document defines how Dank Shield should handle public/beta support, outages, abuse reports, privacy requests, and security incidents. It is an operational runbook, not legal advice.

## 1. Purpose

Public Discord bots need a clear support and incident process before outside servers depend on them.

This runbook exists so the bot operator can respond consistently when:

- The bot is offline or degraded
- Tickets, verification, or moderation features fail
- A server owner needs setup help
- A user or server owner requests data deletion/export
- A server abuses the bot
- A token, key, API, database, or command surface is exposed
- Discord rate limits, gateway reconnects, or API changes break behavior

## 2. Support channels

Before public launch, publish at least one official support path.

Required public support links:

- Support server: `REPLACE_WITH_SUPPORT_SERVER_URL`
- Support email: `REPLACE_WITH_SUPPORT_EMAIL`
- Status/updates channel: `REPLACE_WITH_STATUS_CHANNEL_OR_PAGE`
- Privacy Policy: `REPLACE_WITH_PRIVACY_POLICY_URL`
- Terms of Service: `REPLACE_WITH_TERMS_URL`

Do not use personal DMs as the only support channel once the bot is public.

## 3. Support intake categories

All support reports should be classified into one category.

| Category | Examples | Target handling |
|---|---|---|
| Setup help | Missing category, wrong role, bad permissions, `/dank health` blockers | Ask for `/dank health` screenshot/log first |
| Ticket bug | Ticket not created, close/reopen failed, transcript missing | Request guild ID, channel ID, timestamp, action used |
| Verification bug | Verify role not assigned, token expired, VC verify broken | Request guild ID, user ID, role/channel config |
| Moderation bug | Kick/ban/timeout failed, modlog incorrect | Request action, actor, target, reason, timestamp |
| Performance issue | Slow interaction, heartbeat warnings, reconnects | Check logs, Supabase latency, Discord status |
| Billing issue | Plan access, cancellation, payment issue | Handle privately; never discuss billing in public channels |
| Privacy request | Export/delete guild/user data | Verify requester authority before action |
| Abuse report | Server using bot for harassment, scams, spam, illegal activity | Escalate to abuse review |
| Security report | Token leak, auth bypass, public API issue, data exposure | Treat as incident immediately |

## 4. Required information for bug reports

Use this support template.

```txt
Server/Guild ID:
User ID involved, if any:
Command or button used:
Ticket/channel ID, if any:
Approximate time and timezone:
What happened:
What should have happened:
Screenshot or log snippet:
Output of /dank health:
```

Do not ask users to share Discord tokens, Supabase keys, API secrets, passwords, or private payment information.

## 5. Severity levels

### SEV-1 — Critical

Criteria:

- Bot token, Supabase service-role key, API shared secret, or payment secret leaked
- Public unauthenticated API can perform sensitive actions
- Cross-server data exposure
- Bot mass-deletes, mass-bans, or mass-modifies roles unexpectedly
- Data loss affecting many servers
- Bot is offline for most/all servers
- Exploit is being actively abused

Immediate actions:

1. Disable affected public endpoint or feature.
2. Rotate exposed secrets.
3. Stop risky workers if needed.
4. Preserve logs for investigation.
5. Post a short public status update if users are affected.
6. Patch and redeploy.
7. Write a post-incident summary.

### SEV-2 — Major

Criteria:

- Ticket creation fails for many servers
- Verification fails for many servers
- Significant heartbeat blocking or gateway disconnect loop
- Dashboard/API degraded but bot core still partially works
- Incorrect permission handling could cause unsafe actions but no active abuse seen

Actions:

1. Acknowledge issue in support/status channel.
2. Identify affected module.
3. Disable risky feature if necessary.
4. Patch and test in owner server.
5. Redeploy.
6. Confirm with affected server owners.

### SEV-3 — Minor

Criteria:

- Single-server setup issue
- One command failing due to permissions/config
- Cosmetic embed/UI issue
- Non-critical logging issue
- Documentation confusion

Actions:

1. Ask for `/dank health`.
2. Fix config or document workaround.
3. Patch if reproducible.
4. Add docs if support repeats.

### SEV-4 — Question/feature request

Criteria:

- How-to questions
- Feature suggestions
- Pricing questions
- Roadmap questions

Actions:

1. Answer clearly.
2. Track feature request.
3. Do not promise dates unless committed.

## 6. Incident response checklist

Use this checklist for any SEV-1 or SEV-2.

```txt
Incident ID:
Severity:
Started at:
Detected by:
Affected systems:
Affected guilds/users:
Current status:
Owner:
```

### Contain

- [ ] Stop the unsafe endpoint, command, worker, or deployment.
- [ ] Disable legacy/unsecured API if involved.
- [ ] Revoke or rotate leaked secrets.
- [ ] Block abusive guild/user if involved.
- [ ] Preserve relevant logs.

### Diagnose

- [ ] Identify first bad commit/deploy.
- [ ] Identify exact affected code path.
- [ ] Identify whether data crossed guild boundaries.
- [ ] Identify whether Discord permissions/rate limits contributed.
- [ ] Identify whether Supabase schema or latency contributed.

### Fix

- [ ] Patch code.
- [ ] Add or update guardrails.
- [ ] Add logs/metrics for recurrence.
- [ ] Test in owner server.
- [ ] Test in at least one clean test server.
- [ ] Redeploy.

### Communicate

- [ ] Post initial status update if user-facing.
- [ ] Update support channel when mitigation is live.
- [ ] Notify directly affected server owners if sensitive data or destructive action occurred.
- [ ] Publish post-incident note for serious incidents.

### Prevent recurrence

- [ ] Add regression test or health check.
- [ ] Add checklist item if missing.
- [ ] Add monitoring alert if possible.
- [ ] Document the fix.

## 7. Public communication templates

### Initial incident notice

```txt
We are investigating an issue affecting Dank Shield: <short issue>.
Impact: <who/what is affected>.
Current status: investigating.
Next update: when we have a confirmed fix or workaround.
```

### Mitigation notice

```txt
A mitigation is now live for <issue>.
Please retry <affected action>.
If it still fails, send your guild ID, timestamp, and /dank health output in the support channel.
```

### Resolved notice

```txt
Resolved: <issue>.
Cause: <plain-language cause>.
Fix: <short fix>.
Impact: <affected users/servers, if known>.
Follow-up: <what is being added to prevent it>.
```

### Security/privacy notice

```txt
We identified a security/privacy issue affecting <scope>.
We have contained the issue by <action>.
Affected server owners will be contacted directly if their data or configuration was involved.
We will share more detail after the risk of abuse is reduced.
```

## 8. Data deletion/export requests

### Who can request guild-wide data deletion

Only the Discord server owner or a verified administrator with authority should be allowed to request guild-wide deletion/export.

Required verification:

- Discord guild ID
- Requester's Discord user ID
- Proof they own/admin the server
- Confirmation of requested action

### Who can request user-specific data

A Discord user may request data related to their own user ID. Some records may also be controlled by the server where the interaction happened, such as moderation logs or ticket transcripts.

Handle case-by-case.

### Deletion request checklist

- [ ] Verify requester identity/authority.
- [ ] Identify scope: user data, guild config, tickets, transcripts, modlogs, verification records.
- [ ] Confirm whether deletion is allowed or whether safety/legal retention applies.
- [ ] Export data first if requested and appropriate.
