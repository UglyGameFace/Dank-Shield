# Dank Shield Production Readiness Command Center

This file is the source of truth for turning Dank Shield into a public, multi-server, premium-ready Discord bot without losing direction.

Every bug report, screenshot, PR, feature idea, and audit finding must map back to this command center before code is changed.

---

## Mission

Make Dank Shield public-production-ready for real server owners, staff teams, and future premium customers.

The bot must be:

- safe for many unrelated Discord servers
- simple enough for non-technical owners
- clear enough for staff and visually impaired users
- reliable under Discord/API/database failures
- resistant to duplicate commands, stale UI, and silent errors
- ready for premium gating before premium features are sold

---

## Non-negotiable rules

1. No monkey patches unless there is no safer option.
2. No isolated fixes that bypass centralized systems.
3. No feature work before the current blocker class is controlled.
4. No hidden errors, swallowed exceptions, or generic `interaction failed` paths.
5. No public-server behavior that relies on one private server's environment IDs.
6. No paid/premium release until entitlement gates, downgrade behavior, and QA exist.
7. Every fix must include a verification plan.
8. Existing ticket data, guild setup, roles, logs, and workflows must be preserved.

---

## Status labels

Use exactly these labels when tracking work:

| Label | Meaning |
| --- | --- |
| `BLOCKER` | Prevents public beta or creates customer-trust risk. |
| `HIGH PRIORITY` | Must be fixed before paid/premium launch. |
| `UX` | Confusing, unclear, slow, hard to see, or hard to use. |
| `SECURITY` | Permission, abuse, isolation, invite, spam, or moderation risk. |
| `DATABASE` | Schema, migration, persistence, atomicity, or data integrity issue. |
| `PREMIUM` | Plan limits, entitlement checks, billing safety, downgrade behavior. |
| `TESTING` | Automated test, manual QA, release gate, or reproduction case. |
| `DONE` | Implemented and verified. |
| `DEFERRED` | Intentionally delayed with a reason. |

---

## Current phase

**Phase 1 — Control the chaos**

Goal: establish a stable production-readiness process and visible startup diagnostics before changing deeper runtime systems.

Current priority order:

1. Production command center / tracker
2. Startup health and diagnostics
3. Central guild config resolver
4. Interaction failure wrapper
5. Ticket atomicity and orphan safety
6. Invite Shield native flow and scan behavior
7. Permissions diagnostics
8. Premium entitlement skeleton
9. QA suite

---

## Production readiness score

Current working score: **62 / 100**

Meaning: controlled beta only. Not yet ready for wide public release or paid premium membership.

Score can move up only when blockers are fixed and verified.

---

## Current blockers

### BLOCKER — Startup architecture is too patch/guard-heavy

The bot currently depends on a large startup guard chain. This increases risk that features appear fixed in one path while breaking silently in another.

Safe strategy:

- add startup health reporting first
- make loaded/failed/expected guards visible
- identify monkey-patch guards
- convert risky patch guards into native modules over time

Verification:

- startup report shows expected, loaded, failed, and missing modules
- failed imports are visible in logs/diagnostics
- bot can still boot after diagnostics are added

---

### BLOCKER — Runtime config must be truly per-guild

Public servers must never inherit another server's roles, channels, categories, staff role, ticket category, or verification setup.

Safe strategy:

- introduce a central GuildContext / config resolver
- migrate runtime reads to resolver one subsystem at a time
- keep env IDs only as controlled legacy/private fallback when explicitly allowed

Verification:

- two test guilds can use different roles/channels/categories
- missing setup locks unsafe actions instead of guessing
- diagnostics show exactly what is missing for each guild

---

### BLOCKER — Interaction failure paths need central handling

Any button/modal/select/command path that can timeout or throw must provide a clear user-facing response.

Safe strategy:

- introduce a central interaction guard/helper
- defer early where needed
- route errors to plain-language messages
- log structured failure details for staff/owner diagnostics

Verification:

- no common setup/protection/ticket button shows generic `interaction failed`
- duplicate clicks get a useful response
- permission/API/database failures are explained

---

### BLOCKER — Ticket creation needs DB-atomic numbering and orphan protection

Ticket numbers and channel creation must stay consistent under retries, restarts, and concurrent clicks.

Safe strategy:

- reserve ticket numbers atomically in the database
- make ticket creation idempotent by guild/user/category/request key
- record operation state before creating Discord channels
- repair or clearly report any partial failures

Verification:

- concurrent ticket creation does not duplicate ticket numbers
- failed DB insert does not leave untracked channels silently
- retries reuse or safely repair the same operation

---

### BLOCKER — Invite Shield needs native flow and clear scan behavior

Server owners need to understand exactly what Invite Shield protects, what it ignores, and whether old messages are scanned.

Safe strategy:

- move Invite Shield controls into native Protection Center code
- keep internal-server invite allowance explicit
- add manual scan with dry-run and confirmation
- never claim messages were removed unless deletion succeeded

Verification:

- Invite Shield can enable independently from Link Shield
- internal invites are allowed when configured
- external invites are blocked/deleted predictably
- manual scan reports scanned, allowed, skipped, deleted, and failed counts

