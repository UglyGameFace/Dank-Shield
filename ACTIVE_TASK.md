# ACTIVE TASK

## DS-STABILITY-001 — Finish Dank Shield public stability mission

**Status:** READY FOR REVIEW — NOT MERGED — NOT DEPLOYED
**Branch:** `fix/finish-dank-shield-stability-mission`
**PR:** #101
**Base:** current `main`

## Scope

- Finish Dank Design Smart Auto-Detect category-local correctness and cleanup.
- Make SpamGuard default ON for new/missing-row guilds while preserving explicit OFF.
- Define and protect the canonical public Discord command surface.
- Remove repetitive startup log noise without hiding useful diagnostics.
- Report activity-tracking permission gaps honestly and keep inactivity cleanup fail-closed.
- Make member departure reconciliation authoritative-fetch-only for negative membership evidence.
- Remove temporary/debug/CI machinery and conflicting duplicate code.
- Pass compile, tests, standalone tools, required audits, diff checks, and GitHub Actions before merge approval.

## Root Causes

- Dank Design already had category-local inference from PR #92, but tie/example ordering was not fully deterministic and a stale duplicate repair-safety implementation still overrode the newer frame-matching logic.
- SpamGuard enabled defaults were duplicated across runtime/setup paths instead of being owned by one authoritative product constant.
- `public_access_control` imported advanced setup command modules only to patch their permission helper; those imports executed module-level decorators and could attach commands that public mode had reported as skipped.
- `public_setup_review` attached direct `/dank setup-review` and `/dank db-check` aliases at import time.
- Public command registration and cleanup maintained separate `/dank` allowlists that could drift.
- Activity diagnostics did not expose the complete inaccessible channel list or exact missing permissions, and inactivity confidence could overstate usable coverage.
- Member reconciliation fell back from a failed full `fetch_members()` call to `guild.members` and then used cache absence as negative membership evidence, creating a mass false-departure risk.

## Permanent Fixes

- Dank Design Smart Auto-Detect remains category-local, deterministic, conservative, preview-first, raw-separator-spacing aware, saved-lock aware, protected-name safe, and exact for `keep_existing` icons.
- Removed the duplicate stale majority-repair safety helper block so one implementation owns the behavior.
- Added one authoritative `SPAM_GUARD_DEFAULT_ENABLED = True` contract and wired runtime/setup defaults to it.
- Missing SpamGuard rows default ON and bootstrap through the existing loader; persisted enabled/disabled choices remain authoritative; database load errors are reported separately.
- Defined the intentional public global surface as 9 application commands and one canonical direct `/dank` child contract.
- Removed advanced setup-module imports from public access-control installation and made direct advanced setup aliases registrar/profile-controlled.
- Reused the canonical public command contract in stale-command cleanup and bumped the cleanup epoch for one guaranteed reconciliation sync.
- Added read-only activity scope inspection for View Channel, Read Message History, and Manage Threads/private-thread coverage.
- Setup Check and `/dank diagnostics` now list exact activity-access gaps and coverage percentages without granting permissions.
- Inactivity scans combine retained-data coverage with channel-scope coverage and remain non-actionable while scope is incomplete.
- Member departure marking now requires a successful authoritative Discord member enumeration; cache fallback is positive-presence evidence only.
- Startup SpamGuard state reporting emits once per guild/process with: DEFAULT ENABLED, PERSISTED ENABLED, PERSISTED DISABLED, or DATABASE LOAD ERROR.

## Validation

Code head `932730078a18073dbef3e399cad9dd1857bf467e` passed:

- GitHub Actions `Dank Shield CI` run 439: SUCCESS.
- `Ticket Category Menu Sanity` run 114: SUCCESS.
- `git diff --check`: PASS.
- Python compile check: PASS.
- Full `pytest tests/ -q --tb=short --disable-warnings`: PASS.
- Every standalone `tools/test_*.py`: PASS.
- Public setup audit: PASS.
- Canonical public command surface audit: PASS.
- Public command/friction audit: PASS.
- Public invite permission audit: PASS.
- Setup safety audit: PASS.
- Dank Design Smart Auto-Detect audit: PASS.
- Role truth audit: PASS.
- Event boundary audit: PASS.

This task-record-only commit must also remain green before merge approval.

## Cleanup Status

- Temporary Dank Design cleanup workflow: REMOVED.
- Temporary Dank Design patch/applier script: REMOVED.
- Temporary pytest failure-log artifact step: REMOVED.
- Temporary Dank Design source-snapshot artifact step: REMOVED.
- Duplicate stale Dank Design safety helpers: REMOVED.
- `sitecustomize.py`: UNCHANGED by this PR.
- `usercustomize.py`: UNCHANGED by this PR.
- Main CI workflow contains only permanent validation steps.
- No patch runner, debug dump, captured failure text, self-modifying CI, or CI trigger file remains in the PR diff.

## Final Diff Audit — 34 Files

