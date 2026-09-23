# ACTIVE TASK

## Active task / desired outcome

**P0-ANTINUKE-SELF-001 — Prevent false self-ejection from self-caused `integration_delete` after bot removal**

When Dank Shield removes an unauthorized or known-hostile bot, Discord may automatically
remove that bot's integration and emit an `integration_delete` audit event attributed
to Dank Shield. Treat that exact short-lived derived cleanup as an expected self-action
side effect without weakening fail-closed detection for genuinely unexplained
`integration_delete` events.

## Status

**IMPLEMENTED ON ISOLATED BRANCH — validation in progress**

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
- regression-test matching, mismatching, sparse, repeated, expired, kick, and ban paths;
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
- arms an `integration_delete` expectation only when Dank Shield invokes
  `discord.Guild.kick` or `discord.Guild.ban` on an object explicitly marked as a bot;
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

AntiNuke pre-app installation patches `discord.Guild.kick/ban` before
`stoney_verify.app` is imported.

The later member-join removal safety guard captures the then-current Guild methods and
wraps them rather than replacing them with raw discord.py methods, so the AntiNuke
side-effect wrapper remains in the call chain. The old staff moderation global native
method patch is retired and does not overwrite this chain.

## Tests added / extended

`tests/test_antinuke_self_action_runtime.py` covers:

- expected bot-removal `integration_delete` is consumed before compromise handling;
- correlation is one-time;
- a different integration identity is not hidden;
- a second matching deletion is not hidden;
- sparse integration audit targets consume only the one pending guild/action receipt;
- receipts expire;
- successful bot kicks arm the receipt;
- failed bot kicks cancel their receipt;
- human kicks do not arm a receipt;
- bot bans arm the same derived cleanup receipt.

Existing tests continue to require an unmatched protected self-action to self-eject in
Contain mode.

## Validation / results

Pending:

- final branch diff inspection;
- Python compile/static syntax validation;
- focused self-action and zero-damage AntiNuke tests;
- bot authorization, guardian, race/re-entry, lockdown, runtime-coordinator regressions;
- broader AntiNuke regression suite;
- full applicable Python suite if the available runner permits it;
- PR review-thread and exact-head CI inspection.

No completion or merge-readiness claim until those checks have evidence.

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
narrowest available fallback: one guild, one `integration_delete`, one successful
bot-removal attempt, 15 seconds, one-time consumption. If Discord exposes application
or bot identity, a mismatch is rejected and the fail-closed path remains active.

## Next step

Inspect the exact diff, open a draft PR, run the focused and broader AntiNuke validation,
then resolve any failures before deciding merge readiness.
