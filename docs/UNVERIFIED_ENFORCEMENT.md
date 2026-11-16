# Mod Center: unverified reminder enforcement (opt-in)

## Behavior and scope

Visible routes: `/mod → Unverified Enforcement` and
`/dank home → Verification Center → Unverified Reminders → After 3 Reminders`.

Owners and authorized server managers choose **NONE**, **KICK**, or **BAN**
and a 1–720 hour grace period. Existing deployments are **NONE/OFF** by
default. The manager must explicitly type `CONFIRM`; kick requires the
manager's Kick Members permission, ban requires Ban Members. Bot permissions
are also verified before enabling. Enabling is rejected unless automatic
reminders have exactly three as the per-join maximum and the private
enforcement ledger is healthy.

Only successful, Discord-acknowledged, *scheduled* reminder sends count.
Attempted but failed DMs/channel messages and all manual reminder sends do
**not** count. The count begins only after the server opts in; already
exhausted historical reminder counters do not cause retroactive removals.
Members receive a grace period after the third confirmed scheduled delivery.

At action time, the bot re-reads the guild policy and fetches the member
again from Discord. It checks unchanged join generation, canonical
verification role truth, the member's access to the verification channel,
bot/server owner exemptions, staff/moderation permissions, bot's Kick/Ban
permissions and bot role hierarchy. A newly verified or protected member
is never intentionally removed. A single database reservation prevents
cross-worker duplicates and automatic retries after a crash or ambiguous
Discord error. Successful actions are reported to the configured modlog
channel if available; failed outcomes require manual staff review.

Because the original scheduled ledger intentionally counts *attempts*
before Discord delivery, the opt-in enforcement ledger is separate and
stores only confirmed deliveries. Changing the configured maximum away from
three requires disabling enforcement first.

## Production release gate

This is an **additive PostgreSQL migration**, not an automatic startup
migration. It must be applied in the existing private Discloud database
with the same `DANK_VERIFY_REMINDER_DATABASE_URL` used by reminder delivery.
Do not substitute an unrelated database.

1. Confirm an off-host backup, the private database identity, available
   capacity and authorized maintenance window.
2. Deploy the code on the existing Discloud Dank Shield app (do not
   replace credentials or create another app).
3. From that app's private runtime, execute
   `python -m stoney_verify.verification_reminder_postgres --apply-enforcement`.
4. Open **Unverified Reminders → Check Database** and confirm
   `Enforcement ledger: Ready`. This check never turns actions on.
5. In a controlled test guild, set the schedule maximum to three and
   explicitly select KICK with a short grace period. Test three acknowledged
   scheduled reminders, a verified-before-deadline member, a blocked DM,
   and a protected staff member. Inspect Discord audit/modlog and PostgreSQL
   outcomes. Test BAN separately only with consenting disposable accounts
   and zero history deletion.
6. Verify the exact deployed commit and interaction permissions on Android,
   and verify restart doesn't issue a duplicate action.

**No kick/ban can be called production-proven from merged code or passing
CI alone.** Without an approved database migration and live canary, the
new setting stays unavailable. The previous scheduled reminder and
manual reminder paths remain usable independently.

## Status

Implementation branch: `feature/unverified-reminder-enforcement`.
No private Discloud migration, production restart, or live enforcement
has been performed by this code change. Resolve CI and an owner-approved
maintenance window before enabling any destructive policy.
