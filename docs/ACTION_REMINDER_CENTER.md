# Action & Reminder Center (Issue #367, Slice 4A)

## Operational checkpoint, 2026-10-23 (4D.5C) and deferred DM canary

- Production private Discloud database `dankshield` was backed up to a private encrypted v2 Drive ZIP, successfully decrypted on Android and restored independently into an isolated Termux PostgreSQL instance before writes.
- Owner separately approved both manual migrations; `202610230001` claim/budget and `202610230002` canary audit were applied and checked in the live database. Read-only verification: DB identity `('dankshield', 'dankshield')`; row counts `(delivery,budget,audit,verify)=(0,0,0,3)`; `ready_sync`, `ledger_ready_sync`, `audit_ready_sync` all True.
- Application-owner Discord Readiness Report now confirms configured PostgreSQL, claim schema and audit schema. GitHub CI and live runtime source fingerprints match `549534fd81e572cf`. Declared deployment SHA is unavailable; the report deliberately cannot independently attest it.
- **Owner deferred the live one-member DM canary until later.** All canary target/approved-source environment gates remain disabled, no Action Center claims or DMs have occurred, and the future automatic scheduler is not activated. Opt-in by itself does not bypass existing owner/Administrator exemption or authorize a DM.
- Next reviewed 4E slice adds a **self-service read-only reminder eligibility explanation** to My Reminder Preferences, using the canonical source+ledger inspection. This is not canary approval, a new worker, or scheduler activation.

## Slice 4F: existing notification owner directory (read-only integration)

**Scope:** expose one staff-only "Notification Systems" destination in the
existing Action & Reminder Center. It is a **directory into the existing
canonical owners**, not a cross-feature sender, single database, or new
activation switch. Per-guild staff checks are re-evaluated on each click;
owner destinations independently enforce their own permissions. Ordinary
members still see only their private actions and DM preference controls.

The six pre-existing notification domains are:

| System | Existing code owner | Navigation destination | State authority |
| --- | --- | --- | --- |
| Simple Verify reminders | `verification_reminders` | Verification Reminders | Verification role truth + private Discloud PG claim ledger |
| Member activity notices | `commands_ext/public_members_group` | Members & Moderation, `/members notices` | Existing member notice records/worker |
| Ticket SLA and inactivity | `workers/ticket_automation_worker` | Tickets | Ticket state and settings |
| Quiet Server Notices | `community_tools_runtime` | Community Tools → Quiet Server Notice | Existing community quiet notice store |
| It's 4:20 Somewhere | `somewhere_420_runtime` | It's 4:20 Somewhere | Indexed schedule owner and claim RPC |
| /toke session reminder | `toke_sessions` | Toke Sesh Settings, live session | Existing opt-in session state |

The Action Center **must not infer whether these systems are enabled**
just from the directory, duplicate their completion truth, migrate their
storage, or merge their schedulers. A future shared diagnostic layer must
call each owner's read-only adapter with explicit unavailability; a future
Member Setup sender must reuse the proven rate-budget/claim patterns while
remaining separately approved and disabled by default.

**Excluded:** live canary, new worker, DMs, PostgreSQL or Supabase writes,
environment switches, migrations, automatic scheduling, branch cleanup and
other epic features.

## Slice 4G: first-class Actions & Notifications category

The `/dank home` section picker now includes **📋 Actions & Notifications**
as its own category. The existing Action & Reminder Center moves out of
Onboarding & Access. All feature callbacks keep the exact canonical source
owners; **this change is navigation only**.

Five entrypoints are registered under this category:

- **Action & Reminder Center:** the existing private Action Inbox.
- **Notification Systems:** staff-only directory into the six existing
  notification owner controls.
- **Action Builder (Draft):** staff-only rule editing, no active delivery.
- **Schedule & Readiness (Draft):** staff-only timing/limits, private
  read-only readiness checks and the already locked app-owner canary console.
- **My Reminder Preferences:** per-member, per-guild consent and read-only
  reminder eligibility status.

Verification, Member Setup and Verification Reminders remain in
**🚪 Onboarding & Access**. The six existing notification features keep
their original Home locations as well. This adds no second scheduler,
per-guild database, live notification switch, new worker, delivery history
dashboard or sending. Any missing future diagnostics must be honestly
marked as not shipped until implemented.

