# Dank Shield Public Launch Checklist

_Last updated: 2026-04-26_

> This checklist is for beta/public launch preparation. It uses competitor-style product patterns as benchmarks, not copied text, branding, code, UI, pricing, or private implementation details.

## Public launch rule

Do **not** publicly invite Dank Shield to outside servers until every **Blocker** item is complete.

Use this command in every test server before launch:

```txt
/stoney health
```

A server is ready only when `/dank health` shows no blockers.

---

## 1. Competitor benchmark targets

These are the product standards Dank Shield should be measured against.

| Competitor-style reference | What users expect | Dank Shield launch target |
|---|---|---|
| Ticket Tool-style ticket bots | Fast ticket panels, category routing, staff actions, transcripts, close/reopen/archive flow | Ticket creation must be fast, reliable, per-server configurable, and easy for non-technical admins |
| MEE6/ProBot-style public bots | Simple onboarding, minimal command clutter, clear permissions, public docs | Public profile must stay under Discord command limits and setup must be guided |
| Dyno/Carl-bot-style moderation bots | Modlog reliability, permission checks, audit context, role hierarchy warnings | Moderation commands must refuse unsafe actions and explain missing permissions |
| Premium bot dashboards | Clear pricing tiers, server-level settings, billing transparency | Paid plans must be documented before charging anyone |
| Large verified bots | Privacy Policy, Terms, support server, status communication, abuse handling | Legal docs and support process must exist before public rollout |

Do not copy competitor branding, embeds, copywriting, designs, docs, code, or pricing tables directly. Use the category of feature as the benchmark and build original Dank Shield behavior.

---

## 2. Hard blockers before public beta

### 2.1 Database and per-server config

- [ ] `public.guild_configs` table exists in Supabase.
- [ ] `/dank setup-tickets` successfully saves open ticket category, archive category, staff role, transcript channel, and prefix.
- [ ] `/dank setup-verify` successfully saves verification channels and verification roles.
- [ ] `/dank setup-logs` successfully saves modlog/security log channels.
- [ ] `/dank config` shows `config source` from DB, not env fallback.
- [ ] `/dank health` has no blockers in the primary test server.
- [ ] A second test server can be configured without changing `.env` values.
- [ ] Ticket creation uses the configured category for that server.
- [ ] Ticket close moves to the configured archive category.
- [ ] Ticket reopen moves back to the configured open category.
- [ ] No public workflow depends on one global `GUILD_ID`.

### 2.2 Discord command surface

- [ ] `STONEY_COMMAND_PROFILE=public` is set for public/beta deployments.
- [ ] Global command count stays far below Discord's 100 top-level slash command limit.
- [ ] Public setup commands are grouped under `/stoney`.
- [ ] Ticket commands are grouped under `/ticket`, `/tickets`, `/ticket-intake`, and `/ticket-category`.
- [ ] Legacy/internal admin commands are not exposed to every public server.
- [ ] Dangerous debug commands are hidden, owner-only, or disabled in public mode.

### 2.3 API and secrets

- [ ] `BOT_API_REQUIRE_AUTH=true` in public/beta.
- [ ] `BOT_API_SHARED_SECRET` is a strong random secret with at least 32 characters.
- [ ] `BOT_API_ALLOW_INSECURE=false` in public/beta.
- [ ] Legacy unauthenticated APIs are disabled in public deployment.
- [ ] Discord bot token is not printed in logs.
- [ ] Supabase service-role key is not printed in logs.
- [ ] No secrets are committed to GitHub.
- [ ] Dashboard/server API calls authenticate to the structured bot API.

### 2.4 Event loop and performance safety

- [ ] No synchronous Supabase/PostgREST calls run directly inside Discord gateway event handlers.
- [ ] Voice-state logging cannot block heartbeat.
- [ ] Member join/leave sync is queued or rate-limited.
- [ ] Startup reconciliation jobs cannot block the gateway.
- [ ] Runtime job concurrency is capped.
- [ ] Duplicate startup jobs are deduped.
- [ ] Ticket creation finishes quickly even when DB writes are slow.
- [ ] Modlog/dashboard writes degrade gracefully if DB/API is slow.
- [ ] The bot survives Discord reconnects without creating duplicate workers.

### 2.5 Ticket system minimum standard

