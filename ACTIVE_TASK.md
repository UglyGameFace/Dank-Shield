# ACTIVE TASK

## Active task / desired outcome

**P0-BASIC-VERIFY-INTERACTION-001 — restore reliable Basic Verify button dispatch**

Make the public Basic Verify button acknowledge exactly once, enter one canonical
verification workflow, and remain restart-safe without competing interaction
owners.

## Production symptom

A live Basic Verify panel with footer
`dank_shield:basic_verify:v1 • access only` showed Discord's red
`This interaction failed` result after the user tapped **Verify**.

The displayed panel is the current Basic Verify surface, not the ID-upload flow.

## Status

**IMPLEMENTED ON TASK BRANCH — validation in progress**

Branch: `fix/basic-verify-interaction-owner-20260923`

Base after sync: `main@78565787362cf63d5f40cf2b7e2abb8c96833b38`

No merge-readiness claim is made until exact-head validation completes.\n\nThe branch was synchronized after AntiNuke work advanced `main` twice. The latest\nmain delta changes AntiNuke/Spam Guard/Protection Center files plus `ACTIVE_TASK.md`;\nthere is still no overlap with this task's Basic Verify runtime or regression files.

## Scope

In scope:

- trace the real Basic Verify component dispatch path;
- restore one runtime owner for the Basic Verify custom ID;
- preserve restart-safe persistent-view handling;
- retain a fallback only when persistent-view registration itself fails;
- ensure acknowledgement occurs before config/database/role work;
- prevent an already-acknowledged duplicate route from mutating roles again;
- retire the live compatibility wrapper that independently intercepted the same
  Basic Verify component;
- replace source-shape runtime coverage with behavioral regression tests.

Out of scope:

- ID/web verification;
- VC verification;
- ticket creation;
- verification role-policy redesign;
- setup redesign;
- AntiNuke;
- Spam Guard;
- unrelated startup-guard cleanup.

## Execution path inspected

Production boot:

`Discloud -> main.py -> stoney_verify.app -> commands/events -> bot.run()`

Basic Verify paths before this task:

1. `app.py` called `install_basic_verify_runtime(bot, strict=True)` before login.
2. That installer registered `BasicVerifyView()` with `bot.add_view(...)`.
3. The same installer also registered a global `on_interaction` fallback for the
   same `dank:basic_verify:v1` custom ID even when the persistent view succeeded.
4. `sitecustomize.py` also loaded `basic_verification_mode_guard`.
5. That compatibility guard independently wrapped
   `interaction_handlers.handle_component_interaction` and called
   `maybe_handle_basic_verify_interaction()` for the same Basic Verify custom ID.
6. The persistent button callback contained a second copy of the handler flow.

That left multiple live responders able to race on one Discord component
interaction.

## Root cause / regression finding

PR #81 previously established the correct ownership rule: use the persistent
Basic Verify view when registration succeeds, and install a global fallback only
when persistent registration cannot be confirmed.

Commit `e2be9e03b9ce` later moved restart safety into the native Basic Verify
module but changed that contract to register both the persistent view and the
global fallback every time. The older live compatibility wrapper in
`basic_verification_mode_guard` also remained reachable through
`sitecustomize.py`.

The result was duplicate dispatch ownership around one custom ID. This is an
ownership regression, not a reason to add another retry or another listener.

## Changes

### `stoney_verify/verification_new/basic_verify.py`

- Persistent `BasicVerifyView` is the primary and authoritative runtime owner.
- The global `on_interaction` fallback is registered only if `add_view()`
  fails.
- Once either route owns the custom ID, repeated installer calls cannot add a
  second route later in the same process.
- Fallback acknowledgement is immediate because no persistent view exists in
  fallback mode.
- `BasicVerifyButton.callback` delegates to the one canonical
  `maybe_handle_basic_verify_interaction()` implementation.
- `_ack()` now treats an already-acknowledged interaction as already claimed
  and does not allow a second role mutation.
- Canonical click logging includes the Discord interaction ID.

### `stoney_verify/startup_guards/basic_verification_mode_guard.py`

- Removed the compatibility wrapper that intercepted Basic Verify in the
  centralized component handler.
- The guard retains its still-live allowlist, Verify Panel command, and sync
  compatibility responsibilities.
- Native Basic Verify runtime ownership remains in
  `verification_new/basic_verify.py`.

### Tests

- Replaced `tests/test_basic_verify_native_restart_runtime_static.py`.
- Added behavioral `tests/test_basic_verify_native_restart_runtime.py` covering:
  - persistent-view ownership without a duplicate listener;
  - fallback-only behavior when persistent registration fails;
  - idempotent registration;
  - no late second owner after fallback registration;
  - fail-closed behavior when neither route can register;
  - exact custom-ID fallback routing;
  - button delegation to the canonical handler;
  - acknowledgement before role/database work;
  - no duplicate role mutation after another route already acknowledged.

## Compatibility review

Preserved:

- custom ID `dank:basic_verify:v1`;
- persistent view class and timeout;
- current and old posted panels;
- Basic Verify authorization checks;
- configured role resolution;
- role hierarchy checks;
- role mutation lock;
- user-facing success/error behavior;
- `register_basic_verify_runtime` compatibility alias;
- pre-login strict runtime installation from `app.py`;
- `/verify panel` canonical posting path.

No schema, command name, role-policy, or panel-layout changes are included.

## Validation required

- exact branch diff inspection;
- conflict-marker / whitespace inspection;
- Python compile;
- behavioral Basic Verify runtime tests;
- verification-mode authorization tests;
- Verify Panel posting tests;
- persistent interaction compatibility tests;
- relevant startup/interaction ownership tests;
- full `pytest tests/`;
- standalone repository tool checks required by CI;
- GitHub Actions on the exact final head;
- final changed-file and review-thread inspection.

## Cleanup / conflicts

The duplicate Basic Verify compatibility dispatcher is removed rather than
layered with another guard.

No unrelated production subsystem is intentionally changed.

## Blockers / risks

Live Discord acceptance still requires deployment after validated merge. A
repository test cannot reproduce Discord's client-side red failure banner, so
post-deploy acceptance must include one real Unverified account clicking the
existing panel after a bot restart.

## Backlog

None added from this task.

## Next step

Run exact-head targeted validation, correct only failures caused by this task,
then run the full repository validation and inspect the final diff before any
merge-readiness decision.