## Slice 4H: saved policy status from the existing notification owners

From `/dank home → 📋 Actions & Notifications → Notification Systems`,
authorized guild staff can choose **Check Saved Status**. This is an
on-demand, private, read-only snapshot, not a monitoring worker.

- Simple Verify reminders: the existing guild config + Verification Reminders
  policy parser reports only whether the **saved setting** is on or off.
  It does **not** claim that member verification is pending or PostgreSQL
  delivery is healthy.
- Quiet Server Notice: the existing per-guild quiet configuration reports
  saved on/off or no configuration. No channel send or notice record mutation.
- It's 4:20 Somewhere: the existing indexed **guild-wide** schedule reports
  saved on/off or no guild schedule. **Personal subscriptions are not scanned
  or reported.**
- Member Activity Notices, Ticket Automation, and /toke session reminders:
  the directory explicitly reports **owner-managed / not inspected**,
  retaining the existing owner links. Ticket Automation's general settings
  helper currently swallows storage failures into defaults, so treating its
  returned `enabled=False` as confirmed disabled would be misleading.
- All three checked sources are independently bounded to eight seconds,
  isolate errors, and never display exception details. An unavailable
  source is not silently interpreted as disabled or unconfigured.

This slice intentionally provides **saved policy state**, not queue depth,
health attestation, successful delivery, auditing, or a new unified
scheduler. The production single-user Action Center DM canary is still
deferred and disabled. No database migration or owner feature mutation.

## Slice 4I: bounded opted-in Member Setup candidate source

The next live-delivery prerequisite can reuse **the existing profile guild
settings store**, instead of scanning every Discord member or constructing
a separate reminder database. `profile_card_service` now offers a narrowly
bounded, indexed-by-guild namespaced JSONB-flag read with a stable cursor.
The Discord snowflake `user_id` field is **TEXT** in this existing table,
so cursor comparisons honor the database's lexical order.

`action_member_consent.read_member_setup_opt_in_page` reads at most ten
records for one guild; it verifies each saved member's **versioned, timestamped
opt-in**, returning only a private list of candidate IDs and optional
next-page cursor. It **never scans every guild member**, assumes a cached
Discord membership, or treats old member opt-ins as current permission.

These candidate IDs are **NOT due reminders**. A future gated sender must
still confirm live guild membership, staff exemption, published Member Setup
revision, consent at send time, quiet hours, scheduled cooldown, private
PostgreSQL ledger budget, durable claim-before-send, and consent/completion
again immediately before delivery. **No worker consumes this feed yet.**
Automatic sending and the deferred single-member canary stay disabled.

No new tables, migrations, scheduler, background task, slash command or
production data mutation are part of this slice.

## Slice 4J: integrated Member Setup reminder dry-run workflow

In **/dank home → 📋 Actions & Notifications → Schedule & Readiness
(Draft) → Preview Opted-in Members**, authorized staff can review a
**private, read-only, paged** inspection of opted-in Member Setup reminder
candidates.

This is one connected workflow reusing the existing authorities:

1. The existing profile store's server-filtered JSONB opt-in source returns
   **up to five records per page**. It does not load all guild members.
2. The existing Member Setup publisher is consulted for the current active
   published revision; without that revision the report has nothing to check.
3. For each opted-in ID, use **guild.get_member (local cache only)**. Unknown
   cache state never triggers Discord REST fetch or becomes "departed".
4. For known non-bot members, the existing Action Center readiness owner
   rechecks per-guild consent, exemptions, canonical completion/revision,
   quiet-hour timing, cooldown, daily caps and actual private PostgreSQL
   claim history. No assumption that a failed ledger read means zero claims.
   Maximum two concurrent expensive reads, per-read timeout 8 seconds.
5. Staff see **counts only** for `preview_due`, waiting, quiet,
   completed, exempt, stale, capped, missing cache or unavailable statuses.
   No recipient identities, DMs, claims, guild-wide Discord member fetches,
   new worker, or cross-server state. Next page / Restart re-evaluate
   live authoritative state with server- and user-bound interactions.