- [ ] Public panel can create tickets from Discord buttons.
- [ ] Ticket reason modal works.
- [ ] Category inference does not block ticket creation.
- [ ] Staff can claim/unclaim tickets.
- [ ] Staff can transfer/assign tickets.
- [ ] Staff can rename tickets.
- [ ] Staff can add/remove members from tickets.
- [ ] Staff can lock/unlock tickets.
- [ ] Staff can close tickets.
- [ ] Staff can reopen tickets.
- [ ] Staff can delete tickets only with proper permission.
- [ ] Ticket transcripts can be generated and sent to the configured transcript channel.
- [ ] Closed tickets are archived instead of silently disappearing.
- [ ] Ticket DB state matches Discord channel state after restart.
- [ ] Ticket creation failure messages are clear and ephemeral.

### 2.6 Moderation minimum standard

- [ ] `/mod_kick` checks permission and hierarchy.
- [ ] `/mod_ban` checks permission and hierarchy.
- [ ] `/mod_timeout` checks permission and hierarchy.
- [ ] Bot refuses to moderate owners/admins when unsafe.
- [ ] Bot explains missing permissions to the moderator.
- [ ] Modlog entries include actor, target, reason, timestamp, and action.
- [ ] Duplicate voice/modlog events are suppressed.
- [ ] Audit-log lookups are best-effort and do not block the event loop.

### 2.7 Verification minimum standard

- [ ] Configured verify channel is a text channel.
- [ ] Configured VC verify channel is valid for the intended flow.
- [ ] Unverified role exists.
- [ ] Verified role exists.
- [ ] Bot role is above roles it must assign/remove.
- [ ] Verify flow works from a fresh user account.
- [ ] Verification tokens expire.
- [ ] No-ticket timers resume safely after restart.
- [ ] Verification ticket cleanup does not delete unrelated channels.

---

## 3. Legal and policy blockers

### 3.1 Public docs

- [ ] Privacy Policy is published publicly.
- [ ] Terms of Service is published publicly.
- [ ] Support server or support contact is published publicly.
- [ ] Invite page links to Privacy Policy and Terms of Service.
- [ ] Bot listing description accurately explains what data is logged.
- [ ] Server owners are told tickets/transcripts/modlogs may contain user data.

### 3.2 Discord compliance

- [ ] Bot follows Discord Terms of Service.
- [ ] Bot follows Discord Developer Terms.
- [ ] Bot does not encourage spam, scraping, phishing, token theft, evasion, harassment, or scams.
- [ ] Bot does not collect Discord tokens or passwords.
- [ ] Bot does not impersonate Discord.
- [ ] Bot uses only necessary privileged intents.
- [ ] If the bot crosses Discord verification thresholds, verification requirements are handled before wider rollout.

### 3.3 Paid plans

Do not charge users until these exist:

- [ ] Pricing page or plan table.
- [ ] Clear feature limits per plan.
- [ ] Refund/cancellation policy.
- [ ] Support expectations.
- [ ] Payment provider terms.
- [ ] Tax/business setup reviewed for the owner's location.
- [ ] Premium gating cannot break free/basic safety features unexpectedly.

---

## 4. Competitor-inspired product checklist

### 4.1 Ticket Tool-style expectations

- [ ] Setup should take under 5 minutes for a normal server owner.
- [ ] Ticket panels should be created with one obvious command or dashboard flow.
- [ ] Users should never need to type complicated commands to open a basic ticket.
- [ ] Staff actions should use buttons/select menus where possible.
- [ ] Closing a ticket should have confirmation.
- [ ] Reopening a ticket should be obvious.
- [ ] Transcript location should be obvious.
- [ ] Archived tickets should be easy to find.
- [ ] Common errors should be explained in plain language.

### 4.2 MEE6/ProBot-style expectations

- [ ] Public commands should be simple and grouped.
- [ ] The bot should have a clean invite link.
- [ ] Setup should guide admins instead of assuming technical knowledge.
- [ ] Missing permissions should be reported clearly.
- [ ] Server owners should be able to see what is configured.
- [ ] Public docs should explain each module.
- [ ] Premium features should be clearly marked.

### 4.3 Dyno/Carl-bot-style expectations

- [ ] Moderation logs should be reliable and readable.
- [ ] Role hierarchy problems should be detected before actions fail.
- [ ] Mod commands should have guardrails.
- [ ] Automation should be configurable per server.
- [ ] Audit/logging failures should not crash the bot.
- [ ] Staff should be able to diagnose setup problems without reading raw logs.

