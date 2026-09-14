# CLAUDE.md — Dank Shield change-control & architecture guardrails

Dank Shield is a public, multi-server Discord bot (discord.py) deployed on
Discloud. It does verification, tickets, moderation, and guided `/dank setup`.

This file exists because, for months, fixes kept moving the bot forward and
backward: unrelated systems got touched, startup guards and emergency patches
piled up, and tests locked *code shape* instead of *behavior*. The rules below
keep changes safe and controlled. **Read this before editing.**

---

## 1. How the bot actually boots (read this first)

The real runtime path is narrower than the file tree suggests:

```
Discloud runs main.py
  → main.py imports a SMALL fixed set of startup guards explicitly
  → main.py calls stoney_verify.app.run()
  → app.py imports core modules IN A DELIBERATE ORDER (commands before events)
  → commands.py registers slash commands AT IMPORT TIME
  → bot.run(DISCORD_TOKEN) → on_ready → API/workers/maintenance
```

Critical, non-obvious facts (verified — do not assume otherwise):

- **There is no executable bulk startup-guard loader.** The old
  `load_all_startup_guards()` / `load_startup_guards()` mechanism was formally
  retired after the runtime-ownership audit. `startup_guards/__init__.py` keeps
  an inert historical inventory while older audits are migrated. The inventory
  originally contained 76 names; retired files are removed as their ownership
  migrations complete. Nothing iterates that list during normal boot, and new
  code must not treat membership as runtime activation.
- **The guards that actually run** are the few imported explicitly by `main.py`
  (`discord_api_safety`, `command_safety`, `command_scope_dedupe`,
  `public_server_env_id_guard`, `guild_config_runtime_validator`,
  `interaction_action_lock_guard`), the verified Basic Verify compatibility
  imports from `sitecustomize.py`, their verified transitive imports, and
  guards/helpers deliberately imported by canonical feature modules. The old
  `runtime_safety` and `public_startup_scope` import hooks are retired; do not
  restore them. See `docs/STARTUP_GUARD_RUNTIME_OWNERSHIP_AUDIT.md` and
  `docs/RUNTIME_SAFETY_NATIVE_OWNERSHIP_AUDIT.md` before changing ownership.
- **Importing the `startup_guards` package currently imports `process_health`.**
  That package-level process/import/signal safety is a real live owner and was
  deliberately preserved. Moving it requires its own boot-order-sensitive
  migration; do not confuse other guard retirement with removal of this side
  effect.
- **Slash commands register as an import side effect** (`commands.py` calls
  `register_all_commands(bot, bot.tree)` at module top level). Discord's global
  command cap is 100; the live public surface is ~9 today. Adding a command can
  silently push another out — see `command_safety`.
- **`sitecustomize.py` and `usercustomize.py` auto-run before `main.py`.** Their
  remaining behavior is compatibility-scoped. Do not add a fallback startup
  loader, application runtime patcher, or another compatibility installer there.
- **Channel Builder routes are directly wired.** `app.py` starts
  `api_new.server.start_api(bot)`, `server.py` imports
  `register_channel_builder_routes`, and `start_api()` registers those routes
  directly. The old `channel_builder_api_guard` bridge is removed and must stay
  removed.

If you change boot order, guard imports, or registration, you are touching the
most load-bearing code in the repo. Re-read section 4.

---

## 2. Change-control rules

- **One subsystem per change.** Do not edit unrelated modules "while you're in
  there." Most regressions came from exactly this.
- **No new files in `startup_guards/`, no new root `runtime_*_patch.py`, no new
  `sitecustomize`/`usercustomize` logic** without explicit owner approval. New
  behavior belongs in the module that owns it, not a guard.
- **No new monkey-patches** of discord.py or the command tree
  (`setattr` on `discord.*` classes, `CommandTree.add_command/sync`, etc.).
  If a patch seems necessary, stop and ask.
- **Prefer deleting a superseded patch over adding another layer.**
- **Do not restore a bulk startup-guard loader.** If dormant behavior is proven
  necessary, migrate only that behavior into its canonical owner with behavioral
  coverage. Never iterate the historical guard inventory.