Even `preview_due` means **potentially eligible at inspection time only**;
it does not authorize delivery. Existing single-member canary, its
approval gates and production sender status are **unchanged, OFF and
operator-deferred**. The next gated delivery milestone must include
an explicit, reviewed production activation plan and an accepted live canary;
this PR does not activate an automatic scheduler.

## Slice 4K: automatic reminders v1 (code-complete release)

Dank Shield uses **two different existing notification owners**, intentionally:

- **Unverified members:** no individual opt-in required. The preexisting
  **Verification Reminders** worker automatically targets anyone still pending
  Simple Verify when the guild owner enables its verification reminder policy.
  Completion/role truth stops reminders. Its own verified-scope settings,
  PostgreSQL ledger, rate limits and history are unchanged. Action Center
  does **not** create another verification sender.
- **Member Setup revisions:** optional per-member **My Reminder Preferences**
  opt-in is separate. Guild staff control **Enable/Disable Automatic Setup DMs**
  in **/dank → 📋 Actions & Notifications → Schedule & Readiness**. Each guild
  defaults **OFF**, cannot enable without an active published Member Setup
  revision, included Builder rule and working private PostgreSQL delivery
  ledger. This toggle requires no Discloud environment variables.

When guild staff enable Member Setup automatic reminders, the runtime worker:
1. Starts once after guarded bot initialization plus 120-second quiet window.
   One pass every 180 seconds, max 8 guilds/pass and 4 claims/pass;
   max two claims per guild/pass.
2. Reads up to 5 explicitly opted-in profiles from one guild at a time
   through the **existing** profile-store JSONB namespace page. Uses a cursor,
   never a full Discord guild member list. At most 8 targeted Discord member
   REST gets per pass, strictly for opted-in IDs absent from the local cache
   or confirming their continued membership post-claim.
3. Rechecks the **canonical** Member Setup published revision/completion,
   member consent, exemptions, quiet window, retry interval and private
   PostgreSQL claim-history budget. No guessed statuses authorize sending.
4. Atomically commits a per-member/per-revision attempt and per-guild/day
   budget through the **already deployed private PostgreSQL schema** before
   sending. On claim failure or uncertainty: no send.
5. Rereads the server switch, schedule, owner completion, opt-out, revision
   and membership after the claim. If any check fails, suppresses the DM
   and keeps the claim consumed. Sends a single link-free, mention-free
   private message only on fresh successful checks.
6. Shows guild-private since-start attempt, sent, cancelled, failed and
   unavailable totals to staff in Schedule & Readiness. Runtime counters
   reset on restart; durable PostgreSQL claims and cooldowns do not.

**Release contract:** code-complete after exact-head CI and mock integration
tests pass, **not production-validated**. No live canary is forced on the
owner; no server auto-enables on deploy. After merge/deployment, guild staff
can opt in to automatic Member Setup delivery using the toggle. Owner will
perform real-world testing later and file follow-up bugs as needed.
No new migrations, tables, secrets, verification senders or root commands.

## Scope of this first slice

Find it under **/dank home → Onboarding & Access → Action & Reminder Center**,
or use **Find Feature → reminder center / my actions**.

This initial center is a **private, read-only inbox**, not a replacement for the
existing verification reminders or an automatic notification system.

### Member experience

- Open your own inbox inside a server.
- Pending **verification** comes from the existing per-guild configuration and
  canonical `role_truth.member_is_pending_verification`, never role-name guessing
  or a duplicated verifier. Choose **Find Verification** to get the actual,
  permission-checked verification-channel link; no stale panel message ID.
- Pending **Member Setup** uses the existing `public_member_setup._member_payload`
  checkpoint which already applies the canonical member setup revision, access
  gate, grandfathering, and staff exemptions. Choose **Review Member Setup** to
  open the native setup/confirm UI.
- An unknown/offline backend source is clearly marked **Not checked**; the inbox
  must not claim "all done" on incomplete evidence.
- Refresh reruns both owning sources. No global per-member scan, persistent
  cache of completion state, or new background listener.

### Staff experience

Authorized staff can open the existing **Verification Reminder Settings** and
**Member Setup Manager** directly from the same panel. Permission checks occur
at click-time in both this view and the canonical owner. The existing verified
reminder worker and its Discloud PostgreSQL claim ledger remain the sole owners
of verification nudges. The new inbox never sends an unsolicited DM.

