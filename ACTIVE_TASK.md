# ACTIVE TASK

## DS-AUD-COMMAND-OWNERSHIP — CLOSED

**Outcome:** Menu-first command ownership is now native to the shared Dank Shield bot/tree. The process-wide command/bot startup monkey patches are retired from production ownership.

**Implementation PR:** #227 — `Move command ownership into the native bot tree`
**Validated PR head:** `4c36b7fa0f59fcc04bb4417a1568119dd8c05803`
**Implementation merge SHA:** `f17ad5d2af0809a9738eb6bbcbf4c295c693ba10`

Do not reopen this finding without new regression evidence.

## Product contract preserved

The public application-command surface remains intentionally small and menu-first:

- `/dank`
- `/mod`
- `/ticket`
- `/tickets`
- `/verify`
- `View Dank Profile` context menu

Direct `/dank` children remain exactly `home`, `purge`, `setup`, and `upload`. Normal feature discovery/configuration stays behind the existing Home control-center UI.

## Root cause fixed

Before #227, legitimate command runtime policy was spread across startup guards that mutated discord.py process-wide:

- `command_safety` replaced `CommandTree.add_command`, swallowed `CommandLimitReached`, and wrapped `CommandTree.sync`;
- `global_command_sync` wrapped `CommandTree.sync` again;
- `auto_shard` could replace `discord.ext.commands.Bot` globally;
- `command_scope_dedupe` mutated beta-sync defaults and attached another ready-time cleanup listener.

That architecture could report a healthy boot after silently dropping a user-facing command and made command behavior depend on import-order patch stacking.

## Canonical native owner after #227

`stoney_verify.command_runtime` now owns command policy for the one shared Dank Shield bot/tree instead of modifying framework classes globally.

- `globals.py` remains the one bot construction site and calls `create_discord_bot(...)`.
- `DankBot` / `DankAutoShardedBot` select native sharding from `DISCORD_AUTO_SHARD` and optional `DISCORD_SHARD_COUNT`.
- `DankCommandTree` owns global sync budget enforcement, fail-closed public-surface validation, unchanged-sync state, and configured stale guild-copy cleanup.
- the bot setup hook validates the required six-command/menu-first surface before normal ready handling;
- documented beta-sync and unchanged-sync configuration is live in the canonical runtime rather than stranded in a dormant patch;
- `app.py` keeps its normal `bot.tree.sync()` call sites because policy is instance-owned by `DankCommandTree`.

Production `main.py` no longer imports the migrated command guards. The old `command_safety`, `auto_shard`, `global_command_sync`, and `command_scope_dedupe` modules are inert compatibility shims and do not mutate discord.py.

## Validation evidence

### Exact-head premerge

Frozen head `4c36b7fa0f59fcc04bb4417a1568119dd8c05803` passed all applicable workflows:

- Dank Shield CI #2128 / `34901448124` — SUCCESS; full unit suite, standalone tools, public setup/isolation, canonical command surface, command friction, invite permissions, setup safety, Dank Design, role truth, event boundary, Claim-first security, and Managed category SQL all passed.
- Profile Runtime Diagnostics #894 / `34901449321` — SUCCESS.
- Ticket Owner Emergency Override #699 / `34901448198` — SUCCESS.
- Ticket Category Menu Sanity #525 / `34901448484` — SUCCESS.
- Schema Authority SQL #52 / `34901448253` — SUCCESS.
- Dank Design Regression CI #388 / `34901448477` — SUCCESS.
- Application Command Size Diagnostics #1138 / `34901448236` — SUCCESS.

PR #227 was mergeable, had exactly the intended 15-file scope, had zero review threads/reviews, and canonical `main` remained `fa585c111caf2386c71380e5de9aa44694e6ece4` before merge.

One earlier exact-head run exposed only an invalid test teardown: closing an `AutoShardedBot` instance that had never been started. Production code was unchanged; the test teardown was corrected and the final frozen head above passed.

### Postmerge production acceptance

PR #227 merged with the expected-head lock as `f17ad5d2af0809a9738eb6bbcbf4c295c693ba10`.

Acceptance on that exact canonical SHA passed:

- Dank Shield CI #2129 / `34907624864` — SUCCESS; completed `2026-09-14T23:22:03Z`, including the full unit suite and every standard audit step.
- Ticket Owner Emergency Override #700 / `34907624838` — SUCCESS.
- Ticket Category Menu Sanity #526 / `34907624767` — SUCCESS.
- Schema Authority SQL #53 / `34907624720` — SUCCESS.
- Deploy Supabase migrations #30 / `34908531392` — SUCCESS on the same SHA; started `2026-09-14T23:22:05Z`, after canonical CI completed, and completed `2026-09-14T23:22:23Z`.
- deployment checkout of the validated release commit, immutable current-main verification, required-secret validation, Supabase CLI install, production link, migration status, preview, and apply all passed.
- canonical `main` remained exactly `f17ad5d2af0809a9738eb6bbcbf4c295c693ba10` after promotion.

## Remaining master audit queue

### 1. Interaction/UI action-lock ownership — NEXT

Current repository evidence bounds this finding tightly:

- `startup_guards.interaction_action_lock_guard` is a direct live `main.py` owner and patches private `discord.ui.View._scheduled_task` globally.
- its normal mode defaults to `observe`; blocking requires an explicit block mode plus matching block targets.
- repository configuration does not advertise or configure those block-target settings.
- its duplicate/cooldown counters have no consumer outside the guard and the static source-shape test.
- canonical `stoney_verify.interaction_guard` already provides explicit `asyncio.Lock` action ownership, duplicate rejection, user-visible busy responses, safe defer/send handling, and structured failure diagnostics.
- live feature owners already call `run_guarded_interaction(...)`, including ticket panel, design, protection, diagnostics, setup/help, and related public UI paths.
- `tests/test_interaction_guard.py` already behaviorally proves duplicate in-flight rejection.
- the native `_ACTION_LOCKS` mapping currently retains completed lock objects; because duplicate callers are rejected rather than queued, safe finished-lock cleanup should be evaluated as part of this finding to prevent unbounded key retention.
- native ticket close/reopen/delete mutation locks in `transcripts.py` are protected behavior and must remain. Dormant ticket lock compatibility guards are not to be deleted without separate importer proof.

Target: retire the private `View._scheduled_task` production patch, preserve real native duplicate protection, replace source-shape assertions with behavioral ownership tests, and clean completed native lock entries if proven safe. Do not turn this into a rewrite of every Discord view.

### 2. Discord API / guild-config safety ownership

Migrate `discord_api_safety`, `public_server_env_id_guard`, and `guild_config_runtime_validator` behavior into canonical API/config owners without losing real retry/backoff, public isolation, or config validation behavior.

### 3. Dormant startup-guard consolidation

Delete/consolidate historical guard families only after exact importer/supersession proof. Do not restore a bulk loader and do not mass-delete by filename pattern.

### 4. Parallel/dead implementation-tree audit

Protect confirmed live `events_new`, `tasks_new`, `setup_new`, `api_new`, `members_new`, `moderation_new`, `tickets_new`, and `verification_new` paths. Continue exact importer/behavior proof for unresolved parallel candidates such as `commands_new`, `db_new`, `core/`, and `utils_new` before deletion.

### 5. Feature-owned compatibility/helper cleanup + product correctness

Audit remaining feature-owned helpers individually. Known product correctness debt includes `stoney_verify/services/invite_cleanup_service.py`, where invite allowlisting still contains the TODO `allowed_codes: set[str] = set()` rather than loading configured allowlist truth.

### 6. Dank Design behavioral coverage / architecture cleanup

Strengthen behavior-level coverage before structural cleanup of the churn-prone Dank Design implementation. Do not restore brittle source-shape tests.

### 7. Stale architecture/runbook truth

Update stale documentation only after runtime ownership is proven. Old readiness docs and historical ownership maps must not override current connected-repository evidence.

## Separately suspended

`DS-SEC-044` hostile re-entry production acceptance remains separately suspended. Do not resume it without explicit authorization.

## Next step

Run exact-head validation on this bookkeeping-only closeout branch/PR. After its closeout merge and production acceptance, select **Interaction/UI action-lock ownership** as the next active master-audit finding and begin from that exact canonical main SHA.
