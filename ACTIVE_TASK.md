# Active Task

## Active task / desired outcome

**P0-ANTINUKE-INFLIGHT-SELF-ACTION-RECEIPT-010 — prevent legitimate rate-limited bot-authored mutations from expiring their self-action proof and triggering compromise quarantine/self-ejection**

Desired outcome: any protected Discord mutation issued by this running Dank Shield
process must retain its one-time self-action proof for the entire outbound request
lifecycle, even when discord.py/Discord rate-limit pacing keeps the request in
flight longer than the normal post-request receipt TTL. Truly unexplained
bot-attributed protected actions must remain fail-closed.

## Scope / single active task lock

Only the AntiNuke self-action receipt lifecycle is active:

- preserve action/guild/target-scoped one-time DSA proof;
- keep proof alive while the protected Discord request is still in flight;
- begin the finite receipt TTL only after the request completes successfully;
- remove proof on failed or cancelled requests;
- preserve consumption if the audit event arrives before the HTTP request returns;
- preserve durable compromise quarantine/self-ejection for genuinely unmatched
  self-attributed protected actions;
- cover the reported bot-authored `channel_update` / rate-limited PATCH path;
- inspect the directly shared request-lifecycle code for the same failure mode;
- do not weaken protected action coverage or globally exempt `channel_update`.

Do not broaden into Server Stats design work, verification/ticket interaction
failures, moderator trust policy, Spam Guard, or unrelated AntiNuke redesign.

## Prior task closure

PR #323, **Hide staff permission recipes from regular members**, merged into
`main` as `b5a4fc5c27c1825aba8335db79fff70f8871a9df` on 2026-09-25.

Exact PR-head validation was green:

- DS Backlog 027 Validation — success
- Application Command Size Diagnostics — success
- Ticket Owner Emergency Override — success
- Dank Design Regression CI — success
- Profile Runtime Diagnostics — success
- Dank Shield CI — success

The merge commit also has a successful Discloud commit status.

PR #322 had already merged immediately before #323; its Server Stats / Dank
Design work is therefore inherited through the current `main` base.

## Findings / root cause

`stoney_verify/anti_nuke_self_action_runtime.py` creates a DSA authorization
receipt before awaiting the underlying Discord HTTP request.

Before this task:

1. `_authorize()` stamped the request and saved `created_at`.
2. `_prune_pending()` expired the receipt when
   `now - created_at > 120s`.
3. Only then did/does the wrapped code await Discord's request lifecycle.
4. A rate-limited PATCH can therefore remain in discord.py/Discord pacing longer
   than 120 seconds.
5. If the eventual audit event is attributed to Dank Shield after the receipt was
   pruned, `_audit_guard()` sees no matching proof.
6. `anti_nuke_zero_damage_runtime` replaces the unmatched handler with the
   durable compromise path, persists a 30-minute quarantine, and ejects the bot.

That makes request-start time the wrong TTL origin. The proof must not expire
while the request that owns it is still in flight.

## Execution path

Protected local mutation:

`discord.py mutation -> bot.http.request wrapper -> _request_spec() ->
_authorize() -> Discord HTTP/rate-limit pacing -> successful response ->
_audit_guard() -> _consume()`

False-compromise path before fix:

`_authorize(created_at) -> >120s in-flight -> _prune_pending() deletes proof ->
Discord completes PATCH -> channel_update audit event -> _consume() misses ->
zero-damage unmatched handler -> durable quarantine -> guild.leave()`

## Changes

Branch: `fix/antinuke-inflight-self-action-receipt-20260925`

Implemented:

- added `completed_at` lifecycle state to self-action authorizations;
- in-flight authorizations are no longer TTL-pruned;
- the 120-second receipt window starts after successful request completion;
- removed count-based eviction of valid self-action/side-effect provenance;
  completed receipts remain TTL-bounded and in-flight receipts are tied to real
  outstanding requests, so unrelated guild load cannot erase valid proof;
- HTTP request failure **or cancellation** discards the authorization;
- webhook edit/delete wrappers use the same completion/cancellation lifecycle;
- HTTP-derived `integration_delete` and reasonless `message_delete` side-effect
  receipts now use the same in-flight/completed lifecycle instead of aging while
  their parent request is still rate-limited;
- direct/manual expected side-effect receipts retain their existing immediate TTL;
- audit events can still consume either receipt type while the request is in flight;
- added a regression test that advances monotonic time beyond the old 120-second
  TTL during a protected `PATCH /channels/{id}` and verifies the eventual
  `channel_update` consumes proof without self-ejection;
- added cancellation cleanup coverage;
- added audit-before-HTTP-response coverage so a delayed request can consume its
  proof while still in flight;
- added >4096-entry regression coverage proving valid unexpired provenance is
  retained until TTL rather than evicted by global load;
- updated the old stale-receipt test so expiry is measured after completion.

## Validation / results

Pending exact-head validation.

Required before completion:

- compile changed Python modules;
- focused `tests/test_antinuke_self_action_runtime.py`;
- focused zero-damage AntiNuke tests;
- relevant gateway/guardian AntiNuke regression tests;
- full `pytest tests/`;
- repository GitHub workflows;
- final diff inspection;
- verify neither in-flight nor completed-but-unexpired proof is count-evicted,
  including above the former 4096-entry threshold;
- verify successful completed receipts still expire;
- verify failed/cancelled requests leave no stale proof;
- verify HTTP-derived side-effect receipts cannot expire before their parent
  request completes and still expire after completion;
- verify unexplained self-attributed protected actions still reach durable
  quarantine/self-ejection.

## Cleanup / conflicts

No unrelated code has been intentionally changed.

The fix does not add an exemption, retry shim, second AntiNuke owner, or duplicate
listener. It repairs the existing authoritative self-action proof lifecycle.

## Blockers / risks

- Local execution is not available through the GitHub connector, so validation
  must be driven by repository CI after the branch/PR is published.
- The existing 120-second **post-completion** TTL remains unchanged; CI/regression
  evidence must confirm this preserves expected one-time expiry behavior.

## Backlog

Preserve previously identified unrelated follow-ups without investigating them
inside this task, including moderator trust/re-entry behavior and trusted-role
selection.

## Next step

Inspect the exact branch diff, open a draft PR, run all repository workflows, fix
only same-task regressions, then perform final cleanup/conflict review before
marking the PR ready.