### Data ownership and model

Each action has a stable source-derived ID, severity
(`required` / `recommended` / `announcement`), member target, canonical
source, pending condition, action route, schedule and delivery fields.

For slice 4A, schedule is `on_open` and delivery is `private_panel`.
No policy/database migration is needed. The immutable action model forbids
pretending a future schedule or automatic delivery exists. Only owners may
evaluate and complete their own conditions:

- Verification -> `role_truth` and existing Verify UI
- Member Setup -> `member_setup_service` and native Member Setup UI
- Reminder delivery -> existing `verification_reminders` worker and policy

This keeps a future Action & Reminder Center from competing with underlying
guild role truth, access gates, or reminder delivery. `ACTION_ROUTES` is a
bounded explicit dispatcher allowlist.

## Deliberately not in this PR

- Recurring generic events, staff follow-up queues, arbitrary member task
  creation, automated notifications, new opt-ins, role mentions or new schedulers.
- New tables/RPCs, Supabase or Discloud migrations, role/access mutations.
- Writing to Member Setup revision completion or verification state.
- Member Activity & Reverification, still a separate issue #367 slice 5.

Those require subsequent small slices with a validated source adapter contract,
durable claim/expiry queues, owner-controlled opt-in, quiet-hours/limits, and
delivery-state proof. No action should keep nudging after its owner has completed
the condition. Preview/impact first, targeted audience, no unsolicited mass DMs.

## Rollout and acceptance

1. Merge only after exact-head focused checks, full CI, navigation checks, and
   production safety audits are green.
2. Verify source availability and both pending/complete cases in a real guild,
   on Android Discord, including the offline-source status.
3. Test staff vs ordinary member access and confirm staff-only controls cannot
   be opened by another member or after staff permissions are revoked.
4. Verify the deployed SHA in Discloud. No DB or secrets change should occur
   as a result of this slice.
5. Close this slice without claiming that the entire Issue #367 Action &
   Reminder Center (scheduled/durable coordination) has shipped.


## Slice 4B: Action Builder / conditions (draft-only)

Staff access: `/dank home → Onboarding & Access → Action & Reminder Center
→ Action Builder (Draft)`. Existing guild-specific staff authority is checked
again on every interaction; ordinary members cannot see or edit the builder.

- Staff can configure **pending verification** and **Member Setup revision**
  rules for *members that the existing owner says are pending*. These are the
  two supported conditions, not a free-form expression interpreter.
- Each rule stores whether to include it **in the draft preview**, severity,
  and an optional short note (160 characters max). Verification cannot be
  downgraded below Required. Member Setup access-gated actions are still
  Required even when the draft labels them Recommended.
- Clicking **Preview Conditions** inspects only the requesting staff member's
  live authoritative Action Inbox. No member list sweeps, DMs or channel posts.
  An inaccessible source displays **unavailable**, not completed.
- Each guild draft is stored under its own `action_reminder_builder_v1`
  namespace in existing `guild_configs` using
  `compare_and_swap_guild_config_key`. Saves carry a revision; stale changes
  fail safely and require reopening the builder. Unconfigured defaults are
  empty and excluded; malformed/future versions refuse overwrites.
- Every serialized policy includes `delivery_enabled: false`,
  `schedule: manual_preview`, and `delivery: preview_only`. It is **not a
  new verification policy, role mutation, or message-delivery opt-in**.

**What this does not ship:** any active custom reminders, recurring event
timers, bulk audience targeting, membership-wide impact scans, or staff
follow-up queues. These need a separately reviewed 4C persistence/delivery
contract, durable idempotent claims, opt-in controls, quiet hours, and stop on
canonical completion. No database migration or new env secret is required.

Acceptance: focused builder+inbox+navigation tests, full runtime regression,
Android staff/ordinary member UI, stale modal conflict, and Discloud SHA proof.


## Slice 4C: Scheduling limits and durable-claim readiness (draft only)

Open `/dank home → Onboarding & Access → Action & Reminder Center
→ Action Builder (Draft) → Safety Schedule (Draft)` as server staff.

