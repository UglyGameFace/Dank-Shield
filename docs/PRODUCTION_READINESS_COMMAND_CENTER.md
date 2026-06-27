# Dank Shield Production Readiness Command Center

Last updated: **2026-06-27**

This file is the source of truth for turning Dank Shield into a public, multi-server, premium-ready Discord bot without losing direction. Every bug report, screenshot, PR, feature idea, audit finding, and code change must map back to this command center before runtime behavior is changed.

If a future assistant, agent, or developer loses context, this file is the reset point.

---

## Mission

Make Dank Shield public-production-ready for real server owners, staff teams, and future premium customers.

The bot must be:

- safe for many unrelated Discord servers
- simple enough for non-technical owners
- clear enough for staff and visually impaired users
- reliable under Discord, API, database, restart, and permission failures
- resistant to duplicate commands, stale UI, silent errors, stale panels, and hidden boot mutations
- structured around native services, not startup patch piles
- ready for premium gating before premium features are sold

---

## Non-negotiable rules

1. **No monkey patches.** Valid emergency behavior must be migrated into native modules or removed.
2. **No broad startup guards that secretly rewrite runtime behavior.** Startup may validate and report; product behavior belongs in native services.
3. **No isolated fixes that bypass centralized systems.**
4. **No feature work before the current blocker class is controlled.**
5. **No hidden errors, swallowed exceptions, or generic `interaction failed` paths.**
6. **No public-server behavior that relies on one private server's environment IDs.**
7. **No paid/premium release until entitlement gates, downgrade behavior, and QA exist.**
8. **Every fix must include verification.**
9. **Existing ticket data, guild setup, roles, logs, and workflows must be preserved.**
10. **No direct invite/link deletion outside the central invite policy engine.**
11. **No setting-specific one-off handlers when a central registry/service should own the behavior.**
12. **No direct command-surface mutation in public runtime unless it is part of a deliberate migration tool.**

---

## Required work loop

Every serious change must follow this loop:

```text
1. Read this command center.
2. State the active task ID.
3. Inspect only files needed for that task.
4. Report the exact root cause.
5. Fix the root cause only.
6. If a new blocker appears, pause and promote it.
7. Add or update tests.
8. Verify.
9. Update this command center.
10. State the next single task.
```

No wandering. No drive-by feature work. No vague “fixed” claims.

---

## Interruption rule

If a higher-risk issue is discovered while fixing another issue, stop and record it like this:

```text
INTERRUPTION DETECTED
Original task:
New blocker:
Why it outranks the original task:
What would break if ignored:
Files inspected:
Action taken:
Verification:
Return path:
```

The interruption becomes the active task only if it could cause generic interaction failure, data loss, cross-guild config bleed, unsafe moderation/deletion, command registration breakage, ticket numbering/channel orphaning, silent boot/runtime failure, or removal of an active feature.

---

## Definition of done

A task is not done unless every applicable item is true:

```text
Root cause found: yes
Patch avoided: yes
Central system used: yes
Guild-scoped: yes
Interaction failure handled: yes
Error logged with context: yes
User-facing fix message: yes
Tests added/updated: yes
Verification command run: yes
Remaining risk listed: yes
Command center updated: yes
```

If any item is missing, the task is partial, not done.

---

## Status labels

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
| `PARTIAL` | Some valid work exists, but production criteria are not met. |
| `DEFERRED` | Intentionally delayed with a reason. |

---

## Current phase

**Phase 1 — Control the chaos**

Goal: stop generic interaction failures, stop hidden runtime mutation, centralize settings/decisions, and replace startup patch behavior with native services.

Current priority order:

1. `P0-INT-001` — Replace monkey-patched interaction logger with native interaction service.
2. `P0-GUARD-001` — Startup guard inventory and migration table.
3. `P0-INVITE-001` — Verify every invite/link delete goes through `invite_policy_engine`.
4. `P0-CMD-001` — Remove public runtime command-surface mutation/pruning from normal operation.
5. `P0-SETTINGS-001` — Create central settings registry/service.
6. `P0-DESIGN-001` — Split Dank Design into service/state/UI/logging layers.
7. `P0-TICKET-001` — Ticket atomicity, numbering continuity, and orphan-channel protection.
8. `P0-CONFIG-001` — Finish GuildContext/config resolver migration across subsystems.
9. `P1-PERMS-001` — Central permission diagnostics.
10. `P1-PREMIUM-001` — Premium entitlement skeleton.
11. `P1-QA-001` — Public production QA suite.