- No new `*_new` parallel module trees. Finish or delete; do not fork a third copy.
- No `from .globals import *` in new code — import explicit names.

---

## 3. Testing rules (CI now enforces these)

- CI (`.github/workflows/ci.yml`) runs `compileall`, **`pytest tests/`**, the
  standalone `tools/test_*.py` scripts, and the public-safety audits. Keep all of
  them green. Do **not** weaken a check just to make it pass — fix the code, or
  fix the assertion's intent.
- **Write behavioral tests, not static text-shape tests.** Tests that read a
  source file and assert a string/symbol exists (`"X" in SOURCE`) are what drove
  the churn — they fail on every refactor without catching real bugs. Do not add
  new `*_static.py` source-shape tests. Import the code and assert behavior.
- A quick local smoke test (no Discord connection):
  ```
  DANK_STARTUP_LOG_STYLE=quiet python -c "import stoney_verify.app"
  python -m pytest tests/ -q
  ```

---

## 4. Do not touch without explicit approval

These are load-bearing or dangerous to change blind:

1. `main.py` — entry point and guard import order.
2. `sitecustomize.py` / `usercustomize.py` — host-level auto-run compatibility hooks.
3. `stoney_verify/globals.py` — the shared `bot` singleton, env config, Supabase client, import-time invite listener (wildcard-exported; ripples everywhere).
4. `stoney_verify/app.py` import sequence & `on_ready`.
5. `stoney_verify/commands.py` (esp. the import-time `register_all_commands`) and `commands_ext/__init__.py` (registration pipeline + 100-command budget).
6. `startup_guards/__init__.py` historical inventory/package side effects and the explicitly owned infra-safety guards in section 1.
7. `stoney_verify/guild_config.py` — per-server config resolution (source of past isolation bugs).
8. Supabase client lifecycle (`get_supabase`/`reset_supabase`) and `supabase/migrations/`.
9. `bot.tree.clear_commands` / `copy_global_to` and the dangerous-clear env flags — can wipe the live command surface for every server.

---

## 5. Config / multi-server

- DB config (`guild_configs` in Supabase) is authoritative; `.env` is fallback
  only. Never read deployment-level env role/channel/guild IDs in per-guild
  runtime paths. `public_server_env_id_guard` enforces this.
- One env-var prefix: `DANK_`. (The bot was renamed from "Stoney Verify"; do not
  reintroduce `STONEY_` / `/stoney` markers.)
- `.env.example` is the documented public-production configuration. Keep it in
  sync with what `tools/audit_public_command_friction.py` and
  `tools/audit_public_invite_permissions.py` require.

---

## 6. Known architectural debt (staged work, not drive-by fixes)

These are real and need dedicated, tested passes — flag them, don't blind-fix:

- **Live guard/monkey-patch ownership.** Bulk loading is retired. The temporary
  `runtime_safety` and `public_startup_scope` import hooks are also retired, with
  their one required behavior moved into the canonical identity-admin command.
  Remaining live patch debt includes `process_health`, command safety/tree
  wrappers, and selected feature-owned helpers. Migrate them into canonical
  owners one subsystem at a time; do not delete them merely because they live
  under `startup_guards/`.
- **Historical dormant guard inventory.** The inert historical record is retained
  for audit compatibility, not activation. Retired files are removed from the
  inventory as ownership migrations complete. Remove other dormant files only
  after proving import reachability, newer canonical ownership, and regression
  safety.
- **Channel Builder follow-up debt is not route wiring.** Its API routes are
  directly registered today. Remaining work, if any, is product/runtime cleanup
  and stale documentation/workflow path references, not reintroducing the old
  startup-guard bridge.
- **Dank Design subsystem** (`commands_ext/public_design_studio.py`) is the most
  churn-prone area. Its old static tests were deleted (they pointed at a
  deprecated shim). Rebuild coverage behaviorally before reworking it.
- **Dual implementations** (`*` vs `*_new` trees) and dead trees (`commands_new`,
  `db_new`, `tasks_new`, `core/` have no importers). Consolidate, don't fork.