- Configure a validated **first wait** after server joining (12/24/48/72 hours),
  **repeat spacing** (48/72/168 hours), **maximum attempts** (1–3), local
  **quiet hours** (default 22:00–08:00; same start/end means no quiet window),
  **IANA timezone** (DST-aware), and **server daily cap** (1–10).
- Saved as `action_reminder_schedule_v1` in the existing per-server
  `guild_configs` namespace, with exact per-key compare-and-swap and revision.
  An unknown schema or concurrent/stale save is rejected. Default policy
  disallows delivery; the serialized payload always contains
  `enabled: false` and `delivery: preview_only`.
- **Preview Timing** inspects only the requesting staff member and the canonical
  Action Inbox source results. Unknown or completed conditions never become
  eligible. No member sweep, no notifications or access changes. Preview uses
  the member's real join date for verification and the later of join date
  and the **canonical Member Setup revision publication timestamp** for setup.
  If the publication timestamp is missing, the preview is blocked rather
  than falsely treating a new revision as overdue. It **assumes no previous
  claims**, so it is not a production delivery promise.
- A new, separate `postgres/migrations/202610230001_action_center_delivery_reservations.sql`
  declares only the future **Member Setup** per-revision attempt ledger and
  per-guild/day budget. It **does not change the existing** verification
  reminder ledger. The offline, fail-closed claim adapter reserves both
  membership and daily attempts in one PostgreSQL transaction; when the
  guild cap is reached, the entire transaction rolls back. Failure is treated
  as "no send", not a retry. `tools/migrate_action_center_postgres.py` is
  manual-only, does nothing by default and is never called on bot startup.
- **Important architecture boundary:** existing Simple Verify notifications
  still exclusively belong to `verification_reminders`. Action Center must
  not reserve a second verification reminder claim or deliver a duplicate DM.
  Scheduled Member Setup delivery itself is **NOT implemented or enabled** in
  4C. A separate 4D authorization/migration/live canary is required. Its worker
  must reread the canonical setup revision before AND after reserving, respect
  per-user opt-out/delivery preference, and avoid unbounded guild scans.
- This is **not a new active reminder system**. No production schema was
  changed by this PR. The staged SQL needs separate private-DB review,
  validated deploy credentials, test provisioning and an explicit migration
  operation before it can serve any future sender.

Acceptance: focused scheduling and DST rollover tests, dangerous config
rejection, saved-config CAS/stale modal tests, CI SQL smoke and private Android
panel canary; full bot regressions and Discloud deployed commit verification.


## Slice 4D.1: member-owned reminder preferences and delivery preflight

This slice establishes the **member consent boundary** for any future Member
Setup revision DM, but does not activate delivery. A member opens
`/dank home → Onboarding & Access → Action & Reminder Center
→ My Reminder Preferences` inside a guild.

- **Off by default**, stored per **member + guild** under
  `action_member_reminders_v1` in the existing private
  `profile_guild_settings` namespace. Explicit **Enable Future Setup DMs**
  and **Stop Setup DMs** controls are both member-owned and user-ID/guild-ID
  checked on every interaction. This is not a server-wide consent setting.
- Every choice is written and reread with the canonical profile namespace
  owner. A failed read, unknown schema or unconfirmed write blocks enabling
  future delivery; the UI never says a save succeeded before verification.
  Previous verification reminders and 4:20 DM opt-outs remain independent.
- `action_delivery_preflight.inspect_member_setup_candidate` is a
  **read-only, single-member** check. It reuses canonical Member Setup
  grandfathering, revisions and exemption logic, evaluates explicit private
  preference, draft inclusion and a matching published revision, then applies
  timing/quiet-hour rules. Stale, exempt, complete, opted-out, inaccessible
  or malformed state never passes.
- The preflight's `delivery_authorized` value is permanently **false** in
  this version, including for otherwise due candidates. Its planning result
  does **not** read actual PostgreSQL claim history, reserve a claim, send
  a DM or give any other module permission to deliver.
- No new table, schema migration, background worker, broadcast, new slash
  root, permission mutation or Discloud secret accompanies this slice.

### Required before 4D delivery activation

