# Member Intelligence: one actionable view and accurate readback

## Owner issue

After PR #804 merged to `main`, selecting a member under
`/dank members` → **Intelligence** could hide the new
**External Sources** and **Safety Reports** buttons. The callback first
edited the private member panel and then sent a *second* Stored History
message. On mobile, the second history-only message appeared to be the
result, with no buttons. Historical lookup by ID had no actions either.

The stored history reader also returned empty arrays when backend requests
failed, then displayed the empty arrays as zero activity. A cached
`guild_members.times_joined` could undercount available `member_joins`
rows. The historical first-join timestamp came from only the most recent
bounded join sample. A stale profile invite code could prevent recovery of
matching original join-event evidence.

## Changes

- Select a current member: render **one private evidence report** with
  its action buttons. Do not send a second detached history-only message.
- Select a departed/banned user by ID: render the **same review actions**,
  including Stored History, Connections, Safety Reports and External Sources.
- Historical refresh from member or Connections views retains its
  navigation. An unavailable or timed-out database keeps the actions visible.
- The main report includes current **live Discord facts** only when a REST
  member lookup is verified: display, account created, current join date,
  roles. Stored records remain clearly separate.
- The retained evidence readback now requests up to **15 joins, 30 events,
  15 tickets**, shows up to four recent events and three recent tickets,
  and lists up to five retained names. These are recent samples.
- The connected dedicated DankShield Supabase database also has a separate
  `activity_feed_events` table and a per-member `member_activity_ledger`.
  The owner-requested Member Intelligence report now reads the **30 newest**
  activity-feed records by exact `guild_id` plus `target_user_id`, plus the
  member's own tracked last activity date/type. The feed's moderation count
  is shown separately from `member_events` and **never summed together**
  as independent incidents: the feed may already represent staff actions
  present in other databases.
- On 2026-11-02, a read-only inspection of the *connected* DankShield
  Supabase project found **4,841** activity-feed rows, **3,369** moderation
  feed entries with exact target Discord IDs, **672** member-event rows,
  **3,369** staff-action log rows (potential mirrors of feed records) and
  **341** activity ledger records. These are project-wide row counts,
  **not** evidence that a particular member has any record, nor proof that
  the live Discloud app is currently connected to this project.
- Feed/ledger outages are explicitly labeled `activity_feed` and
  `activity_ledger` unavailable; no query is made for unrelated guilds
  or a staff actor when inspecting a target user.
- A join count is a **minimum of available recorded evidence**. A missing
  `in_guild` value never defaults to true; live Discord REST is the
  authoritative current-membership check.
- A saturated join readback fetches the oldest retained join separately
  when possible. This is the earliest *retained* join, not necessarily the
  account's first-ever join.
- Member, join, event and ticket readers have an optional strict mode
  used only for forensic snapshots. The existing soft-fail behavior of other
  bot services and the ticket repository remains unchanged. Source errors
  are presented as **unavailable**, never as zero/no misconduct.
- Latest join/source/quality fields in the UI use the vetted relationship
  result, not fresh fallback to possibly stale raw profile values.
- Keys and paid-provider behavior are not changed. No third-party service
  provides retroactive Discord history, and no automatic cross-server
  membership inference is introduced.

## Production verification

1. Confirm PR head CI passes and merge to the existing `main` branch.
2. Confirm the existing Discloud bot restarted with the exact merged commit
   SHA (branch is always `main`, but SHA matters).
3. On Android, open `/dank members` → Intelligence → select a current
   member: one report with External Sources and Safety Reports buttons.
4. Use **Stored History by ID** with a departed/banned member: the
   historical evidence panel must also have buttons.
5. Verify live roles/join date against Discord, and retained event/ticket
   samples and the separately labeled activity feed/ledger rows against the
   actual connected database, not a test fixture.
6. Block the database or remove read access in a disposable environment:
   report source(s) as unavailable; no false clean history.
7. Check a stale invite profile vs a newer join: no old invite, and
   event recovery only when the original event timestamp matches.
8. Confirm no automatic punishments, member-access leaks, or changes to
   unrelated tickets, activity tracking, verification and lifecycle code.

**Boundary:** These changes repair known reader and navigation defects.
They do not prove the live database has complete historical records.
Original events that were never collected cannot be recreated.
