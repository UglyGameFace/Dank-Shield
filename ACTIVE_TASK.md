# ACTIVE TASK

## Active task / desired outcome

**P0-ANTINUKE-MOD-001 — stop legitimate moderation from becoming durable hostile identity and restore Spam Guard cleanup**

Production incident:

- Spam Guard correctly detected and removed a malicious member.
- Its selected spam messages were left behind.
- A moderator manually deleted one of those messages.
- AntiNuke treated that ordinary moderator deletion as destructive, contained the moderator,
  persisted the moderator as a confirmed hostile actor, and fast re-entry kept removing them.

The fix must correct the policy and cleanup defects without weakening genuinely destructive
AntiNuke behavior.

## Status

**IN PROGRESS — isolated branch**

Branch: `fix/antinuke-moderator-spam-cleanup-20260923`

Base: merged `main` commit `67e5c50d00f45c9164e2d09fa7aff352f2a84a66`
(PR #303).

## Confirmed root causes

### 1. Ordinary moderator message deletion is first-strike in normal Contain

`anti_nuke_zero_damage_runtime.py` registers `message_delete` as a strict/destructive
Guardian action.

`anti_nuke_product_policy_runtime.py` removes the historical hardcoded override in
normal Contain, but the canonical AntiNuke engine still assigns an untrusted human a
threshold of 1 and aggregate threshold of 1.

Therefore a moderator who is not manually listed in the AntiNuke trust lists can be
contained after one ordinary audit-log `message_delete`.

Discord's audit-log contract defines `message_delete` specifically as a message deleted
by a moderator, with the message author as target and count/channel metadata in
`entry.extra`. It is not sufficient by itself to prove server takeover.

### 2. The false containment becomes durable and causes a rejoin loop

The hostile-actor containment wrapper records the actor as
`confirmed_destructive_actor` before containment.

`anti_nuke_reentry_race_runtime._fast_member_join` reads hot/local hostile reputation
and immediately removes an active hostile identity when AntiNuke is enabled in Contain.

Existing legacy false-positive cleanup covers several ordinary Discord actions but not
the exact historical reason:

`Dank Shield AntiNuke containment: Message deletion`

As a result, adding the moderator to a trust list does not clear the already-poisoned
hostile row.

### 3. Spam Guard single-message cleanup uses an incompatible delete call

`spam_guard._delete_recent_messages` calls
`PartialMessage.delete(reason=reason)` for the one-message path and repeats the same
call in fallback cleanup.

The deployed discord.py Message/PartialMessage delete path does not accept that
`reason=` keyword. The exception is swallowed, so `deleted_count` can remain zero
while Spam Guard still proceeds to timeout/kick/ban the detected spammer.

`commands_ext/public_spam_cleanup_hardening.py` repeats the same incompatible
`message.delete(reason=...)` pattern.

## Scope

In scope:

- make normal Contain treat ordinary `message_delete` as non-punitive;
- preserve first-strike `message_delete` enforcement in Strict Lockdown;
- keep `message_bulk_delete` as a distinct high-risk action;
- automatically mask and durably clear only legacy hostile records produced by the exact
  ordinary-message-deletion false-positive reason;
- preserve real destructive hostile records such as channel deletion;
- repair Spam Guard single-message/fallback message deletion using the supported delete API;
- repair the public Spam Guard cleanup sweep's direct Message.delete call;
- make unexpected cleanup failures visible instead of silently disappearing;
- add a native Discord role selector for AntiNuke trusted roles using the existing
  `antinuke_trusted_role_ids` persistence;
- retain existing trusted user and pre-approved bot ID support;
- add focused regression coverage for every behavior above.

Out of scope:

- weakening channel/role/webhook/integration destructive protections;
- changing bot-add authorization policy;
- removing Strict Lockdown;
- changing Spam Guard detection thresholds or kick/ban policy;
- Exit Card Unicode work.

## Intended product behavior

Normal Contain:

- ordinary moderator deletion of a message is not a destructive AntiNuke incident;
- bulk message deletion remains independently protected;
- destructive structural/moderation actions remain protected;
- moderators do not need manual trust merely to perform routine message cleanup.

Strict Lockdown:

- `message_delete` remains a protected first-strike action.

Trust lists:

- user IDs and pre-approved bot IDs remain available;
- trusted roles are selectable through a native Discord role picker;
- role trust is an optional delegation feature, not a workaround for the moderator
  false-positive.

Spam Guard:

- selected malicious messages are actually deleted using supported discord.py methods;
- the punitive user action can still proceed independently;
- cleanup failures are observable in diagnostics instead of being silently swallowed.

## Validation required

Before merge readiness:

- focused AntiNuke benign-action / product-policy / hostile-reentry tests;
- focused Protection Center trust UI tests;
- focused Spam Guard cleanup tests for one message, fallback, bulk, and sweep paths;
- exact legacy false-positive cleanup test for Message deletion;
- explicit regression proving Channel deletion hostile reputation is never cleared;
- explicit Strict Lockdown regression proving message deletion is still enforced;
- Python 3.11 compile;
- full repository `tests/` suite;
- standalone `tools/test_*.py` checks and repository audits;
- exact-head workflow/review/conflict inspection.

## Completed prerequisite

PR #303 — Prevent AntiNuke self-ejection on bot-removal integration cleanup

- merged to `main` as `67e5c50d00f45c9164e2d09fa7aff352f2a84a66`;
- exact tested PR head passed 1850 tests plus all repository workflows/audits.

## Suspended task

PR #302 — Diagnose cross-guild Exit Card Unicode rendering

- remains suspended;
- do not mix its runtime/test changes into this P0.

## Next step

Implement the normal-Contain message-delete policy boundary, exact legacy reputation
cleanup, Spam Guard delete compatibility fixes, and native trusted-role selector with
focused regression tests before opening the implementation PR for full CI.
