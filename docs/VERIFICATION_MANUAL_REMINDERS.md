# Verification Center: manual reminders (separate from scheduled delivery)

## What owners see

Open `/dank home → Verification Center → Unverified Reminders`.

- **Configure**: sets the *automatic* first delay (1–720 hours), repeat (12–720 hours), and its own per-join maximum.
- **Check Scheduled Due**: scans one bounded page against the **automatic** schedule; it never sends early.
- **Send Reminder Now**: fetches the full live guild membership with the existing shared, rate-budgeted membership enumerator. The current server's saved **Unverified role ID**, not the role's name, is the starting point. The canonical role-truth owner excludes bots and members who already have the server's mapped access or staff roles.
- Preview reports the total scanned, those with the mapped Unverified role, those still pending under server settings, access blockers, and manual cooldown exclusions. It never claims that an incomplete cache is complete. If an authoritative full scan fails, sending is blocked instead of reporting zero.
- Each group includes up to **25 eligible recipients**, all selected initially. Staff can optionally select fewer from the names menu. **Next Eligible Group** browses additional eligible users without fetching the whole guild again.
- **Channel** sends *one* message mentioning the selected members in the saved reminder channel. **Use DMs** makes the same manual preview send private messages to each selected member instead; it does not silently send to the public chat.
- New members can be reminded before automatic delay. Manual and automatic attempts have independent PostgreSQL claim histories and never reset one another.

## Safety and large guilds

- Per-guild configuration controls the pending/verified/staff/access roles, verification channel, delivery preference and message. No server name or role text is hardcoded. Role-mapping conflicts are counted for staff instead of being pinged as unverified.
- **No lifetime per-join cap on manual sends**. A durable 6-hour *per-member* manual cooldown prevents repeated pings. A server may start one manual batch per minute and up to four per hour, max 25 selected people per confirmation. These are independently enforced for channel and DM sends across bot restarts and shards.
- Staff receive a private delivery report, including successfully delivered, Discord failures and members who no longer qualify. DM permission failures are counted, not retried blindly.
- Recipient verification status, membership, join generation and channel visibility are rechecked immediately before and after durable claims. A setting change invalidates an old preview.
- Member privacy: only staff see previews; direct messages are sent only when the server owner has chosen DM delivery.
- The automatic reminder ledger and worker remain authoritative for scheduled reminders.


## Database release gate

**Code merge/deployment does not perform the database migration.** The separate
additive PostgreSQL tables are private, use existing
`DANK_VERIFY_REMINDER_DATABASE_URL`, and cannot alter automatic reminder rows.
The manual UI intentionally fails closed until the new tables and appropriate
database grants are present.

Before a real deployment: validate the off-Discloud backup of the *actual
private Discloud PostgreSQL database*, confirm its available storage, and make
sure maintenance is approved. Once the updated code is deployed on the existing
app and environment values are preserved, use the Discloud console (same app
working directory):

```bash
python -m stoney_verify.verification_reminder_postgres --apply-manual
```

This command exists in the deployed `stoney_verify` package even though
Discloud ignores the repository's `tools/` folder. It runs the **idempotent**
`postgres/migrations/202610250001_verify_manual_reminders.sql` file and verifies
the private schema. It never prints the DSN and never starts at bot launch.
Do not run it against an unrelated Supabase/Aiven/voice-bot database.

Then verify a **test guild/member** on Android: preview a newly joined member
before automatic delay, confirm a single manual mention in the saved channel,
check the private delivery result, repeat immediately (blocked), and check
automatic reminder history/intervals remain unchanged. Recheck after restart
and staff/role/channel revocation. Production completion requires these
checks, not a green GitHub CI badge alone.

This is the single reminder-worker delivery authority, not a new reminder bot
or an Action Center sender. LFG #638 remains separate.