---

## Production readiness score

Current working score: **49 / 100**

Meaning: internal/testing only. Not ready for wide public release, not ready for paid premium membership, and not ready to promise seamless UX.

Score moved from 48 to 49 because the native interaction foundation now exists and Protection Center callbacks have started migrating to it. It cannot rise further until real test/compile commands are run and the old framework monkey patch is removed safely.

Remaining score blockers:

- a large startup guard chain still controls too much behavior
- `global_interaction_trace_guard` still patches Discord.py framework internals
- not all setup/design/ticket/verify callbacks use the native interaction guard yet
- command registry still contains runtime pruning/mutation logic
- settings are not yet defined through one central registry
- Dank Design still contains too much state/UI/service logic in one command module

---

## Current active task

### `P0-INT-001` — Replace monkey-patched interaction logger with native interaction service

Status: `PARTIAL / BLOCKER`

Goal:

Stop generic `interaction failed` outcomes without patching Discord.py internals.

Current evidence:

- `stoney_verify/startup_guards/global_interaction_trace_guard.py` patches `app_commands.CommandTree._call`.
- It also patches app command invocation methods.
- It also patches `discord.ui.View._scheduled_task`.
- The logger captures useful fields, but the implementation is still a monkey patch.

Progress completed:

- `stoney_verify/interaction_guard.py` now has native structured context capture.
- It creates `DANK-xxxxxxxx` error IDs without Discord.py private method replacement.
- It records guild/channel/user/message/custom_id/component/command context.
- It logs defer failures, send failures, callback exceptions, and duplicate action clicks.
- It keeps a bounded recent-failure ring for diagnostics/tests.
- It has native duplicate-action lock support.
- `tests/test_interaction_guard.py` covers response/followup behavior, send failure logging, defer failure logging, safe callback errors, and duplicate locked actions.
- `stoney_verify/commands_ext/public_protection_center.py` now routes `/dank protection`, Protection Center buttons, spam editor select/actions, filter modals, refresh, and close through native guarded actions.
- Legacy local `try/print` handling was removed from the Protection Center open path.
- `tests/test_public_protection_center_native_interaction_static.py` now prevents Protection Center from regressing back to unguarded command-open handling and verifies required guarded action names exist.

Important behavior note:

- Slow Protection Center config writes now prefer deferred private followups over risky unacknowledged edit-in-place behavior. This is intentional for reliability. A later UX pass can improve in-place refresh once every path is safely acknowledged.

Remaining before this task can be marked done:

- migrate the highest-risk setup/design/ticket/verify callbacks to `run_guarded_interaction()` or native helpers
- ensure diagnostics can expose recent native interaction failures safely
- remove or disable `global_interaction_trace_guard` framework patching only after native coverage exists
- run the interaction/protection tests and compile checks in a real checkout

Exit criteria:

- no production startup guard patches `CommandTree`, app command internals, or `View._scheduled_task`
- setup/protection/design/ticket/verify callbacks can use the native helper directly
- failing callbacks log structured context and show a useful user message
- tests cover response done/not done, followup fallback, duplicate click, stale component, and exception paths

---

## Current blockers

### `P0-GUARD-001` — Startup architecture is too patch/guard-heavy

Status: `BLOCKER`

The bot currently depends on a large startup guard chain. This increases risk that features appear fixed in one path while breaking silently in another.

Confirmed risk areas:

- interaction framework patching
- invite enforcement guards overlapping the central invite engine
- protection center guards mutating command/UI behavior
- setup UX guards patching flows at boot
- design guards patching Dank Design behavior instead of native design modules
- ticket/VC guards patching product behavior instead of native services
- `builtins.print` suppression during startup guard loading