An independent follow-on slice must provision and verify the staged private
PostgreSQL 4C migration, read real claim attempts and daily budgets, provide
member opt-out before *and after* reservation, re-fetch Member Setup
completion and guild policy immediately before/after the atomic claim, prevent
multiple workers racing, bound REST fetch volume, perform only explicitly
authorized sends, and add privacy-aware lifecycle cleanup. Run a small,
auditable guild canary, verify actual deployed release/Discloud access, and
keep all reminder delivery disabled until these gates are met.

These controls should never make a pending verification reminder eligible
for a duplicate send: that feature's own verified worker retains sole
delivery authority.


## Slice 4D.2: real private ledger readiness, NO sending

Staff can open `Action Builder (Draft) → Safety Schedule (Draft) →
Check Delivery Readiness`. This **only inspects the requesting staff
member** (never a guild-wide member scan) and cannot activate reminder DMs.

- The 4C private PostgreSQL ledger is **still not migrated to production**
  by merging this PR. A missing table, secret, permission, network connection
  or malformed row is displayed as **ledger_unavailable**, not "zero claims"
  or safe to deliver. No automatic DMs, PostgreSQL inserts, claim reservations,
  new workers, new environment flags or public commands.
- The read-only adapter queries one `(guild,user,member-setup-revision)`
  attempt row and one server-local `(guild,date)` counter. The status can
  explain actual retry cooldown, maximum attempts, daily server cap, and
  DST-aware quiet hours once the ledger is separately provisioned.
- Candidate eligibility is checked **before and after** the ledger read, using
  fresh member opt-in, real Member Setup status, canonical grandfathering/
  exemptions, exact published revision and per-guild policy. If consent is
  withdrawn, a newer revision is published or the safety policy changes,
  the check blocks instead of assuming a prior pass is still valid.
- The UI confirms its results are **informational**. No status is an
  authorization to send, and `delivery_authorized` is immutable False.
  A snapshot cannot replace an atomic claim transaction and final
  fresh owner/opt-out/Discord membership checks in future 4D.3 delivery.
- Real PostgreSQL regression runs on a disposable PostgreSQL 17 service
  **with explicit opt-in**, never the broad bot suite or the live Discloud
  database. Tests prove two snapshot reads do not consume extra claims,
  revision isolation, daily-cap isolation and fail-closed offlines.

No work on 4:20, Cinema, branch retirement or the verification reminder
worker is included. Android Discord canary and actual Discloud deployed
SHA remain separate acceptance gates before claiming production readiness.


## Slice 4D.3: disconnected, manually authorized single-user canary dispatcher

This slice introduces `stoney_verify/action_canary_delivery.py` as an
**internal, unconnected dispatcher** for a single Member Setup revision.
It has no Discord button, slash command, scheduled worker, startup hook or
bulk member fetch. **Merging it cannot start sending messages.**

Before an explicitly reviewed canary **in a later rollout**, the operator
would need to: migrate and verify the already-staged private PostgreSQL 4C
claim tables, verify production credentials, confirm a real target member's
affirmative and fresh per-server DM consent, ensure the server draft includes
Member Setup revisions, and then deliberately set ALL FOUR exact-match
process-local settings:

- `DANK_ACTION_CENTER_CANARY_SEND=1` (absent/off by default)
- `DANK_ACTION_CENTER_CANARY_GUILD_ID` = an explicitly selected guild ID
- `DANK_ACTION_CENTER_CANARY_USER_ID` = an explicitly selected member ID
- `DANK_ACTION_CENTER_CANARY_REVISION` = that member's current **published**
  Member Setup revision number

These are gates, **not secrets**. They have not been configured or deployed
by this PR. This slice intentionally does **not** expose an invocation path
even when all four values exist. A later PR must approve and wire a manual
invocation only for an authorized canary, with an auditable staff action.

If that later manual invocation happens, the internal API first enforces exact
gates and current cached Discord membership, then checks private consent,
canonical current Member Setup completion and schedule, durable real claim
history, guild-local quiet hours, attempt and daily budgets. Before Discord
delivery it reserves the per-revision attempt **atomically** on the private
PostgreSQL ledger and limits a canary to **one attempt per revision and one
attempt per guild-local day** regardless of more permissive guild draft caps.

