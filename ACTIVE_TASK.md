# ACTIVE TASK

## Active task / desired outcome

**P0-BASIC-VERIFY-FALLBACK-001 — restore live Basic Verify button reliability after PR #304**

Restore the existing Basic Verify panel so a real Discord button click is
acknowledged even when discord.py's persistent-view dispatch misses an
already-posted component after restart.

## Production symptom

The live Basic Verify panel still renders correctly with footer
`dank_shield:basic_verify:v1 • access only`, but tapping **Verify** can produce
Discord's red **This interaction failed** banner.

Because the canonical handler defers before config/database/role work, that red
client failure points to the click not reaching a successful acknowledgement
path, rather than a normal role-hierarchy/config rejection.

## Status

**ROOT CAUSE IDENTIFIED — repair implemented; exact-head validation pending**

Branch: `fix/basic-verify-delayed-safety-fallback-20260924`

Base: `main@f9644c73d31b502259c36ff5658265918372e40e`

## Recent-change review

PR #289 (Server Stats) and PR #302 (Exit Card Unicode) did not change Basic
Verify runtime files.

The only recent Basic Verify production change was PR #304,
`3c21431e7c4c8920b76ad671cf857b232f300c6d`.

PR #304 correctly removed:

- the compatibility wrapper in `basic_verification_mode_guard`;
- duplicated role-mutation callback code;
- immediate competing interaction ownership.

But it also changed the native runtime from:

- persistent view **plus delayed gateway fallback**

to:

- persistent view only whenever `bot.add_view()` succeeds.

That was an over-correction.

## Root cause

The previous validation treated successful `bot.add_view(BasicVerifyView())`
registration as proof that every real component click would reach that callback.

It is not.

For the deployed `discord.py==2.7.1`, the gateway interaction parser:

1. creates the Interaction;
2. dispatches the component to the internal ViewStore;
3. then emits the public `interaction` event.

The internal view dispatch schedules the item callback. Therefore a delayed
`on_interaction` listener can safely provide a second *dispatch route* without
creating a second role-mutation owner:

- persistent view gets first chance;
- listener waits 150 ms;
- listener exits if the interaction is already acknowledged;
- only a still-unanswered Basic Verify click enters the same canonical handler;
- canonical `_ack()` remains the final single-claim boundary before any
  database or role mutation.

This is also the contract originally documented by PR #81, which fixed dead
already-posted Basic Verify panels by keeping a fallback only after giving native
view handling the first chance.

## Changes

### `stoney_verify/verification_new/basic_verify.py`

- restored the global Basic Verify `on_interaction` safety listener even when
  the persistent view registers successfully;
- restored a short 150 ms grace window when the persistent view exists;
- skips fallback work if the interaction has already been acknowledged;
- handles immediately when persistent-view registration failed entirely;
- both routes still delegate to the same
  `maybe_handle_basic_verify_interaction()` implementation;
- `_ack()` still rejects an interaction already claimed elsewhere;
- partial registration is independently retryable without adding duplicate
  listeners/views;
- startup diagnostics now report persistent-view and delayed-fallback state;
- fallback takeover logging includes the Discord interaction ID.

### `stoney_verify/app.py`

- corrected the startup ownership comment to match the restored runtime.

### Tests

`tests/test_basic_verify_native_restart_runtime.py` now covers:

- persistent view + delayed safety listener registration;
- fallback-only operation if persistent registration fails;
- filling a missing persistent route without duplicating the listener;
- idempotent full registration;
- strict failure if neither path can register;
- exact custom-ID filtering;
- persistent callback winning during the grace window;
- delayed fallback claiming a click when persistent dispatch misses;
- canonical button delegation;
- acknowledgement before role/database work;
- duplicate role mutation remaining impossible.

## Ownership / compatibility

Still one canonical mutation owner:

`maybe_handle_basic_verify_interaction`
→ `_ack`
→ `apply_basic_verification`

Not restored:

- the old `basic_verification_mode_guard` component wrapper;
- duplicate callback business logic;
- a second role/config implementation.

Preserved:

- custom ID `dank:basic_verify:v1`;
- existing posted panels;
- guild verification-mode authorization;
- role hierarchy checks;
- role mutation lock;
- `/verify panel`;
- old/current panel embed recognition.

## Validation required

- exact branch diff inspection;
- conflict-marker and whitespace inspection;
- Python compile;
- Basic Verify behavioral tests;
- verification-mode authorization tests;
- public Verify Panel tests;
- persistent interaction compatibility tests;
- full `pytest tests/`;
- standalone repository checks/audits;
- all GitHub workflow gates;
- review-thread inspection;
- final mergeability/currentness check.

## Blockers / risks

Repository tests can model discord.py's dispatch ordering but cannot reproduce
Discord's client-side red failure banner. Post-deploy acceptance still requires
one real Unverified account clicking the existing panel after a bot restart.

## Backlog

- partner-server live activity panel remains separate;
- no unrelated subsystem is active in this task.

## Next step

Open the isolated draft PR, run exact-head CI, fix only failures caused by this
repair, then perform final diff/currentness/review inspection before merge
readiness.