Safe strategy:

- inventory every startup guard
- classify each as `VALIDATION ONLY`, `MIGRATE`, `DELETE DUPLICATE`, `DELETE OBSOLETE`, or `BLOCKED`
- migrate valid behavior into native service/module owners
- remove duplicate/obsolete guards only after tests prove behavior remains covered

Verification:

- startup report shows expected, loaded, failed, and missing modules
- failed imports are visible in logs/diagnostics
- no startup guard mutates Discord.py internals in production
- bot can still boot after each migrated guard is removed

---

### `P0-CONFIG-001` — Runtime config must be truly per-guild everywhere

Status: `PARTIAL / BLOCKER`

The central `guild_config.py` is moving in the right direction. It has guild-scoped cache keys and public config isolation. That is good but not complete.

Remaining concern:

Old compatibility globals, startup guards, and split modules may still use fallback env IDs, module globals, or stale cache state outside the central resolver.

Safe strategy:

- finish central GuildContext/config resolver
- audit all role/channel/category/settings reads
- migrate runtime reads to resolver one subsystem at a time
- keep env IDs only as controlled legacy/private fallback when explicitly allowed

Verification:

- two test guilds can use different roles/channels/categories
- missing setup locks unsafe actions instead of guessing
- diagnostics show exactly what is missing for each guild
- cache keys include guild ID for all guild behavior

---

### `P0-INVITE-001` — Invite/link deletion must be exclusively centralized

Status: `PARTIAL / BLOCKER`

Good news:

`stoney_verify/invite_policy_engine.py` already has a strong central decision object and correct policy posture:

- same-server invites allowed by default
- Spam Guard alone does not delete a single invite
- decisions include feature owner, rule ID, reason, fix hint, guild ID, channel ID, author ID, classification, and delete result

Remaining risk:

Startup guards and legacy listeners may still contain invite/link delete behavior or overlapping enforcement.

Safe strategy:

- search all direct `message.delete()` paths
- identify every invite/link scanner/listener/runtime cleanup path
- require all invite deletes to call `decide_invite_message()` and `delete_message_if_allowed()`
- remove duplicate invite enforcement guards after coverage exists

Verification:

- Invite Shield can enable independently from Link Shield
- internal invites are allowed when configured
- external invites are blocked/deleted predictably
- manual scan reports checked, matched, allowed, skipped, deleted, and failed counts
- no invite/link message is deleted without a stored `InviteDecision`

---

### `P0-CMD-001` — Public command surface must not mutate at runtime

Status: `BLOCKER`

The command registry is partially centralized, but it still includes runtime pruning/removal logic for stale top-level commands and confusing `/dank` children.

Safe strategy:

- make public command surface deterministic from the command registry
- move command cleanup to explicit dev/admin migration tooling only
- remove public-runtime command pruning from normal startup
- add tests for public command children and top-level commands

Verification:

- `/dank` public children are deterministic
- no hidden legacy command appears in public profile
- no normal startup path removes commands after registration
- tests cover public, public-admin, dev, and minimal command profiles

---

### `P0-SETTINGS-001` — Central settings registry is missing

Status: `BLOCKER`

Settings are currently spread across guild config, spam settings, automod presets, invite policy keys, setup helpers, and feature-specific UI code.

Safe strategy:

Create a central settings registry with key, display name, plain-English description, owning feature, default, valid values, storage location, conflict rules, migration aliases, visibility rules, audit log behavior, and premium gating metadata when needed.

Verification:

- Protection Center reads setting definitions from registry
- Setup screens can show current/recommended/missing values from registry
- Invite Shield conflict warnings use registry conflict rules
- tests catch duplicate keys, unknown aliases, invalid defaults, and missing descriptions

---

### `P0-DESIGN-001` — Dank Design must be split out of giant command-module behavior

Status: `BLOCKER / UX`

Good news:

Dank Design now has native registration and visible cleanup for newline artifacts like `\\n`, `\n`, `/n`, and `/N`.

Remaining concern:

`public_design_studio.py` still owns pending state, snapshots, locks, rollback persistence, format editor drafts, UI views, permission checks, and save/load behavior.

Safe strategy:

Split into design service, state store, rollback service, UI views, logger/audit service, and permission helper.

Verification:

- Dank Design works on any guild
- no `/n`, `\n`, or `\\n` artifacts appear in embeds
- channel editor groups by category
- exact format editor includes font, separator, emoji, frame, preview, and apply path
- bot access/fix button only appears when actually needed

---

### `P0-TICKET-001` — Ticket creation needs DB-atomic numbering and orphan protection

Status: `BLOCKER`

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
- imported/highest previous ticket numbers do not reset unexpectedly

---

## High-priority work

### `P1-PERMS-001` — Permissions diagnostics

Status: `HIGH PRIORITY`

Owners should see the exact missing permission and where it is missing.

Required output format:

```text
Missing: Manage Messages
Where: #general
Needed for: deleting blocked external invite links
Fix: give the Dank Shield role Manage Messages in this channel or category
```

---

### `P1-SETUP-UX-001` — Setup UX must stay beginner-safe

Status: `HIGH PRIORITY / UX`

Every setup screen should explain what this does, current saved value, recommended value, risk if wrong, what button to press next, what conflicts with this setting, and how to undo or repair it.

---

### `P1-PERSISTENT-VIEWS-001` — Persistent views and public panels must be audited

Status: `HIGH PRIORITY`

No duplicate commands, stale commands, hidden legacy setup paths, or expired public panels should remain in the normal user path.

---

## Premium readiness requirements

Status: `HIGH PRIORITY / PREMIUM`

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

## Startup guard migration ledger

Every startup guard must be classified before removal.

| Area / example | Current status | Required action |
| --- | --- | --- |
| `global_interaction_trace_guard` | useful logic, invalid monkey-patch implementation | migrate to native interaction service |
| `interaction_action_lock_guard` | likely valid product rule | migrate to central interaction lock/idempotency service |
| `command_safety` | likely valid validation | keep only as validation, no runtime mutation |
| `slash_command_cleanup` | dangerous in production if mutating commands | move to explicit dev/admin migration tool or delete |
| `protection_center_command_guard` | mutates command surface to hide aliases | migrate into deterministic command registry |
| `protection_import_button_patch` | patch file by name | inspect, migrate valid behavior, delete patch |
| `spam_guard_invite_hard_block` | overlap risk with invite policy engine | migrate/delete after invite policy verification |
| `discord_invite_blocker_runtime_guard` | overlap risk with invite policy engine | inspect for direct deletes |
| `invite_live_enforcer_guard` | loader explicitly calls `apply()` | high-risk inspection required |
| `server_design_*_guard` | design behavior should be native | migrate into design service/UI |
| `setup_*_guard` | too many setup UX fixes at startup | classify and migrate valid UX into setup modules |
| `ticket_*_guard` | ticket behavior should be native | migrate into ticket services |
| `vc_*_guard` | VC verify should be native | migrate into verification service |
| `job_dedupe` | valid reliability concept | keep as native scheduler/job service, not broad patch |

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
pytest tests/test_public_protection_center_native_interaction_static.py
pytest tests/test_ticket_counter_concurrency.py
pytest tests/test_ticket_creation_idempotency.py
pytest tests/test_invite_policy_engine.py
pytest tests/test_invite_shield_scan.py
pytest tests/test_permission_model.py
pytest tests/test_public_command_surface.py
pytest tests/test_server_design_full_user_workflow_audit.py
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
10. Run invite scan dry-run.
11. Confirm deletion count only reports actual successful deletes.
12. Restart bot.
13. Confirm panels and setup still work.

