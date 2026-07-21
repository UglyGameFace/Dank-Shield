# Dank Shield

Dank Shield is a Discord ticket, verification, moderation, and setup assistant built to feel simple for server owners while still giving staff powerful tools.

The normal public setup path is intentionally boring:

```text
/dank setup
```

You should not need to memorize setup helper commands.

---

## What Dank Shield does

- Creates and manages support tickets.
- Lets staff close, reopen, delete, archive, and review tickets.
- Supports verification roles and VC/text verification workflows.
- Tracks modlog coverage for important server events.
- Provides spam/invite guard controls.
- Stores setup per server, so one server does not use another server's roles or channels.

---

## Quick start for server owners

### 1. Invite the bot

Invite Dank Shield with permissions that allow it to manage the workflow:

- Kick Members
- Ban Members
- Manage Channels
- Manage Roles
- View Audit Log
- View Channels
- Send Messages
- Send Messages in Threads
- Embed Links
- Attach Files
- Read Message History
- Manage Threads
- Manage Messages
- Moderate Members
- Move Members

Keep the Dank Shield bot role above roles it needs to assign, such as Unverified, Verified, Member/Resident, and ticket staff roles.

### 2. Run setup

In Discord, run:

```text
/dank setup
```

The normal setup flow is one guided path:

```text
Start Setup
Choose a setup plan
Set Up This Step (or Continue Setup for Choose Core Features)
Automatic Setup Check
Test Your Setup
Finish Setup
```

### 3. Follow the guided steps

Dank Shield asks for one required item at a time. Choose an existing role/channel or let Dank Shield create the missing item when that step supports creation.

Use **Manage Setup** for secondary tools such as changing the setup plan, optional settings, Review Setup, permission repair, backups, Server Design, or starting over.

SpamGuard is enabled by default for normal new-server setup. Owners can still turn it off explicitly from the protection/settings controls.

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
/dank setup → Manage Setup → All Features & Settings → Setup Plan & Server Items → Choose Roles & Channels
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
/dank setup → Manage Setup → All Features & Settings → Tickets → Ticket Choices
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
/dank setup
/dank help
/dank commands
/dank spam panel
/dank cleanup status
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
/dank spam panel
/dank spam status
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

### I do not see `/dank setup`

- Restart/redeploy the bot.
- Wait for Discord global command propagation.
- Check the boot logs for command registration errors.
- Run `/dank commands` if available.

### Setup says a role cannot be managed

Move the Dank Shield bot role above the role it needs to assign/remove.

### Setup says a channel/category is missing permissions

Give Dank Shield permission to View Channel, Send Messages, Embed Links, Read Message History, Manage Channels where needed, and Attach Files for transcript channels.

### Ticket menu options are missing

Run:

```text
/dank setup → Manage Setup → All Features & Settings → Tickets → Ticket Choices → Create Recommended Ticket Menu
```

### Health check still fails after setup

Fix the first blocker shown, then use **Continue Setup** or **Review Setup** to check again. Do not chase optional warnings before required blockers.

---

## Public-server safety rules

- Dank Shield stores setup per server.
- A server must save its own roles/channels/categories.
- If setup is missing, ticket/staff workflows stay locked instead of guessing.
- The bot should not rely on one server's `.env` IDs for another server.
- Old advanced setup helpers are not part of the normal public setup path.

---

## Recommended release test

Before giving the bot to another server, test this exact flow in a fresh server:

```text
Invite bot
/dank setup
Start Setup
Choose the setup plan you want to test
Use Set Up This Step until Setup Check reports ready
Test Your Setup
Finish Setup
Manage Setup → All Features & Settings → Tickets → Ticket Choices
Confirm the intended ticket choices
/ticket-panel post
Open ticket
Close ticket
Reopen ticket
Delete ticket
Check transcript
Check modlog
Restart bot
Confirm /dank setup shows the finished Setup Summary
```

If all of that works, the server-owner setup path is ready for beta testing.