- `.github/workflows/ci.yml` — permanent diff check plus canonical command-surface and Dank Design audits; no temporary artifact/debug machinery.
- `ACTIVE_TASK.md` — locked mission record, findings, validation, cleanup, blockers, and final diff audit.
- `docs/public-production-env.md` — documents the intentional 9-command public global surface and hidden advanced aliases.
- `stoney_verify/command_surface_contract.py` — single canonical public global/direct-child/hidden-command contract.
- `stoney_verify/commands_ext/public_access_control.py` — removes advanced-module import side effects and gates direct setup-access alias to admin/dev profiles.
- `stoney_verify/commands_ext/public_diagnostics_group.py` — read-only activity coverage and exact permission-gap diagnostics.
- `stoney_verify/commands_ext/public_setup_group.py` — surfaces activity scope in Setup Check.
- `stoney_verify/commands_ext/public_setup_review.py` — removes import-time direct advanced command attachment and adds explicit registrar.
- `stoney_verify/members_new/activity_scope.py` — shared read-only channel/thread permission coverage model.
- `stoney_verify/members_new/activity_service.py` — folds activity scope into inactivity confidence and fail-closed actionability.
- `stoney_verify/members_new/membership_authority.py` — distinguishes authoritative full member enumeration from non-authoritative cache fallback.
- `stoney_verify/members_new/sync_service.py` — blocks departure marking unless membership enumeration is authoritative.
- `stoney_verify/services/server_design_majority_layout.py` — deterministic category-local inference and removal of stale duplicate safety helpers.
- `stoney_verify/spam_guard.py` — runtime SpamGuard default now reads the authoritative enabled constant.
- `stoney_verify/spam_guard_defaults.py` — single authoritative default-ON product policy.
- `stoney_verify/startup_guards/__init__.py` — loads the permanent once-per-startup SpamGuard state reporter.
- `stoney_verify/startup_guards/setup_service_modes.py` — setup/service SpamGuard defaults and normalizer use the authoritative enabled constant.
- `stoney_verify/startup_guards/slash_command_cleanup.py` — uses canonical public `/dank` contract and updated cleanup epoch.
- `stoney_verify/startup_guards/spam_guard_default_state_guard.py` — once-per-startup SpamGuard state classification/logging.
- `tests/test_activity_scope_permissions.py` — exact inaccessible-channel and missing-permission coverage tests.
- `tests/test_external_healthchecks_watchdog_static.py` — updates its existing SpamGuard assertion to the authoritative default contract; watchdog runtime behavior is unchanged.
- `tests/test_inactive_scan_activity_scope_coverage.py` — verifies inactivity coverage/actionability reflects inaccessible channel scope.
- `tests/test_member_reconciliation_authority.py` — verifies failed full fetch cannot mass-mark departures and authoritative sync still works.
- `tests/test_public_command_cleanup_contract_static.py` — protects canonical cleanup/hidden-command contract.
- `tests/test_public_command_count_docs_static.py` — locks the intentional 9-command public global count/list and docs.
- `tests/test_server_design_category_aware_auto_detect.py` — expands exact font, raw separator spacing, per-category, lock, uncertainty, determinism, preview, and keep-existing regressions.
- `tests/test_server_design_majority_layout_cleanup_static.py` — ensures duplicate stale design safety helpers cannot return.
- `tests/test_spam_guard_default_on_behavior.py` — covers brand-new, missing row, persisted ON, persisted OFF, DB read failure, and restart persistence.
- `tests/test_spam_guard_default_on_bootstrap_static.py` — audits runtime/setup default paths against the single authoritative ON policy.
- `tests/test_spam_guard_startup_state_labels.py` — locks the four required startup state labels.
- `tools/audit_dank_design_smart_auto_detect.py` — permanent source/flow audit for category-local design behavior, raw separator identity, determinism, duplicate cleanup, and native command path.
- `tools/audit_public_command_friction.py` — aligns public friction/cleanup audit with the canonical command contract.
- `tools/audit_public_command_surface.py` — permanent exact public surface and indirect-registration drift audit.
- `tools/test_inactive_members_truth_gate_static.py` — protects fail-closed inactivity truth/actionability behavior.

## Blockers

- No known technical blocker remains.
- Merge into `main` requires explicit user approval.
- Manual deployment is not authorized and has not occurred.

## Backlog

Locked. Do not switch to unrelated work until DS-STABILITY-001 is merged/closed or the user explicitly uses the required FORCE SWITCH format.

## Definition of Done

- [x] Dank Design uses category-local styles and does not flatten unrelated categories.
- [x] Exact supported font families are detected by regression coverage.
- [x] Separator symbol and raw spacing identity are preserved.
- [x] Saved channel/category/global lock precedence is protected.
- [x] Protected names and category-header preservation remain protected.
- [x] Uncertain local dimensions preserve current formatting.
- [x] `keep_existing` preserves an empty icon as empty.
- [x] Preview-first Smart Auto-Detect flow explains category-local decisions and safety skips.
- [x] SpamGuard defaults ON for new/missing-row guilds.
- [x] Explicit persisted SpamGuard OFF remains OFF.
- [x] SpamGuard persistence/error/default states are regression-tested.
- [x] Public command surface is intentionally defined, documented, and audited.
- [x] Advanced/internal commands cannot attach indirectly through public access-control imports.
- [x] Repetitive child-prune startup logging remains once-per-process from the existing PR #94 fix.
- [x] Activity permission gaps list exact affected channels/permissions.
- [x] Inactivity coverage does not claim full confidence when channel scope is incomplete.
- [x] Inactivity cleanup remains fail-closed on incomplete activity scope.
- [x] Failed/partial Discord member enumeration cannot mass-mark departures.
- [x] Temporary/debug/CI machinery is absent from the final PR diff.
- [x] `sitecustomize.py` and `usercustomize.py` retain legitimate runtime behavior and are unchanged by this PR.
- [x] Permanent CI workflow is clean and restored.
- [x] Final diff contains only mission-related permanent source, tests, audits, docs, CI, and task record changes.
- [x] Full required validation passed on the code head.
- [ ] Final task-record-only head CI must pass.
- [ ] Merge/deploy requires explicit user approval.
