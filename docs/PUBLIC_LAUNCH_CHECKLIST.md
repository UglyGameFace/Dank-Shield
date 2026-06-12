# Dank Shield Public Launch Checklist

_Last updated: 2026-06-12_

This checklist is the public-release gate for the Dank Shield bot runtime and the Vercel dashboard handoff. Do not invite the bot broadly until every blocker section passes in a fresh server.

## Launch rule

Dank Shield is not launch-ready just because the process boots. A public server owner must be able to install the bot, run setup, post a ticket panel, open a ticket, verify a member, and use the dashboard without knowing any of the internal history.

A server is launch-ready only when these checks pass:

```text
python -m py_compile stoney_verify/*.py stoney_verify/members_new/*.py stoney_verify/tickets_new/*.py stoney_verify/verification_new/*.py stoney_verify/startup_guards/*.py tools/*.py
python tools/audit_public_setup.py
python tools/audit_public_command_friction.py
python tools/audit_public_invite_permissions.py
python tools/audit_public_command_friction.py
python tools/audit_public_launch_readiness.py
```

## Required public command surface

Global slash commands must stay small and obvious:

```text
/dank
/mod
/ticket
/tickets
/ticket-category
/ticket-panel
/verify
```

The public `/dank` group must show only:

```text
cleanup
commands
help
members
setup
spam
```

These must not appear in public autocomplete:

```text
/stoney
/dank scoreboard
/dank setup-status
/dank db-check
/dank production-audit
/repair_verify_ui
/recompute_member_risk
/recompute_all_member_risk
/spam_guard
/grant_vr
```

## One-time command cache flush

Use this only when Discord still shows stale commands after the bot code is clean:

```env
DANK_FORCE_COMMAND_SYNC_ON_BOOT=true
```

Restart once, wait for global command sync to complete, then turn it back off:

```env
DANK_FORCE_COMMAND_SYNC_ON_BOOT=false
```

Never use these for normal public cleanup:

```env
CLEAR_GLOBAL_COMMANDS_ON_BOOT=true
STONEY_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT=true
```

## Public production environment

The deployment must not contain server-specific Discord IDs. Roles, channels, categories, and server IDs must be saved per guild through `/dank setup` and the dashboard.

Required public defaults:

```env
STONEY_DEPLOYMENT_MODE=production
STONEY_PUBLIC_MODE=true
STONEY_PRODUCTION_MODE=true
STONEY_COMMAND_PROFILE=public
STONEY_SYNC_BETA_GUILD_COMMANDS=false
CLEAR_GLOBAL_COMMANDS_ON_BOOT=false
STONEY_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT=false
DANK_SKIP_UNCHANGED_GLOBAL_SYNC=true
DANK_FORCE_COMMAND_SYNC_ON_BOOT=false
STONEY_PUBLIC_CONFIG_ISOLATION=true
STONEY_ALLOW_SERVER_ENV_IDS=false
STONEY_SERVER_ENV_IDS_ENABLED=false
BOT_DISPLAY_NAME=Dank Shield
```

Optional schema bootstrap may warn if no direct Postgres URL exists. That is acceptable as long as Supabase REST is configured and optional tables are readable.

## Minimum invite permissions

Dank Shield should avoid requiring Administrator for public installs.

Required baseline permissions:

```text
Manage Channels
Manage Roles
View Channels
Send Messages
Embed Links
Attach Files
Read Message History
Manage Messages
Moderate Members
```

Role hierarchy is mandatory. The Dank Shield bot role must be above every role it needs to assign or remove.

## Fresh server smoke test

Run this in a brand-new test server with no old Dank Shield channels, no Ticket Tool categories, and no manually configured role IDs in environment variables.

```text
1. Invite Dank Shield with the public invite permissions.
2. Run /dank setup.
3. Choose Fresh Server.
4. Create Missing Defaults Now.
5. Run Health Check.
6. Post the ticket panel with /ticket-panel post.
7. Open a test ticket.
8. Claim, close, reopen, and delete or archive the ticket.
9. Confirm transcript and modlog output.
10. Join with a test member and confirm Unverified assignment.
11. Verify the test member.
12. Confirm no false fail-closed kick or red safety card appears.
```

Pass condition:

```text
public_startup_guard blockers=0 warnings=0
commands_ext registration complete
local global commands: ['dank', 'mod', 'ticket', 'tickets', 'ticket-category', 'ticket-panel', 'verify']
dank_children=['cleanup', 'commands', 'help', 'members', 'setup', 'spam']
```

## Existing server smoke test

Run this in a server that already has categories, roles, staff roles, logs, and old ticket history.

```text
1. Run /dank setup.
2. Choose Existing Server.
3. Set Ticket Basics.
4. Set Verification Roles.
5. Set Verification Channels.
6. Set Logs + Status.
7. Run Health Check.
8. Post a new ticket panel.
9. Open a ticket from each menu option.
10. Confirm numbering does not reset to #0001.
11. Confirm overflow category routing near Discord's 50-channel category limit.
12. Confirm close-before-delete is enforced.
```

## Dashboard handoff gate

The Vercel dashboard must match the bot's per-guild truth model.

Before public launch, verify:

```text
1. Login does not loop.
2. Choose Server works on mobile, tablet, and desktop.
3. Bot installed detection matches the actual bot guild list.
4. Account menu works.
5. Forms page does not block ticket setup unless forms are truly required.
6. Ticket categories and menu options are not confused.
7. Setup checklist count matches visible completed items.
8. Dashboard never relies on deployment-level GUILD_ID.
9. Dashboard reads/writes the same per-guild config the bot uses.
10. Vercel build passes.
```

## Stability burn-in

Run the bot for 24 to 48 hours before a wider public invite push.

Watch for:

```text
no restart loop
no ImportError
no stale command resurfacing
no false verification fail-closed cards
no duplicate ticket panels
no duplicate slash commands
no Supabase broken-pipe loop
heartbeat memory stable
```

A normal restart should show:

```text
process_health BOOT
startup_guard loader complete
public_startup_guard deployment=production profile=public strict=True blockers=0 warnings=0
commands_ext registration complete
Shard ID None has connected to Gateway
```

## Do not copy competitors

Use Ticket Tool and other bots as product benchmarks only. Do not copy branding, embed layouts, copywriting, docs, pricing, designs, source code, or private workflows.
