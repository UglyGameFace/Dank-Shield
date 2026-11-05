# Native Birthdays in Dank Shield

Birthdays are an optional **member celebration**, not an age-verification method.
Members privately enter a full birth date including the year. The year is never
included in public announcements or the upcoming list. A self-reported date
is **not** proof of age or a Discord age-verification mechanism.

## Member flow

- Open the existing Profile Builder and press **My Birthday**, open
  `/dank birthdays me`, or use `/dank birthdays set` for the private date modal.
- Enter your full birth date as `MM-DD-YYYY` (for example `06-20-2002`).
  Valid calendar dates and past years are required. All sharing options default **off**.
- Individually opt into public wishes, a temporary birthday role, and private DMs.
- Use **Remove My Birthday** to stop future celebrations and delete the saved date.
  An active role lease is revoked immediately when Discord permissions permit it;
  otherwise the expiration worker retains the removal task.

Members are stored **per guild**, so joining another guild does not silently share
a birthday or opt-in there. The public upcoming list includes only members with
public birthday wishes enabled.

## Server manager flow

- Open the existing Profile Builder and press **Birthday Setup**, or use
  `/dank birthdays setup`. Members can run `/dank birthdays upcoming` to see
  only opted-in month/day celebrations, never birth years.
- Choose the announcement channel, a timezone and daily celebration hour (default 09:00),
  optionally create a permissionless `🎂 Birthday` role or select a safe existing role.
- Choose whether February 29 birthdays fall on February 28 or March 1 in nonleap years.
- Enable celebrations. The feature remains disabled until staff explicitly does so.

Channel and role selection use the shared searchable, paged server-item browser.
**No Birthday Role** clears the optional server role without deleting member dates
or preferences. Date submission and preference edits acknowledge before storage I/O;
the resulting private panel shows the saved state. Opening a date modal performs no
storage read. The upcoming list shows each member once, ordered by their next
celebration date, including the configured leap-day rule.

The role is not a self-selected cosmetic role: birthdays are assigned and removed by
the birthday worker. Existing protected/verification/staff roles are never accepted.
The worker uses timezone-aware dates including daylight-saving transitions, and stores
temporary role leases with an expiry at the *next local midnight*.

## Storage and deployment

Apply `supabase_fresh/migrations/202611040001_dankshield_native_birthdays.sql`
then `supabase_fresh/migrations/202611040002_dankshield_birth_year.sql`
to the **dedicated** Dank Shield Supabase project `viamriafkbqhquvrsnua`
through its reviewed migration process. Verify service-role access, RLS, claim RPCs,
and index readback. Neither migration runs automatically just because code is merged. This PR does **not** apply migrations or change credentials.

The schema has four service-role-only tables:

- `dank_birthday_members`: private per-guild full birth dates and explicit opt-ins;
  pre-existing yearless records stay intact until members enter their year.
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

`tests/test_production_interaction_stabilization.py` and
`tests/test_birthday_delivery_and_lifecycle_compatibility.py` cover acknowledgement
ordering, private responses, full-date edits, preference preservation, exact annual
leap-day matching, worker reservations, consent rechecks and role-lease recovery.

## Welcome and activity setup

`/dank welcome`, the Home destination and Setup Home open **Member Welcome &
Activity**. **Card Design** opens the existing Join/Exit studios, **Text Announcements**
opens the existing text editor, **Operational Log Channel** selects the separate
record destination, and **Static Welcome Help** opens the persistent-message editor.
Staff Modlog remains independent.

Legacy channel aliases remain readable. Editing an operational log writes only
log-specific aliases and pins the server's effective legacy exit-card state and
destination to their existing explicit settings. It preserves goodbye-text aliases,
artwork, staff logs and unrelated settings. This is an additive migration within
the existing config JSON when the manager saves; no SQL migration or reset is needed.
The obsolete startup patch that replaced the log picker has been removed.

**Do not deploy automatically**: exact-main CI, verified independent off-Discloud
backup, observed Discloud live SHA, explicit release approval, canary, and tested
rollback still apply.
