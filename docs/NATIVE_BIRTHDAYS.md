# Native Birthdays in Dank Shield

Birthdays are an optional **member celebration**, not an age-verification method.
No birth year, age, identity document, or proof-of-age claim is collected.

## Member flow

- Open the existing profile panel and press **My Birthday**, or use
  `/dank profile birthday` or `/dank birthdays me`.
- Enter a calendar date as `MM-DD`. All sharing options default **off**.
- Individually opt into public wishes, a temporary birthday role, and private DMs.
- Use **Remove My Birthday** to stop future celebrations and delete the saved date.
  An active role lease is revoked immediately when Discord permissions permit it;
  otherwise the expiration worker retains the removal task.

Members are stored **per guild**, so joining another guild does not silently share
a birthday or opt-in there. The public upcoming list includes only members with
public birthday wishes enabled.

## Server manager flow

- Open the existing Profile Builder and press **Birthday Setup**, or use
  `/dank birthdays setup`.
- Choose the announcement channel, a timezone and daily celebration hour (default 09:00),
  optionally create a permissionless `🎂 Birthday` role or select a safe existing role.
- Choose whether February 29 birthdays fall on February 28 or March 1 in nonleap years.
- Enable celebrations. The feature remains disabled until staff explicitly does so.

The role is not a self-selected cosmetic role: birthdays are assigned and removed by
the birthday worker. Existing protected/verification/staff roles are never accepted.
The worker uses timezone-aware dates including daylight-saving transitions, and stores
temporary role leases with an expiry at the *next local midnight*.

## Storage and deployment

Apply `supabase_fresh/migrations/202611040001_dankshield_native_birthdays.sql`
to the **dedicated** Dank Shield Supabase project `viamriafkbqhquvrsnua`
through its reviewed migration process, then verify service-role access, RLS, claim RPCs,
and index readback. This PR does **not** apply migrations or change credentials.

The schema has four service-role-only tables:

- `dank_birthday_members`: yearless, private per-guild dates and explicit opt-ins
- `dank_birthday_guild_settings`: server setup and indexed next-due celebration slot
- `dank_birthday_deliveries`: atomic once-per-date per-action reservations
- `dank_birthday_role_leases`: durable midnight removal and bounded retry claims

The bot uses its current authoritative Supabase client, with no new external service
or fallback to the original IdleGrowOp project or Discloud PostgreSQL.

An indexed RPC claims only due guilds rather than polling every guild; another
RPC reserves each member delivery before a Discord API call. This deliberately
prioritizes **no duplicate birthday announcements**, including on restart,
over at-least-once retries when the Discord send result is ambiguous. Late
celebrations older than three hours are skipped. Role removals are retried until
confirmed. Storage outages stop delivery and back off rather than silently
manufacturing records.

## Validation and release boundary

Run `python -m pytest tests/test_native_birthdays.py` and project CI, then test
with a server role below the bot in a Discord test guild. Verify timezone,
role grant/revoke, lack of unexpected pings, private opt-ins, restarts,
February 29, missing permissions, and migration preflight.

**Do not deploy automatically**: exact-main CI, verified independent off-Discloud
backup, observed Discloud live SHA, explicit release approval, canary, and tested
rollback still apply.