---

## High-priority work

### HIGH PRIORITY — Permissions diagnostics

Owners should see the exact missing permission and where it is missing.

Required output format:

```text
Missing: Manage Messages
Where: #general
Needed for: deleting blocked external invite links
Fix: give the Dank Shield role Manage Messages in this channel or category
```

---

### HIGH PRIORITY — Setup UX must stay beginner-safe

Every setup screen should explain:

- what this does
- current saved value
- recommended value
- risk if wrong
- what button to press next

---

### HIGH PRIORITY — Persistent views and command surfaces must be audited

No duplicate commands, stale commands, hidden legacy setup paths, or expired public panels should remain in the normal user path.

---

## Premium readiness requirements

Premium features must not ship until this exists:

- plan definitions
- entitlement lookup
- feature gates
- locked-feature messages
- downgrade-safe behavior
- data retention rules after downgrade
- staff/admin override rules
- tests for free, premium, expired, and downgraded states

Free tier must keep basic safety usable. Premium should enhance scale, analytics, automation, branding, forms, transcripts, and advanced protection — not lock essential server safety behind a confusing paywall.

---

## Required test matrix

### Automated checks

```bash
python -m compileall stoney_verify
pytest
pytest tests/test_startup_health.py
pytest tests/test_guild_context.py
pytest tests/test_multi_guild_isolation.py
pytest tests/test_interaction_guard.py
pytest tests/test_ticket_counter_concurrency.py
pytest tests/test_ticket_creation_idempotency.py
pytest tests/test_invite_shield_scan.py
pytest tests/test_permission_model.py
pytest tests/test_premium_gates.py
```

### Manual public-server QA

```text
Fresh server:
1. Invite bot without Administrator.
2. Run /dank setup.
3. Create missing defaults.
4. Run health check.
5. Post ticket panel.
6. Open, claim, transfer, close, reopen, transcript, delete a ticket.
7. Enable Invite Shield only.
8. Confirm internal invite behavior.
9. Confirm external invite behavior.
10. Restart bot.
11. Confirm panels and setup still work.

Existing server:
1. Map existing roles/channels/categories.
2. Run health check.
3. Confirm no old channels/roles/tickets were deleted.
4. Confirm ticket numbering does not reset.
5. Confirm staff actions show staff names, not raw IDs.
6. Confirm missing permissions are exact and actionable.
```

---

## Implementation roadmap

### Commit 1 — Production command center and startup diagnostics

Status: `IN PROGRESS`

Scope:

- add this command center file
- add startup health reporting foundation
- do not change runtime behavior yet

Verification:

- docs render in GitHub
- diagnostics helper imports without booting the Discord client
- compileall passes

---

### Commit 2 — Central GuildContext / config resolver

Status: `NOT STARTED`

Scope:

- create central resolver for guild roles/channels/categories/settings
- mark env/global reads as legacy fallback only
- update one low-risk subsystem first

---

### Commit 3 — Interaction guard foundation

Status: `NOT STARTED`

Scope:

- central defer/followup/error helper
- first migrate setup/protection buttons with highest failure reports

---

### Commit 4 — Ticket atomicity and orphan safety

Status: `NOT STARTED`

Scope:

- database atomic ticket number reservation
- idempotent ticket creation operation records
- repair/report partial channel creation failures

---

### Commit 5 — Native Invite Shield flow and scan

Status: `NOT STARTED`

Scope:

- native Protection Center controls
- dry-run scan
- internal invite allowance
- deletion result accounting

---

### Commit 6 — Permissions diagnostics

Status: `NOT STARTED`

Scope:

- central permission requirements map
- exact owner-facing missing permission messages
- no Administrator requirement for normal operation

---

### Commit 7 — Premium entitlement skeleton

Status: `NOT STARTED`

Scope:

- plan definitions
- feature gates
- entitlement provider abstraction
- locked-feature messaging
- downgrade tests

---

### Commit 8 — Public production QA suite

Status: `NOT STARTED`

Scope:

- automated regression tests
- manual QA checklist
- release gate script/documentation

---

## Final production gate

Dank Shield is not public-production-ready until every item below is true:

- no critical startup failures are hidden
- fresh server setup passes from zero config
- existing server setup preserves data
- ticket lifecycle passes end-to-end
- ticket numbers do not duplicate or reset unexpectedly
- VC verification staff actions show correct staff identity
- Invite Shield blocks external invites and allows configured internal invites
- manual invite scan has dry-run confirmation
- no normal button path shows generic `interaction failed`
- permissions diagnostics are exact and actionable
- multi-guild isolation tests pass
- restart restores persistent views
- premium gates exist before premium upsells exist
- downgrade preserves data safely
- logs explain failures in plain English
- normal operation does not require Administrator

---

## Update protocol

When continuing the project, update this file before or alongside the code change:

```text
Current phase:
Current commit:
Files touched:
Risk:
Verification:
Result:
Remaining work:
```

If a new bug is reported, place it under the correct label first. Then decide whether it belongs in the current phase or must wait.
