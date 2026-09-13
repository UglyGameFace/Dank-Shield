# ACTIVE TASK

## DS-SEC-040 — AntiNuke incident reliability follow-up

**Status:** REBASED ON PR #204 / EXACT-HEAD VALIDATION PENDING
**Branch:** `fix/antinuke-owner-compromise-response`
**Base:** `17a432b45b6d170486bb898f2b5afef82afceaa5` (`main`, merge of PR #204)
**PR:** #203

## Objective

Finish the AntiNuke reliability follow-up without changing PR #204's managed-integration readiness behavior.

## Rebase result

PR #203 previously overlapped PR #204 in `stoney_verify/anti_nuke_finalizer_runtime.py`. The rebased design removes that overlap:

- PR #204's finalizer remains unchanged.
- State continuity, ready-time prewarm, owner-boundary reporting, and sparse audit attribution recovery live in `stoney_verify/anti_nuke_incident_runtime.py`.
- Startup order is gateway -> finalizer -> incident runtime -> app import.
- The incident audit listener replaces the gateway audit listener rather than stacking beside it.

## Scope

- `main.py`
- `stoney_verify/anti_nuke_incident_runtime.py`
- focused AntiNuke incident/runtime tests
- this task file

## Regression coverage

- last-known enabled state survives a temporary authoritative-config outage;
- intentional disabled state remains disabled;
- ready-time prewarm covers connected guilds;
- guild-owner boundary events are reported rather than silently discarded;
- sparse bot-add, role-create, dangerous-role-update, member-role-update, and timeout audit entries use target-correct recovery;
- exactly one audit-log-entry listener remains after runtime installation;
- PR #204 readiness regressions remain inherited from `main`.

## Hard limits

Discord does not allow a bot to contain the physical guild owner. Local state continuity is not structural backup/restore. Missing Discord audit evidence can still prevent attribution, so failure paths must remain visible.

## Definition of done

- exact-head targeted tests pass;
- full Dank Shield CI and companion workflows are green on the same head;
- no PR #204 readiness behavior is overwritten;
- PR #203 metadata reflects the rebased scope;
- merge only after final exact-head validation.