---

## 5. Beta rollout plan

### Phase 0 — Private owner server

Goal: prove no obvious crashes.

- [ ] Run all `/dank setup-*` commands.
- [ ] Run `/dank health` until no blockers remain.
- [ ] Create 10 test tickets.
- [ ] Close/reopen/delete test tickets.
- [ ] Generate transcripts.
- [ ] Test verification with a fresh account.
- [ ] Test moderation commands against safe test users/roles.
- [ ] Restart bot 5 times and confirm no duplicate workers or broken state.

### Phase 1 — 3 to 5 trusted servers

Goal: prove per-guild config.

- [ ] Add bot to 3-5 small trusted servers.
- [ ] Configure each server without editing `.env`.
- [ ] Confirm each server uses its own categories/roles/channels.
- [ ] Confirm one server's config never affects another.
- [ ] Collect staff feedback on setup clarity.
- [ ] Track ticket creation latency.
- [ ] Track Discord heartbeat warnings.
- [ ] Fix all blockers before inviting more servers.

### Phase 2 — 10 to 25 servers

Goal: prove reliability under mixed usage.

- [ ] Monitor Supabase rate/latency.
- [ ] Monitor Discord reconnects.
- [ ] Monitor command errors.
- [ ] Monitor ticket creation failures.
- [ ] Monitor modlog backlog.
- [ ] Add support workflow for bug reports.
- [ ] Add status/update announcements.

### Phase 3 — 50 to 100 servers

Goal: prepare for real public listing.

- [ ] Enable auto-sharding if required by scale.
- [ ] Review privileged intents and Discord verification requirements.
- [ ] Move recurring jobs to dedicated queues/workers where needed.
- [ ] Add dashboard onboarding if command-only setup causes support load.
- [ ] Add incident response process.
- [ ] Add backup/export strategy.
- [ ] Publish final Privacy Policy and Terms links.

### Phase 4 — 500 to 1000+ servers

Goal: operate like a real SaaS bot.

- [ ] Run sharded bot workers.
- [ ] Split dashboard/API/bot workers cleanly.
- [ ] Use durable background queues for heavy jobs.
- [ ] Add database indexes for all hot queries.
- [ ] Add rate-limited logging and metrics.
- [ ] Add uptime/status page.
- [ ] Add customer support ticket process.
- [ ] Add billing and entitlement system if charging.
- [ ] Add abuse detection for malicious server owners.
- [ ] Add automated data retention cleanup.

---

## 6. Production environment checklist

Recommended public/beta env:

```env
STONEY_DEPLOYMENT=public
STONEY_COMMAND_PROFILE=public
BOT_API_REQUIRE_AUTH=true
BOT_API_ALLOW_INSECURE=false
BOT_API_SHARED_SECRET=<strong random 32+ char secret>
STONEY_DISABLE_LEGACY_API=true
STONEY_RUNTIME_JOBS_MAX_CONCURRENT=8
DISCORD_AUTO_SHARD=false
```

For larger public scale, review:

```env
DISCORD_AUTO_SHARD=true
```

Only enable auto-sharding after testing startup, command sync, persistent views, and guild config cache behavior.

---

## 7. Go / no-go decision

### Go for private beta only if:

- [ ] Public startup guard shows `blockers=0`.
- [ ] `/dank health` shows no blockers in the owner server.
- [ ] Privacy Policy and Terms have been reviewed and placeholders replaced.
- [ ] `guild_configs` exists and per-server config works.
- [ ] Structured API is secure.
- [ ] Ticket create/close/reopen/transcript works.
- [ ] No heartbeat-blocking stack traces appear during normal use.

### No-go if any of these happen:

- [ ] Bot heartbeat blocks during normal voice/member/ticket events.
- [ ] Ticket creation takes long enough for interactions to fail.
- [ ] Any public API runs without auth.
- [ ] Any server uses another server's config.
- [ ] Bot requires editing `.env` for every new server.
- [ ] Slash command count returns to the 100-command limit.
- [ ] Secrets appear in logs, Discord messages, GitHub, or dashboard responses.

---

## 8. Current known warning

`GUILD_ID` may remain set during owner-server beta testing, but production behavior must rely on per-guild database config.

Before wide public rollout, verify that adding a new server works with no env changes.
