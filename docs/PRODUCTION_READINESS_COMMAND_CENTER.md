# Dank Shield Production Readiness Command Center

Last updated: **2026-06-27**

This file is the source of truth for turning Dank Shield into a public, multi-server, premium-ready Discord bot without losing direction. Every bug report, screenshot, PR, feature idea, audit finding, and code change must map back here before runtime behavior is changed.

If a future assistant, agent, or developer loses context, this file is the reset point.

---

## Mission

Make Dank Shield public-production-ready for real server owners, staff teams, and future premium customers.

The bot must be safe for unrelated Discord servers, simple enough for non-technical owners, clear enough for staff and visually impaired users, reliable under Discord/API/database/restart/permission failures, resistant to duplicate commands/stale UI/silent errors/hidden boot mutations, structured around native services instead of startup patch piles, and ready for premium gating before premium features are sold.

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

If a higher-risk issue is discovered while fixing another issue, record:

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

The interruption becomes active only if it could cause generic interaction failure, data loss, cross-guild config bleed, unsafe moderation/deletion, command registration breakage, ticket numbering/channel orphaning, silent boot/runtime failure, or removal of an active feature.

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

## Current phase

**Phase 1 — Control the chaos**

Goal: stop generic interaction failures, stop hidden runtime mutation, centralize settings/decisions, and replace startup patch behavior with native services.

Priority order:

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

Score moved from 48 to 49 because the native interaction foundation exists, Protection Center callbacks started migrating to it, and `/dank design` command-open now uses a guarded native wrapper. It cannot rise further until real test/compile commands are run, raw setup/design/ticket/verify callbacks are migrated, and the old framework monkey patch is removed safely.

Remaining score blockers:

- large startup guard chain still controls too much behavior
- `global_interaction_trace_guard` still patches Discord.py framework internals
- many setup/design/ticket/verify callbacks still do raw `interaction.response.*` work
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

- `stoney_verify/interaction_guard.py` has native structured context capture.
- It creates `DANK-xxxxxxxx` error IDs without Discord.py private method replacement.
- It records guild/channel/user/message/custom_id/component/command context.
- It logs defer failures, send failures, callback exceptions, and duplicate action clicks.
- It keeps a bounded recent-failure ring for diagnostics/tests.
- It has native duplicate-action lock support.
- `tests/test_interaction_guard.py` covers response/followup behavior, send failure logging, defer failure logging, safe callback errors, and duplicate locked actions.
- `stoney_verify/commands_ext/public_protection_center.py` routes `/dank protection`, Protection Center buttons, spam editor select/actions, filter modals, refresh, and close through native guarded actions.
- Legacy local `try/print` handling was removed from the Protection Center open path.
- `tests/test_public_protection_center_native_interaction_static.py` prevents Protection Center from regressing back to unguarded command-open handling and verifies required guarded action names exist.
- `stoney_verify/commands_ext/public_design_group.py` registers `/dank design` through a native `run_guarded_interaction()` wrapper instead of delegating the slash command callback to the raw studio opener.
- `tests/test_public_design_group_native_interaction_static.py` verifies guarded `/dank design` registration and records the remaining raw callback debt in the large studio module.
- `patches/p0-int-design-exact-format-native-guard.patch` now contains the next intended exact-format editor migration for `_open_exact_format_editor`, exact layout examples, save-preview, server-style, emoji modal, and back actions.
- `tests/test_public_design_exact_native_guard_patch_static.py` verifies the exact-format patch artifact contains native guard helper targets and records that `public_design_studio.py` is still debt until the patch is applied.

Important behavior notes:

- Slow Protection Center config writes now prefer deferred private followups over risky unacknowledged edit-in-place behavior. This is intentional for reliability. A later UX pass can improve in-place refresh once every path is safely acknowledged.
- `/dank design` command-open is guarded now, but most internal Dank Design buttons/selects/modals still live in `public_design_studio.py` and need careful small-slice migration.
- Attempting the exact-format slice revealed a tooling constraint: the GitHub connector replaces large files as whole files. Because `public_design_studio.py` is over 5,300 lines, the exact editor runtime change was recorded as a controlled patch artifact instead of risking a corrupted full-file replacement from snippets.
- `public_design_enhancements.py` still activates enhancement code from `startup_guards`; this is recorded under `P0-GUARD-001` and should be removed during guard migration, not buried as a new interaction patch.

Remaining before this task can be marked done:

- apply `patches/p0-int-design-exact-format-native-guard.patch` in a real checkout, then run compile/tests
- migrate the highest-risk setup/design/ticket/verify callbacks to `run_guarded_interaction()` or native helpers
- migrate Dank Design internal buttons/selects/modals in smaller slices, starting with exact format editor and apply/rollback flows
- ensure diagnostics can expose recent native interaction failures safely
- remove or disable `global_interaction_trace_guard` framework patching only after native coverage exists
- run interaction/protection/design static tests and compile checks in a real checkout

Exit criteria:

- no production startup guard patches `CommandTree`, app command internals, or `View._scheduled_task`
- setup/protection/design/ticket/verify callbacks can use the native helper directly
- failing callbacks log structured context and show a useful user message
- tests cover response done/not done, followup fallback, duplicate click, stale component, and exception paths

---

## Current blockers

### `P0-GUARD-001` — Startup architecture is too patch/guard-heavy

Status: `BLOCKER`

Confirmed risk areas:

