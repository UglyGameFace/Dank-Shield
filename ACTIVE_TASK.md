# ACTIVE TASK

## Active task / desired outcome

**P0-ANTINUKE-SELF-001 — Prevent false self-ejection from self-caused `integration_delete` after bot removal**

When Dank Shield removes an unauthorized or known-hostile bot, Discord may automatically
remove that bot's integration and emit an `integration_delete` audit event attributed
to Dank Shield. Treat that exact short-lived derived cleanup as an expected self-action
side effect without weakening fail-closed detection for genuinely unexplained
`integration_delete` events.

## Status

**IMPLEMENTATION COMPLETE ON ISOLATED BRANCH — repository validation blocked by runner allocation**

Branch: `fix/antinuke-self-integration-delete-20260923`

Base: current `main` at `0935f80071723b878ba1fb85a3402608512d1aec`
(PR #297 merge).

## Root cause

The self-action proof in `anti_nuke_self_action_runtime.py` authorizes direct protected
REST mutations by adding a one-time `[DSA:<nonce>]` marker to the Discord audit reason.

The bot-add containment path removes an unauthorized bot with `guild.kick(...)`.
Discord can then delete that bot's integration as a secondary consequence. That
`integration_delete` is not a direct integration DELETE request from Dank Shield, so
it has no DSA marker.

Because `integration_delete` is intentionally protected, the self-action guard saw
the markerless event attributed to Dank Shield as an unexplained self-action.
`anti_nuke_zero_damage_runtime.py` then persisted the compromise quarantine and
self-ejected from the guild.

The failure was therefore a missing causal correlation between a locally initiated bot
removal and Discord's derived integration cleanup, not evidence of a stolen token.

## Execution path

1. `anti_nuke_guardian_runtime._handle_bot_add` or the native
   `anti_nuke._handle_bot_add` rejects an unauthorized bot.
2. Contain mode calls `guild.kick(bot, reason=...)`.
3. Discord may remove the OAuth/integration object and emit `integration_delete`.
4. `anti_nuke_self_action_runtime._audit_guard` sees the actor as Dank Shield.
5. There is no direct REST DSA nonce for the Discord-generated integration cleanup.
6. Before this fix, the event fell into `_unmatched_self_action`.
7. Zero-damage hardening persisted a 30-minute compromise quarantine, then retried
   `guild.leave()`.

## Scope

In scope:

- correlate bot kick/ban operations initiated through `discord.Guild` with the
  possible derived `integration_delete`;
- keep the correlation guild-scoped, one-time, short-lived, and bot-specific when
  audit metadata exposes the application/bot identity;
- support sparse Discord integration audit targets without disabling
  `integration_delete` protection;
- cancel the correlation when the initiating kick/ban fails;
- regression-test matching, mismatching, sparse, repeated, expired, kick, ban,
  event-ordering, and concurrent-removal paths;
- preserve the existing fail-closed compromise path for unexplained self-actions.

Out of scope:

- changing bot-add authorization/preapproval policy;
- automatically trusting Top.gg or any other newly invited bot;
- changing AntiNuke thresholds or containment policy;
- Exit Card Unicode/font work;
- unrelated moderation/removal redesigns.

## Implementation

`stoney_verify/anti_nuke_self_action_runtime.py` now:

- maintains an in-memory expected-side-effect ledger separate from direct DSA nonces;
- extends the existing authoritative HTTP self-action interceptor rather than adding
  another Discord moderation-method monkey patch;
- arms an `integration_delete` expectation when a protected local kick/ban targets
  a cached bot, or when an AntiNuke bot-removal reason confirms the freshly-added-bot
  path during a temporary cache miss;
- scopes each expectation to one guild, one action, one related bot ID, and a 15-second TTL;
- consumes each expectation at most once;
- matches integration `user`, `application`, `application.bot`,
  `application_id`, or `user_id` when Discord supplies those fields;
- falls back to the one-time guild/action correlation only when Discord supplies a
  sparse integration target with no semantic bot/application identity;
- cancels the expected side effect if the initiating bot kick/ban raises;
- checks expected derived side effects only after ordinary DSA nonce matching and
  before the fail-closed unmatched-self-action path.

Existing direct integration deletions still use the normal DSA nonce route.

## Compatibility / patch ordering

No new `discord.Guild.kick/ban` monkey patch is introduced.

The correlation runs inside the existing self-action HTTP interceptor that already owns
DSA nonce creation for protected local REST mutations. That makes the correlation
independent of higher-level `Guild` / `Member` moderation wrappers and preserves the
existing member-removal safety guard's ownership.

## Tests added / extended

`tests/test_antinuke_self_action_runtime.py` covers:

- expected bot-removal `integration_delete` is consumed before compromise handling;
- correlation is one-time;
- a different integration identity is not hidden;
- a second matching deletion is not hidden;
- sparse integration audit targets consume only the one pending guild/action receipt;
- receipts expire;
- a protected HTTP bot kick arms the receipt and the derived cleanup consumes it;
- cached bot identity can establish the correlation;
- a narrow allowlist of actual AntiNuke bot-removal reasons covers the freshly-added-bot
  cache race without treating human AntiNuke containment as bot removal;
- failed bot-removal HTTP requests cancel both the direct DSA nonce and side-effect receipt;
- human removals do not arm a receipt;
- bot bans arm the same derived cleanup receipt.

Existing tests continue to require an unmatched protected self-action to self-eject in
Contain mode.

## Validation / results

Completed at exact implementation head before this task-record update:

- branch comparison: 10 commits ahead / 0 behind the production base at that checkpoint;
- changed-file scope: exactly `ACTIVE_TASK.md`,
  `stoney_verify/anti_nuke_self_action_runtime.py`, and
  `tests/test_antinuke_self_action_runtime.py`;
- PR #303 was mergeable and remained draft;
- review threads: none;
- cleanup inspection found no leftover temporary Guild kick/ban patch, no broad
  `startswith("dank shield antinuke")` fallback, no debug/TODO/HACK additions, and
  no newly added line over 120 characters;
- isolated correlation logic harness passed matching identity, mismatching identity,
  sparse audit target, guild scoping, one-time use, expiry, cached-bot recognition,
  exact bot-removal reason fallback, human-containment rejection, and concurrent
  same-guild bot removals whose rich integration identities arrive out of order;
- isolated guarded-request sequencing harness passed: the DSA nonce and derived
  integration receipt are both armed before the protected HTTP request can execute,
  and a failed request cancels both;
- focused regression coverage now also exercises both audit order concerns directly:
  the normal direct kick audit can consume its DSA nonce while the independent
  integration-cleanup receipt remains available for the derived cleanup.

GitHub-hosted validation is currently non-executing, not code-failing:

- historical control: Dank Shield CI run `35483010115` on `main` started
  2026-09-20 02:04:06 UTC and completed successfully at 02:12:42 UTC; its checkout,
  Python setup, dependency install, diff check, compile, full unit suite, standalone
  tools, and repository audits all actually executed and passed;
- failure boundary: by Dank Shield CI run `35488999182`, created
  2026-09-20 04:22:58 UTC, the jobs were already failing with no steps/runner;
- that boundary predates this P0 branch and therefore rules out this branch as the
  cause of the repository's hosted-runner outage;
- account-level control: the public `UglyGameFace/Idle-Grow-Op` repository still had
  successful GitHub-hosted CI runs on 2026-09-23, so hosted Actions were not globally
  unavailable for the account; private-repository quota/entitlement/provisioning
  remains a plausible class of cause, but the available connector cannot read private
  Actions billing/entitlement state and no narrower cause is claimed;
- the failed workflows were explicitly retried and GitHub accepted all five reruns;
- rerun attempt 2 again failed before any step ran;
- the Dank Shield CI job metadata reports `runner_id=0`, an empty runner name,
  an empty steps array, and 0 ms billable Ubuntu execution;
- a fresh workflow set triggered by the later regression-test commit failed in the
  same pre-runner state;
- `Python compile check` therefore still never reached checkout or Python;
- job-log download returned no executable log on the earlier identical failure mode;
- therefore GitHub Actions has not run `git diff --check`,
  `python -m compileall -q stoney_verify main.py tools`, or the pytest suite.

The current environment cannot clone the private repository directly, so the repository
Python 3.11 compile/test suite cannot be truthfully claimed as run here.

Still required before merge readiness:

- repository-native `git diff --check`;
- Python 3.11 compileall;
- focused self-action and zero-damage AntiNuke tests;
- bot authorization, guardian, race/re-entry, lockdown, and runtime-coordinator regressions;
- broader AntiNuke regression suite;
- full `tests/` suite per the repository CI convention;
- final exact-head PR/CI/review inspection after those commands run.

No fixed/complete/merge-ready claim is made while repository execution remains blocked.

## Cleanup / conflicts

- Branch was created directly from current `main`.
- No Exit Card runtime/test changes were brought into this branch.
- The only intended runtime ownership change is the self-action causal-correlation layer.
- No direct `integration_delete` protection, quarantine behavior, or self-ejection code
  has been removed.

## Suspended task

PR #302 — **Diagnose cross-guild Exit Card Unicode rendering**

- state: open draft;
- branch: `fix/exit-card-font-cross-guild-20260923`;
- head when suspended: `c9d8b2a636ccae8ddaf3f1b94b1bd7b07936e7e2`;
- changed files: `ACTIVE_TASK.md`, `tests/test_exit_card_renderer.py`;
- stage: renderer-level reproduction/validation only, no production runtime change;
- next step when resumed: run the focused renderer/fallback regression in the Ubuntu
  validation environment before choosing a runtime fix.

## Backlog

Separate from this P0:

- decide whether bot-install preapproval/authorization UX should change for owner-added
  bots such as Top.gg. That is a product-policy decision and is not required to stop
  Dank Shield from falsely diagnosing its own integration cleanup as credential compromise.

## Blockers / risks

The sparse Discord audit target case cannot prove the related bot ID, so it uses the
narrowest available fallback: one guild, one `integration_delete`, one locally
authorized bot-removal request, 15 seconds, one-time consumption. If Discord exposes
application or bot identity, a mismatch is rejected and the fail-closed path remains
active.

The cache-miss reason fallback is restricted to the exact known bot-removal reason family.
Generic human containment such as
`Dank Shield AntiNuke containment: unauthorized bot addition ...` explicitly does not
qualify.

The side-effect receipt is armed before the protected HTTP request so the audit event
cannot win a race against the request return. If that request fails, both its normal
DSA nonce and the side-effect receipt are canceled.

## Next step

Run the repository-native compile and pytest validation as soon as an executable runner is
available. Keep PR #303 draft until that evidence is green, then perform one final
exact-head diff/CI/review inspection before any merge decision.