Following the committed claim it independently rereads owner status,
preference, policy, and exact-guild/recipient/revision gates. Any opt-out,
completion, rule revision, local-day rollover, missing source or uncertain send
consumes the attempt but **does not send or retry**. No automatic retry is
possible in this API. Discord DM text has no staff-defined content, roles,
mentions or external links. Verification reminder DMs remain owned entirely
by the existing worker.

Important limitations: there is no transactional lock across Discord and
Supabase, so a preference could theoretically change between the final fresh
read and the Discord API send. The post-claim check reduces this window but
cannot offer perfect atomic cancellation. A future real rollout must explicitly
accept/mitigate that residual race, add operational delivery result auditing,
and confirm privacy-aware ledger cleanup, monitoring and human rollback.

**Acceptance gates:** exact-head CI, disposable PostgreSQL 17 concurrency and
idempotency tests, no automatic caller/static audit, safe failed-send behavior,
real Discloud release SHA, reviewed private ledger migration and explicit
operator canary approval. None of these PR changes perform a migration, add a
production environment variable, or send a DM.


## Slice 4D.4: application-owner manual canary console and durable audit

The existing **Safety Schedule (Draft)** gains **Owner Canary Console**.
The console is deliberately visible inside staff controls but remains strictly
restricted to the **Dank Shield application owner** (Discord bot-owner API)
at open, confirmation and execution. Server ownership or Administrator
permission alone never authorizes a canary.

**No production delivery is activated by this PR.** The console reports
`Locked` unless ALL prerequisites already exist:

1. A current, safely cached guild member whose **per-server Member Setup
   revision DM preference was explicitly enabled**, the action is still due,
   and neither exemption nor canonical completion cancels eligibility.
2. All four exact-match process-local environment gates documented under
   4D.3. No values are configured, generated or changed by this PR.
3. The separately staged 4C private **claim** tables and this 4D.4 private
   **audit** table both exist, with appropriate SELECT/INSERT/UPDATE grants
   and the existing private reminder PostgreSQL DSN. Their migrations are
   intentionally never executed by startup or this PR.
4. An actual application-owner tap, followed by a **typed confirmation**
   `SEND guild_id/user_id/revision`. The confirmation can be submitted
   only once. All operator and recipient identities are rechecked afterward.

The owner console first commits a durable `requested` audit record with a
fresh UUID. If it cannot prove that write succeeded, **no claim or DM is
attempted**. Then the existing 4D.3 dispatcher rechecks source/consent,
reserves exactly one atomic per-member/revision, per-guild/day claim, and
rechecks conditions again before a single mention-free DM. A final audit write
records whether the attempt was claimed or a message was confirmed. If the
final audit update fails after a possible send, operators are instructed
**NOT to retry** because the claim may already be consumed.

Private additive SQL: `postgres/migrations/202610230002_action_center_canary_audit.sql`.
An explicit no-write-by-default helper
`tools/migrate_action_canary_audit_postgres.py` exists for a separately
authorized private-DB operation. It must not be run against Supabase or an
unverified database DSN. Audit records contain IDs, timestamps and status,
**no DM text**, attachments, secrets, or member profile data.

This is a **single manual canary**, NOT recurring reminder delivery.
4D.5 must provide a reviewed production migration, explicit one-user target
and approval, observability, private audit retention/cleanup, a safe Discloud
release verification, and a narrow end-to-end canary before permitting
scheduling. The existing verification reminder worker remains untouched.


## Slice 4D.5A: non-sending live readiness diagnostics and deployment proof

A merged PR is not proof that the `stoneyverify` process is executing it.
This slice creates an app-owner-only **Readiness Report** from
`/dank home → Action & Reminder Center → Action Builder (Draft) →
Safety Schedule (Draft) → Owner Canary Console → Readiness Report`.

This report is available **even when canary environment gates are disabled**.
It can report, without showing credentials or target IDs:

- The runtime's declared release SHA (if supplied), and a local source
  fingerprint. Critical Action Center canary, audit, consent and claim files
  are now covered by that fingerprint.
- Whether the dedicated `DANK_VERIFY_REMINDER_DATABASE_URL` is configured.
  Only a boolean is shown. Neither the DSN nor any database errors appear.
