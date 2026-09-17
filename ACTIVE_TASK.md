# ACTIVE TASK

## DS-SEC-ANTINUKE-RUNTIME-CONSOLIDATION — Make AntiNuke runtime ownership explicit

**Status:** PHASE A IMPLEMENTATION / VALIDATION

**Branch:** `refactor/antinuke-runtime-bootstrap-consolidation`
**Base main:** `bbf36ee6a4859f55a2e23373313cb21ccb64686d`

## Previous task locks closed

- PR #251 (`DS-MEMBER-JOIN-LOG-REGRESSION`) merged as `0c8d8ae1971656f0c5c2e9ef95ebd11a9c94fa97`, became `main`, and Discloud reported success.
- PR #253 (`DS-SEC-OWNER-POLICY-NORMALIZATION`) merged as `e895e875c1661d32424c968babca5ef098786fc1`, became `main`, and Discloud reported success.
- PR #254 (`DS-INVITE-HUMAN-EMBED-FALSE-POSITIVE`) merged as `bc0a14cf59bcddb7c7ac0a45498125921cae4023` and Discloud reported success.
- PR #255 (`DS-INVITE-INTERACTION-RESPONSE-FALSE-POSITIVE`) merged as `bbf36ee6a4859f55a2e23373313cb21ccb64686d` and Discloud reported success.

The Single Active Task Lock is now this AntiNuke runtime-ownership consolidation only.

## Resync after emergency hotfixes

The consolidation branch was paused while the two live Invite Shield regressions were fixed. Before resuming implementation, the old branch head `e46c7b771088e464867ae5cb374cd0e714c30d72` was preserved at `backup/antinuke-runtime-bootstrap-pre-hotfix-sync`, then the consolidation changes were reapplied onto current production `main` `bbf36ee6a4859f55a2e23373313cb21ccb64686d`.

The resync explicitly preserves the new `_install_invite_policy_message_surface_runtime()` bootstrap call before invite reconciliation. AntiNuke consolidation must not remove, reorder behind reconciliation, or otherwise regress the Invite Shield fixes from PRs #254/#255.

## Outcome target

Reduce the architectural risk created by AntiNuke being assembled through a long sequence of independently owned startup wrappers and overlapping runtime policy patches. Preserve all validated security behavior while making bootstrap order and policy ownership explicit enough that a later fix cannot silently depend on accidental monkey-patch order.

## Confirmed current execution path

Before this task, `main.py` independently installed the following AntiNuke layers in order:

1. gateway
2. finalizer
3. incident
4. hostile actor reputation
5. lockdown
6. self-action proof
7. zero-damage hardening
8. audit compatibility
9. readiness gate
10. product policy after app import
11. hostile re-entry race guard

That sequence is behaviorally important, but the ordering contract was spread across eleven wrapper functions in `main.py`, with each wrapper repeating lazy imports, duplicate handling, and exception handling. Several tests therefore pinned security behavior to string positions in the entrypoint instead of to one authoritative runtime bootstrap contract.

The deeper overlap audit also confirmed that lockdown, zero-damage, incident, and product-policy runtimes touch some of the same policy surfaces. PR #253 already made incident runtime the authoritative guild-owner severity classifier, so the old lockdown owner-first-strike wrapper is now legacy overlap rather than the source of truth.

## Phase A — bootstrap ownership consolidation

Implemented first because it is behavior-preserving and gives the remaining cleanup one explicit installation boundary:

- added `stoney_verify/anti_nuke_runtime_coordinator.py`
- moved the authoritative pre-app and post-app AntiNuke installation order into declarative layer tables
- centralized lazy import resolution, duplicate reporting, and per-layer fail-soft exception handling
- reduced `main.py` to one pre-app AntiNuke coordinator call and one post-app coordinator call
- preserved the exact behavioral module order and the app-import boundary
- left SpamGuard installation independent because it is not an AntiNuke runtime layer
- did not change thresholds, owner severity, containment, bot authorization, self-action proof, readiness requirements, or quarantine behavior in this phase

## Phase B — overlapping policy ownership cleanup

After Phase A is validated on the branch, continue inside this same task lock and remove only overlap that is proven redundant by current production semantics. Priority ownership boundaries:

- incident runtime owns guild-owner severity classification
- product-policy runtime owns normal Contain versus optional Strict Lockdown threshold behavior
- lockdown runtime owns control-plane/config-history/bot-delegation invariants, not owner severity
- zero-damage runtime owns expanded audit coverage, compromise quarantine, and self-action hardening, not product-tier threshold semantics
- gateway/guardian remain the event attribution and rollback surfaces

Do not collapse modules merely to reduce file count. A runtime is removed or simplified only when its behavior has a clear canonical owner and regression coverage proves the replacement path.

## Validation gate

Before merge:

- update startup-order tests to assert the coordinator contract instead of obsolete `main.py` wrapper strings
- add focused coordinator tests for exact phase order, app boundary, duplicate handling, and fail-soft continuation
- run Python compile and the full unit suite
- run standalone/security/event-boundary audits
- pass required `Python compile check`, `Claim-first ticket security`, and `Managed category SQL smoke test`
- pass companion Dank Shield, design, profile, command-size, and ticket-owner workflows
- inspect the exact final diff for debug code, stale wrappers, conflict artifacts, accidental policy changes, and duplicate runtime ownership
- final branch must be 0 behind `main`
- merge only the exact validated head
- verify resulting `main` and `discloud/commit: success` before releasing this task lock

## Next step

Verify the resynced branch is 0 behind current `main` with only the intended three consolidation files changed. Then finish Phase A regression migration and exact behavior checks before touching any Phase B overlap.