Existing server:
1. Map existing roles/channels/categories.
2. Run health check.
3. Confirm no old channels/roles/tickets were deleted.
4. Confirm ticket numbering does not reset.
5. Confirm staff actions show staff names, not raw IDs.
6. Confirm missing permissions are exact and actionable.
7. Confirm Dank Design previews before applying changes.
```

---

## Implementation roadmap

### Commit 1 — Production command center and current audit refresh

Status: `DONE`

Scope:

- update this command center to reflect current audit findings
- lower readiness score from 62 to 48
- define active task IDs
- add interruption rule
- add strict definition of done
- update startup guard migration ledger

Verification:

- docs render in GitHub
- no runtime behavior changed

---

### Commit 2 — Native interaction service

Status: `IN PROGRESS / PARTIAL`

Task ID: `P0-INT-001`

Scope:

- create central interaction service/helper
- preserve useful error IDs and structured context
- migrate highest-risk setup/protection/design buttons first
- remove Discord.py private method patching only after native coverage exists

Progress:

- native `interaction_guard.py` now captures structured context and error IDs
- native duplicate-action lock support exists
- native recent-failure ring exists for diagnostics/tests
- tests expanded for response, followup, send failure, defer failure, callback exception, and duplicate action paths
- Protection Center command/button/modal/select paths now use native guarded interaction wrappers
- Protection Center static regression test added

Verification:

- `tests/test_interaction_guard.py` updated
- `tests/test_public_protection_center_native_interaction_static.py` added
- static GitHub inspection completed
- compile/pytest still need to be run from a real checkout
- setup/design/ticket/verify callbacks are not fully migrated yet

---

### Commit 3 — Startup guard inventory and migration table

Status: `NOT STARTED`

Task ID: `P0-GUARD-001`

Scope:

- inventory every startup guard
- classify each guard
- identify native owner module
- migrate/delete safely in batches

---

### Commit 4 — Invite policy enforcement verification

Status: `NOT STARTED`

Task ID: `P0-INVITE-001`

Scope:

- verify every invite/link delete calls central invite policy engine
- remove duplicate invite hard-block paths
- add scan/deletion accounting tests

---

### Commit 5 — Deterministic public command surface

Status: `NOT STARTED`

Task ID: `P0-CMD-001`

Scope:

- remove normal public runtime command pruning/mutation
- keep cleanup as explicit migration/admin tooling only
- add command surface tests

---

### Commit 6 — Central settings registry

Status: `NOT STARTED`

Task ID: `P0-SETTINGS-001`

Scope:

- define setting registry model
- migrate Protection Center first
- add conflict and alias validation tests

---

### Commit 7 — Dank Design service split

Status: `NOT STARTED`

Task ID: `P0-DESIGN-001`

Scope:

- split design command module into service/state/UI/logging
- preserve previews, rollback, examples, and format locks
- remove design startup patch dependencies

---

### Commit 8 — Ticket atomicity and orphan safety

Status: `NOT STARTED`

Task ID: `P0-TICKET-001`

Scope:

- database atomic ticket number reservation
- idempotent ticket creation operation records
- repair/report partial channel creation failures

---

### Commit 9 — Permissions diagnostics

Status: `NOT STARTED`

Task ID: `P1-PERMS-001`

Scope:

- central permission requirements map
- exact owner-facing missing permission messages
- no Administrator requirement for normal operation

---

### Commit 10 — Premium entitlement skeleton

Status: `NOT STARTED`

Task ID: `P1-PREMIUM-001`

Scope:

- plan definitions
- feature gates
- entitlement provider abstraction
- locked-feature messaging
- downgrade tests

---

### Commit 11 — Public production QA suite

Status: `NOT STARTED`

Task ID: `P1-QA-001`

Scope:

- automated regression tests
- manual QA checklist
- release gate script/documentation

---

## Final production gate

Dank Shield is not public-production-ready until every item below is true:

- no critical startup failures are hidden
- no production monkey patches remain
- no broad startup guard rewrites runtime behavior
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
- every public setting has a clear description, current state, conflict warning, and fix path

---

## Update protocol

When continuing the project, update this file before or alongside the code change:

```text
Current phase:
Active task ID:
Original goal:
Files touched:
Risk:
Verification:
Result:
Remaining work:
Next single task:
```

If a new bug is reported, place it under the correct label first. Then decide whether it belongs in the current phase or must wait.

When the user says **continue**, do not freestyle. Read this file, take the current active task, work the loop, update the ledger, and stop at the next single task.