- **Read-only database permission probes** for both the 4C atomic Member Setup
  claim tables and the 4D.4 operator audit table, checking columns and all
  three privileges **SELECT, INSERT and UPDATE individually**, without
  executing a write. Missing/unmigrated/private-VLAN-unreachable tables are
  visibly blocked. No DDL, INSERT, UPDATE or DELETE is run.
- Whether all four canary flags are syntactically complete and the configured
  guild matches the server where the owner opened the panel. It does **not**
  print private target IDs and does not authorize a send.

**IMPORTANT: Source identification is not source attestation.**
A declared SHA can be absent or incorrectly set. The report always marks
GitHub-main equivalence unverified, because the deployed bot has no trusted
live GitHub-main comparison. The dedicated Action Center CI now prints the
same source fingerprint for that PR. An operator must compare the fingerprint
of the **exact approved merged release** with the running Discloud result,
and independently verify the deployment method and actual release SHA.
If fingerprints differ, treat runtime version as unverified and stop.

The owner console now uses actual **Discord application ownership** on its
own buttons, without incorrectly requiring a separate staff-role membership
for an app owner. It remains accessible *through* the existing staff Action
Center navigation: no new root command, no public owner bypass.

### Current blocking state / next production gate

- GitHub main after PR #698 was confirmed at
  `1d12e05d408bd5e7198d607422aad44a80c71927`.
  This is a **repository fact**, not evidence that Discloud runs that SHA.
  Any newer PR merge will advance it, so use current main when validating.
- The 4C and 4D.4 PostgreSQL migrations are staged, not applied by any Action
  Center PR. An independently approved migration to the *correct private*
  PostgreSQL database (not Supabase) remains required.
- The guarded existing-app Discloud workflow has release-safety inputs.
  Do not bypass its backup/data-recovery approvals or assume an automatic
  deploy occurred. A separate deployment check is required.
- No exact guild/member/revision canary flags have been configured here.
  Only an explicitly opted-in non-exempt member genuinely pending the
  published revision can be the pilot; the application owner confirms once.
- No production schema writes, Discloud deploys, new environment settings,
  claims or Discord DMs are performed by this slice.
- Live end-to-end acceptance and any later recurring scheduler require fresh
  human authorization after this read-only gate has been verified.

This diagnostics stage remains under the **single active task** lock.


## Slice 4D.5B: human-approved exact source lock (dormant by default)

The one-recipient dispatcher additionally requires
`DANK_ACTION_CENTER_CANARY_APPROVED_SOURCE_FP` to equal the running
`runtime_source_fingerprint()` **exactly**. This is a new,
absent-by-default, non-secret rollout gate in addition to the existing
four exact guild/member/revision/send gates.

An operator must independently examine the **full CI result for the exact
release intended for Discloud**, record the 16-character lowercase source
fingerprint printed by its dedicated Action Reminder Center CI, and compare
it to the bot's Readiness Report after a safely approved deployment. Only
after that review, and after confirming the rest of the private database
and member opt-in gates, can the operator explicitly set this approved
fingerprint in the live bot environment. Never take the approved value
straight from the running bot without independently comparing GitHub CI;
a compromised or stale runtime is not its own release authority.

The owner-only Readiness Report shows **approved fingerprint match** as
a separate boolean. This is a process-local lock, **not independent proof**
the running process corresponds to the current GitHub main commit; the
independently verified deployment evidence remains a manual prerequisite.
A blank, uppercase, malformed, or stale approval blocks the console and
dispatcher from sending, even when all four original target fields match.
The dispatcher rechecks the release lock before claims and immediately
before the Discord DM, preserving the cancellation-on-revocation path.

The Action Reminder Center CI still prints the fingerprint for the current
PR and exercises stale/absent/mismatched source gating. Since this PR changes
critical source files, **use its newly emitted fingerprint after merge**, not
the previous 4D.5A value `ee70d969f61b59e7`.

**No production schema writes, Discloud env changes, credentials, REST sends
or automatic reminders accompany 4D.5B.** The two separately staged private
PostgreSQL migrations and single opted-in-member canary still require a
subsequent explicit owner-approved production operation.
