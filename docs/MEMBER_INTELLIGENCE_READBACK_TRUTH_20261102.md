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

## Smart caching (staff history only)

- The normal reader remains uncached. Only a staff-authorized Member
  Intelligence screen opts into the process-local cache. It keys by the exact
  server ID, user ID, read limits and enabled source families. It never
  caches staff authorization decisions, external provider API results,
  staff verdicts or live Discord membership/roles.
- Bounded to **128** account snapshots, **25 seconds** for evidence, **5
  seconds** for empty records. Every duplicate simultaneous lookup within
  one event loop shares a single read. A cancelled Discord interaction does
  not cancel another user's pending lookup.
- Tapping **Stored History** bypasses the fresh cache; live account selection
  always verifies current membership independently through Discord.
- Join/leave/ban/profile-update Discord events invalidate the corresponding
  exact account. Completion of persisted join context invalidates again so
  an in-flight read cannot repopulate evidence from before the write.
  Other write families remain protected by the short TTL and forced refresh;
  this is not a distributed cache across Discloud workers.
- Incomplete or failed reads never become cached clean results. A partially
  fresh read shows its missing sources. When **every** source is unavailable,
  the previous complete result may be shown for at most **120 seconds**,
  clearly marked **STALE**, with the failed source names and cached age.
  After that, the report is unavailable, not cleared/empty.
- External safety providers retain their **separate** 15-minute positive
  account-response cache and consent-based, on-demand request flow. Cache
  eviction now drops only the oldest accounts, not every account at the cap,
  and cached findings explicitly show their age. No disabled paid lookup is
  enabled or triggered by the staff-history cache.

Test cancellation, concurrent same-account lookup, per-guild isolation, bounded
capacity, TTL/negative TTL, failure handling, forced refresh, mutation safety,
in-flight invalidation and live Discord status separation. These snapshots
are review aids only, never evidence that enables automatic bans.

## Production verification

1. Confirm PR head CI passes and merge to the existing `main` branch.
2. Confirm the existing Discloud bot restarted with the exact merged commit
   SHA (branch is always `main`, but SHA matters).
3. On Android, open `/mod` → Member Command Center → Intelligence → select a current
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
