# ACTIVE TASK

## Active task / desired outcome

**P0-INVITE-RUNTIME-001 — Retire legacy invite runtime/override compatibility chain**

Keep Invite Shield enforcement under one live owner and one recovery owner. Remove obsolete startup-guard listener/bridge/override modules that no longer own production behavior but could recreate duplicate invite enforcement or policy drift if imported.

## Status

**IMPLEMENTED — exact-head validation pending**

Branch: `audit/retire-legacy-invite-runtime-20260921`

Base: current `main` after PR #294.

## Previous task closed

**P0-PROTECTION-IMPORT-001** is complete.

PR #294 merged as:

`4799b7199586ca6c7754c4fd40cf35b13683ea8a`

Exact implementation head:

`edff3139d8c232fff68075ed78ca8bb6aa60dcf5`

Termux validation passed:

- diff integrity;
- Python compile;
- native Protection Import Pack regressions;
- Protection Center native interaction tests;
- protection/invite regressions;
- startup ownership tests;
- full suite: **1803 passed, 79 warnings, 0 failures**.

Post-merge verification confirmed:

- `protection_center_command_guard.py` absent;
- `protection_import_button_patch.py` absent;
- `protection_pack_manual_import_guard.py` absent;
- their startup metadata absent;
- native Import Pack button, modal, merge helper, and retirement regression present on `main`.

## Root cause / ownership finding

Five invite-era compatibility modules no longer own production behavior:

### `invite_live_enforcer_guard.py`

Contains a second `on_message` listener implementation, but has no production importer.

Canonical live ownership already exists in:

`stoney_verify.globals._dank_globals_live_invite_enforcer`

which calls:

`invite_policy_engine.enforce_live_invite_message(..., refresh_policy=True)`

### `discord_invite_blocker_runtime_guard.py`

Compatibility bridge only. It no longer installs a live listener and merely delegates to the central engine/reconciliation runtime.

Its only code caller is the legacy Spam Guard hard-block shim.

### `spam_guard_invite_hard_block.py`

Legacy compatibility facade. It has no independent production owner and forwards its delete entrypoint to `discord_invite_blocker_runtime_guard`.

### `spam_guard_invite_override_options.py`

Historical monkey-patch layer over Spam Guard settings/UI. It patches Spam Guard functions at import time.

Native bot/channel targeting now belongs to:

- `invite_scope_settings.py`;
- `commands_ext/public_protection_invite_ui.py`;
- `invite_policy_engine.py`.

The canonical invite policy engine does not use the obsolete `invite_override_*` patch keys.

### `protection_invite_target_precedence_guard.py`

Historical patch layered on the legacy hard-block/override modules. It has no production importer and attempts to patch helper ownership that no longer exists in the current hard-block facade.

## Canonical owners after retirement

### Live enforcement

`globals.py`
→ `invite_policy_engine.enforce_live_invite_message`
→ central decision/delete/modlog/stat handling.

### Missed-message / restart recovery

`main.py`
→ `invite_reconciliation_runtime.install_invite_reconciliation`
→ `invite_policy_engine.scan_channel_invites`.

### Invite targeting UI/state

`public_protection_invite_ui.py`
+ `invite_scope_settings.py`
→ canonical guild-scoped scope metadata consumed by `invite_policy_engine`.

## Scope

In scope:

- delete:
  - `startup_guards/invite_live_enforcer_guard.py`;
  - `startup_guards/discord_invite_blocker_runtime_guard.py`;
  - `startup_guards/spam_guard_invite_hard_block.py`;
  - `startup_guards/spam_guard_invite_override_options.py`;
  - `startup_guards/protection_invite_target_precedence_guard.py`;
- remove their inert startup inventory entries;
- update invite safety audit to assert canonical owners and retired-file absence;
- extend Invite Shield guard-retirement regression;
- update recovery regression so it no longer preserves the compatibility bridge;
- update invite/startup/readiness ownership documentation.

Out of scope:

- changing `invite_policy_engine` decisions;
- changing Invite Shield enable/disable semantics;
- changing same-server invite allowance;
- changing message-surface extraction;
- changing history scan limits/budgets;
- changing Protection Center Invite Settings UX;
- retiring unrelated `protection_center_clear_categories_guard`;
- changing Spam Guard behavior for non-invite spam.

## Expected production behavior

**No intended policy change.**

External invite deletion decisions remain centralized in `invite_policy_engine`.

The intended risk reduction is:

- one live invite listener instead of a dormant duplicate implementation;
- one recovery runtime;
- no legacy hard-block bridge stack;
- no obsolete Spam Guard invite override monkey patches;
- no dead precedence patch that can be resurrected accidentally.

## Validation required

- exact-head `git diff --check`;
- Python compile;
- `tools/audit_invite_link_safety.py`;
- `tests/test_invite_live_enforcement.py`;
- `tests/test_invite_runtime_reconcile_194.py`;
- `tests/test_protection_invite_guard_retirement.py`;
- `tests/test_protection_invite_native_ui.py`;
- invite-policy/message-surface tests;
- startup ownership tests;
- full Python suite;
- final changed-file/review-thread inspection;
- merge with expected-head guard;
- post-merge absence verification on `main`.

## Next step

Inspect the exact branch diff, open a focused draft PR, validate the exact head in GitHub CI or Termux, then merge and verify the five compatibility files remain absent while live and recovery invite enforcement remain native.
