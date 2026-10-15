# Member Center stabilization: historical evidence and role-neutral inactivity purge

This changes the existing Member Command Center; it adds no new slash commands,
migration, configuration flag, startup patcher or separate data store.

## Verified runtime defects fixed

- History buttons imported `review_history` from a module that only defines
  that callback inside the command registration function, causing runtime import
  errors. Both current-member history and by-ID history now call a dedicated
  staff-only read path.
- The ID modal previously called `fetch_user` before checking stored history.
  A departed/banned/kicked member can now be searched by numeric Discord ID
  even if Discord does not resolve the account, using guild-scoped historical
  member, join, event, ticket and verdict records. No retention is invented:
  missing rows are clearly identified.
- Slow guild config lookups occurred before acknowledgement on several button
  and purge paths. These now acknowledge first to avoid Discord interaction
  timeouts.

## Inactivity review and purge

- Activity review and purge scan **all current human member role types**,
  including unverified, pending, member, resident, verified and custom roles.
  Bots, staff, owner, protected roles, grace-period members and users above
  the bot hierarchy remain protected.
- For non-verified members, merely lacking a role or having no activity records
  is **not enough** for automatic cleanup. A removable candidate must have
  direct, dated activity proof inside the trusted tracker coverage window,
  must be older than the inactivity threshold and have medium/high confidence.
- If activity scope or continuous coverage is incomplete, removal fails closed.
  No Discord member cleanup can infer inactivity solely from offline status.
- The preview limits each destructive confirmation to 20 targets. Mass purge
  always requires staff confirmation, even when no-confirm queue settings
  exist. Existing queued review behavior remains separate.
- Previous cleanup re-ran a full-server Discord history scan for every target.
  Preview now uses its own completed scan for **read-only** preliminary
  protection/role checks, and confirmation runs **one fresh evidence scan**
  for the bounded batch. Each target still passes action-time role, staff,
  bot, hierarchy, active membership, scan-lock and authoritative proof checks.
  The shared proof expires after 45 seconds, stopping subsequent removals
  rather than acting on stale evidence.

## Release and live acceptance

Do not merge until exact-head checks are green. After merge, the new code does
not run on Discloud until the separate guarded main release is approved and
the existing app's runtime SHA verified. No database or Discord members were
mutated while preparing this PR.

Acceptance on Android/Discloud:
1. Open `/dank home` -> Safety & Moderation -> Member Command Center.
2. Exercise each navigation button, role/member selectors and back buttons.
3. Enter the ID of a previously kicked user in the Intelligence history modal
   and confirm retained events are shown without treating them as present.
4. Run all-member inactivity review, confirm that all role types appear and
   weak coverage makes everyone **review-only**.
5. In a test guild with known continuous evidence, preview a small purge
   batch, verify protected/active/new accounts are absent, then cancel first.
6. Only with an off-host backup and explicit owner authorization, verify one
   controlled removal and its audit event in the target test guild.

A completed CI run is not proof that the whole Command Center works on mobile;
interaction-based acceptance is still required.
