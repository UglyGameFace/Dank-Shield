# Action & Reminder Center (Issue #367, Slice 4A)

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
  the member's real join date but **assumes no previous claims**, so it is not
  a production delivery promise.
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
