# ACTIVE TASK

## DS-BACKLOG-027 — Finish incorrectly bulk-closed production backlog

**Status:** ACTIVE — ROOT-CAUSE / IMPLEMENTATION PASS
**Branch:** `fix/ds-backlog-integrity-027`
**Base:** merged `main` at `13759f84ce15ae5dcd7a48e01438d1dc94a1639b` (PR #181)
**Started:** 2026-08-11

## Scope

Finish or correctly supersede the issues that were bulk-closed as completed without enough implementation evidence:

- #119 — one-click channel/category permission repair
- #57 — Channel Builder direct registration + remove compatibility shim
- #56 — global guild operation queue completion/integration
- #20 — production inactive-member cleanup execution
- #11 — TicketTool parity stabilization umbrella completion/supersession
- #2 — invite/join/inviter/approval truth pipeline completion/supersession

Issues #1 and #168 were independently verified as genuinely implemented and are not being reopened or rewritten.

## Rules

- Do not use issue state as implementation evidence.
- Fix owner files/services directly; remove obsolete compatibility shims instead of adding patch-on-patch behavior.
- Preserve the compact `/dank` public command surface and current multi-server isolation.
- No Administrator permission requirement for public installs.
- Dangerous mutations must use shared queue/idempotency or an equivalent scoped exclusive path.
- Definition of Done requires targeted tests, regression/static/compile coverage, cleanup/dead-code/conflict inspection, and exact-final-head CI before merge readiness.

## Work plan

### #57 Channel Builder direct registration
- [ ] Verify `server.py` registration path and queue audit expectations.
- [ ] Register Channel Builder routes directly in the owner API startup path.
- [ ] Remove `channel_builder_api_guard` compatibility shim and obsolete workflow/codemod artifacts where safe.
- [ ] Lock direct-registration behavior with tests/static audit.

### #119 Permission repair
- [ ] Inventory existing permission diagnostics/health models and OAuth invite permission helpers.
- [ ] Add channel/category target selection, exact missing-permission audit, minimum/full repair modes, preview/confirmation, hierarchy/blocker explanations, reauthorization fallback, child-channel sync option, audit logging, and undo/restore.
- [ ] Integrate repair entry points with setup/diagnostics without adding command clutter.

### #56 Operation queue
- [ ] Audit current queue against issue acceptance criteria and all dangerous mutation callers.
- [ ] Complete persistence/recovery/cancellation/backpressure/retry/result-reporting gaps that are still real.
- [ ] Convert remaining named high-risk flows or explicitly prove they already use the shared queue/exclusive layer.
- [ ] Add health/metrics and startup stale-job reconciliation coverage.

### #20 Inactive cleanup
- [ ] Preserve existing conservative scan/review engine.
- [ ] Add dry-run-first guided cleanup execution with protections, confirmation, authorized no-confirm option, bounded removals, partial-failure reporting, one summary log, and persisted last cleanup summary.
- [ ] Keep low-confidence/manual-review candidates non-removable by default.

### #2 Join/approval truth
- [ ] Audit startup/reconnect invite cache warming, join diffing, approval writes, reconcile behavior, and dashboard/staff readers.
- [ ] Standardize canonical join/approval field mapping and conflict handling.
- [ ] Ensure confirmed/partial/unknown truth survives verification and reconcile flows and both UI readers use the same truth model.

### #11 TicketTool parity umbrella
- [ ] Map each remaining checklist item to current canonical implementations/tests.
- [ ] Fix any genuine residual gap discovered by that mapping.
- [ ] Close as superseded only when every remaining requirement is either implemented here or linked to a completed canonical successor.

## Definition of Done

This task is complete only when the six questionable issues are either fully implemented to their current acceptance criteria or deliberately superseded with exact successor evidence, obsolete shims are removed, dangerous mutations are protected by canonical queue/idempotency paths, inactive cleanup can safely execute rather than only scan, join/approval attribution remains truthful through reconcile paths, permission repair is actually usable end-to-end, targeted/regression/static/compile checks are green, and the final PR head passes CI without unresolved review threads.