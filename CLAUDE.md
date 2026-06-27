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

- **The startup-guard loader is NOT called at boot.** `load_all_startup_guards()`
  / `load_startup_guards()` has no live call site. The ~100 modules listed in
  `stoney_verify/startup_guards/__init__.py::_STARTUP_GUARDS` are **dormant**
  unless something imports them directly. Do not assume a guard runs just
  because it is in that list. `main.py`'s "minimal guards" comment is the real
  contract.
- **The guards that actually run** are the few imported explicitly by `main.py`
  (`discord_api_safety`, `command_safety`, `command_scope_dedupe`,
  `public_server_env_id_guard`, `guild_config_runtime_validator`,
  `interaction_action_lock_guard`), the ones imported by `sitecustomize.py` /
  `usercustomize.py` (host auto-import), and anything imported transitively by
  the `app.py` module chain.
- **Slash commands register as an import side effect** (`commands.py` calls
  `register_all_commands(bot, bot.tree)` at module top level). Discord's global
  command cap is 100; the live public surface is ~9 today. Adding a command can
  silently push another out — see `command_safety`.
- **`sitecustomize.py` and `usercustomize.py` auto-run before `main.py`** and
  mutate the command registry. Keep them consistent with each other.

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
- **Do not re-activate the guard loader** (`load_all_startup_guards`) without a
  deliberate, staged review — it would suddenly run ~100 dormant monkey-patches.
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
2. `sitecustomize.py` / `usercustomize.py` — host-level auto-run hooks that mutate the command registry.
3. `stoney_verify/globals.py` — the shared `bot` singleton, env config, Supabase client, import-time invite listener (wildcard-exported; ripples everywhere).
4. `stoney_verify/app.py` import sequence & `on_ready`.
5. `stoney_verify/commands.py` (esp. the import-time `register_all_commands`) and `commands_ext/__init__.py` (registration pipeline + 100-command budget).
6. `startup_guards/__init__.py` loader list and the infra-safety guards in section 1.
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

- **Dead guard loader.** `_STARTUP_GUARDS` + `load_all_startup_guards` are
  effectively dead. Decide: formally retire the loader, or deliberately re-wire a
  vetted subset. Do not flip it on casually.
- **Channel Builder dashboard API is unwired in production.** Its routes register
  only via `channel_builder_api_guard`, which lives in the dead loader and is
  imported by no live path; `server.py` is not directly patched. The
  `channel-builder-*` workflows reflect this. Wiring it is a deliberate task.
- **Dank Design subsystem** (`commands_ext/public_design_studio.py`) is the most
  churn-prone area. Its old static tests were deleted (they pointed at a
  deprecated shim). Rebuild coverage behaviorally before reworking it.
- **Dual implementations** (`*` vs `*_new` trees) and dead trees (`commands_new`,
  `db_new`, `tasks_new`, `core/` have no importers). Consolidate, don't fork.
