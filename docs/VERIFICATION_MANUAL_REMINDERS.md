# Verification Center: manual reminders (separate from scheduled delivery)

## What owners see

Go to `/dank home → Verification Center → Unverified Reminders`.

- **Configure**: sets the existing automatic first delay (1–720 hours), repeat
  interval (12–720 hours), and maximum attempts (1–5 per join).
- **Check Scheduled Due**: immediately runs one bounded pass of the **automatic**
  rules. It never skips first delay or repeat limits, and its zero count is NOT a
  full-guild verification audit.
- **Send Reminder Now**: opens a **private page preview**. It counts the 100
  Discord members inspected, how many remain unverified on that page, access
  blockers, and how many would still be too early for automatic reminders.
  It displays up to three selected people and the exact message. **Next 100
  Members** looks farther into the server if no desired candidates appear.
  **Confirm Manual Send** then performs fresh permission/role/member checks and
  independently reserves the manual attempt before sending.
- Manual reminders can reach still-unverified members who joined minutes ago.
  They do not affect the automatic 24h/48h timing or consume the automatic
  reminder ledger. Manual sends work with automatic scheduling disabled when
  the saved delivery mode is Channel and Simple Verify remains active.

## Safety / clear outcomes

Requires the server's saved Simple Verify mode, both mapped roles, verification
channel, and a saved reminder **text channel** where the bot can post. Never
silently sends DMs or uses the channel where staff clicked the menu.

Only members currently pending per canonical role truth, able to see both
channels and still present in the guild may receive a manual reminder. Bot and
staff are excluded by existing role truth. Preview is advisory: live changes
can reduce the final number of recipients.

Manual budget: **up to three recipients per confirmation**, maximum one batch
per server per 60 seconds and four batches per hour; **at most two manual
attempts per member per join** with a 12-hour per-member cooldown. PostgreSQL
claims are durable across shards and restarts. Discord failures count as
attempts so retries cannot accidentally spam members.

The final private result distinguishes selected, reserved, successfully
delivered, Discord failures, people who ceased qualifying, and manual limits.
No result claims to represent every unverified member in a server unless every
page was actually scanned.

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
