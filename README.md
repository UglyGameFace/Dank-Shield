# Dank Shield

Dank Shield is a Discord ticket, verification, moderation, and setup assistant built to feel simple for server owners while still giving staff powerful tools.

The normal public setup path is intentionally boring:

```text
/stoney setup
```

You should not need to memorize setup helper commands.

---

## What Stoney does

- Creates and manages support tickets.
- Lets staff close, reopen, delete, archive, and review tickets.
- Supports verification roles and VC/text verification workflows.
- Tracks modlog coverage for important server events.
- Provides spam/invite guard controls.
- Stores setup per server, so one server does not use another server's roles or channels.

---

## Quick start for server owners

### 1. Invite the bot

Invite Stoney with permissions that allow it to manage the workflow:

- Manage Channels
- Manage Roles
- View Channels
- Send Messages
- Embed Links
- Attach Files
- Read Message History
- Manage Messages
- Moderate Members

Keep the Stoney bot role above roles it needs to assign, such as Unverified, Verified, Member/Resident, and ticket staff roles.

### 2. Run setup

In Discord, run:

```text
/stoney setup
```

You will see three main paths:

```text
Fresh Server
Existing Server
Advanced Setup
```

### 3. Pick the right path

#### Fresh Server

Use this when the server does not already have a support/verification layout.

Stoney will create missing recommended items only. It will not delete old channels, old tickets, or existing roles.

Recommended flow:

```text
/stoney setup
Fresh Server
Create Missing Defaults Now
Health Check
/ticket-panel post
Open a test ticket
```

#### Existing Server

Use this when the server already has roles/channels/categories.

Recommended flow:

```text
/stoney setup
Existing Server
Ticket Basics
Verification Roles
Verification Channels
Logs + Status
Back to Setup
Health Check
/ticket-panel post
Open a test ticket
```

#### Advanced Setup

Use this only when you want to fine-tune names, ticket menu options, logs, status, or category routing.

---

## Ticket menu options vs Discord categories

This is the setup concept that confuses people the most.

### Discord categories

These are actual Discord channel folders:

```text
ACTIVE TICKETS
TICKET ARCHIVE
```

Configure these in:

```text
/stoney setup → Existing Server → Ticket Basics
```

### Ticket menu options

These are the choices users see when opening a ticket:

```text
Support
Verification Help
Appeal
Report User
Question
Bug Report
Other
```

Configure these in:

```text
/stoney setup → Advanced Setup → Ticket Menu Options
```

The recommended menu is:

| Option | Purpose |
| --- | --- |
| Support | General help requests and the default fallback. |
| Verification Help | Users stuck verifying or missing verification roles. |
| Appeal | Ban, timeout, mute, blacklist, or denied-access appeals. |
| Report User | Reports about users, scams, spam, harassment, or rule breaking. |
| Question | Simple questions that staff can answer in a ticket. |
| Bug Report | Broken buttons, setup issues, missing panels, or workflow bugs. |
| Other | Catch-all option so users are never stuck. |

The green button in Ticket Menu Options creates only missing recommended menu options. It does not create Discord channels and it does not delete anything.

---

## Daily-use commands

### Owners/admins

```text
/stoney setup
/stoney help
/stoney commands
/stoney spam panel
/stoney cleanup status
```

### Ticket staff

```text
/ticket close
/ticket reopen
/ticket delete
/tickets
/ticket-panel post
/ticket-category
```

### Verification staff

```text
/verify status
/verify diagnose
/verify grant-vr
/verify set-verified
/verify set-resident
/verify fix-member
/verify repair-unverified
```

### Moderation staff

```text
/mod
/stoney spam panel
/stoney spam status
```

---

## First ticket test

After setup passes health:

1. Run `/ticket-panel post` in the channel where users should open tickets.
2. Click the panel as a test user.
3. Confirm the ticket opens in the active ticket category.
4. Close it with `/ticket close`.
5. Reopen it with `/ticket reopen`.
6. Delete or archive it with `/ticket delete` if needed.
7. Check the transcript channel and modlog channel.

---

## Troubleshooting

### I do not see `/stoney setup`

- Restart/redeploy the bot.
- Wait for Discord global command propagation.
- Check the boot logs for command registration errors.
- Run `/stoney commands` if available.

### Setup says a role cannot be managed

Move the Stoney bot role above the role it needs to assign/remove.

### Setup says a channel/category is missing permissions

Give Stoney permission to View Channel, Send Messages, Embed Links, Read Message History, Manage Channels where needed, and Attach Files for transcript channels.

### Ticket menu options are missing

Run:

```text
/stoney setup → Advanced Setup → Ticket Menu Options → Create Recommended Ticket Menu
```

### Health check still fails after setup

Fix the first blocker shown, then run Health Check again. Do not chase warnings before blockers.

---

## Public-server safety rules

- Stoney stores setup per server.
- A server must save its own roles/channels/categories.
- If setup is missing, ticket/staff workflows stay locked instead of guessing.
- The bot should not rely on one server's `.env` IDs for another server.
- Old advanced setup helpers are not part of the normal public setup path.

---

## Recommended release test

Before giving the bot to another server, test this exact flow in a fresh server:

```text
Invite bot
/stoney setup
Fresh Server
Create Missing Defaults Now
Health Check
Advanced Setup → Ticket Menu Options
Create Recommended Ticket Menu if needed
/ticket-panel post
Open ticket
Close ticket
Reopen ticket
Delete ticket
Check transcript
Check modlog
Restart bot
Confirm /stoney setup still works
```

If all of that works, the server-owner setup path is ready for beta testing.