- interaction framework patching
- invite enforcement guards overlapping the central invite engine
- protection center guards mutating command/UI behavior
- setup UX guards patching flows at boot
- design enhancements still activated through startup guard modules from the native design registration path
- design guards patching Dank Design behavior instead of native design modules
- ticket/VC guards patching product behavior instead of native services
- `builtins.print` suppression during startup guard loading

Safe strategy:

- inventory every startup guard
- classify each as `VALIDATION ONLY`, `MIGRATE`, `DELETE DUPLICATE`, `DELETE OBSOLETE`, or `BLOCKED`
- migrate valid behavior into native service/module owners
- remove duplicate/obsolete guards only after tests prove behavior remains covered

---

### `P0-CONFIG-001` — Runtime config must be truly per-guild everywhere

Status: `PARTIAL / BLOCKER`

The central `guild_config.py` is moving in the right direction. It has guild-scoped cache keys and public config isolation. Remaining concern: old compatibility globals, startup guards, and split modules may still use fallback env IDs, module globals, or stale cache state outside the central resolver.

---

### `P0-INVITE-001` — Invite/link deletion must be exclusively centralized

Status: `PARTIAL / BLOCKER`

`stoney_verify/invite_policy_engine.py` already has a strong central decision object and correct policy posture. Remaining risk: startup guards and legacy listeners may still contain invite/link delete behavior or overlapping enforcement.

---

### `P0-CMD-001` — Public command surface must not mutate at runtime

Status: `BLOCKER`

The command registry is partially centralized, but it still includes runtime pruning/removal logic for stale top-level commands and confusing `/dank` children.

---

### `P0-SETTINGS-001` — Central settings registry is missing

Status: `BLOCKER`

Settings are currently spread across guild config, spam settings, automod presets, invite policy keys, setup helpers, and feature-specific UI code.

---

### `P0-DESIGN-001` — Dank Design must be split out of giant command-module behavior

Status: `BLOCKER / UX`

Good news: Dank Design has native registration and visible cleanup for newline artifacts like `\\n`, `\n`, `/n`, and `/N`.

Remaining concern: `public_design_studio.py` still owns pending state, snapshots, locks, rollback persistence, format editor drafts, UI views, permission checks, and save/load behavior.

---

### `P0-TICKET-001` — Ticket creation needs DB-atomic numbering and orphan protection

Status: `BLOCKER`

Ticket numbers and channel creation must stay consistent under retries, restarts, and concurrent clicks.

---

## Startup guard migration ledger

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
| `public_design_enhancements` startup-guard imports | native design path still activates startup guard modules | migrate strict/majority layout into native design services |
| `setup_*_guard` | too many setup UX fixes at startup | classify and migrate valid UX into setup modules |
| `ticket_*_guard` | ticket behavior should be native | migrate into ticket services |
| `vc_*_guard` | VC verify should be native | migrate into verification service |
| `job_dedupe` | valid reliability concept | keep as native scheduler/job service, not broad patch |

---

## Required test matrix

```bash
python -m compileall stoney_verify
pytest
pytest tests/test_interaction_guard.py
pytest tests/test_public_protection_center_native_interaction_static.py
pytest tests/test_public_design_group_native_interaction_static.py
pytest tests/test_public_design_exact_native_guard_patch_static.py
pytest tests/test_startup_health.py
pytest tests/test_guild_context.py
pytest tests/test_multi_guild_isolation.py
pytest tests/test_ticket_counter_concurrency.py
pytest tests/test_ticket_creation_idempotency.py
pytest tests/test_invite_policy_engine.py
pytest tests/test_invite_shield_scan.py
pytest tests/test_permission_model.py
pytest tests/test_public_command_surface.py
pytest tests/test_server_design_full_user_workflow_audit.py
pytest tests/test_premium_gates.py
```

---

## Implementation roadmap

### Commit 1 — Production command center and current audit refresh

Status: `DONE`

### Commit 2 — Native interaction service

Status: `IN PROGRESS / PARTIAL`

Progress:

- native `interaction_guard.py` captures structured context and error IDs
- native duplicate-action lock support exists
- native recent-failure ring exists for diagnostics/tests
- interaction guard tests expanded
- Protection Center command/button/modal/select paths now use native guarded interaction wrappers
- Protection Center static regression test added
- `/dank design` command-open now uses a native guarded wrapper from the public design registrar
- public design group static regression test added
- exact-format editor native guard patch artifact added for safe local application
- exact-format patch static test added

Verification:

- static GitHub inspection completed
- compile/pytest still need to be run from a real checkout
- setup/design internal/ticket/verify callbacks are not fully migrated yet

### Commit 3 — Startup guard inventory and migration table

Status: `NOT STARTED`

### Commit 4 — Invite policy enforcement verification

Status: `NOT STARTED`

### Commit 5 — Deterministic public command surface

Status: `NOT STARTED`

### Commit 6 — Central settings registry

Status: `NOT STARTED`

### Commit 7 — Dank Design service split

Status: `NOT STARTED`

### Commit 8 — Ticket atomicity and orphan safety

Status: `NOT STARTED`

### Commit 9 — Permissions diagnostics

Status: `NOT STARTED`

### Commit 10 — Premium entitlement skeleton

Status: `NOT STARTED`

### Commit 11 — Public production QA suite

Status: `NOT STARTED`

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

When the user says **continue**, do not freestyle. Read this file, take the current active task, work the loop, update the ledger, and stop at the next single task.
