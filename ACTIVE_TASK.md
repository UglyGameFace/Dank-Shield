# ACTIVE TASK

## DS-STABILITY-001 — Finish Dank Shield public stability mission

**Status:** ACTIVE
**Branch:** `fix/finish-dank-shield-stability-mission`
**Base:** current `main`

### Scope
Finish the remaining production-stability mission without switching to unrelated work:

- Dank Design Smart Auto-Detect category-local correctness and cleanup.
- SpamGuard default-ON truth, persistence, diagnostics, and regression coverage.
- Canonical public Discord command surface and indirect-registration cleanup.
- Startup log de-duplication.
- Honest activity-tracking permission/coverage reporting and fail-closed inactivity safety.
- Safe member departure reconciliation that never treats a failed member fetch or partial cache as authoritative negative membership evidence.
- Removal of temporary/conflicting implementation debris while preserving legitimate runtime startup behavior.
- Full compile, test, standalone-tool, audit, CI, diff, and changed-file validation before merge approval.

### Findings
- PR #92 is already merged; its original branch is gone. Current `main`, not the old PR branch, is authoritative.
- PR #94 added useful SpamGuard/default and log-hygiene work, but the required behavioral persistence/error-state coverage is incomplete.
- PR #99 is a draft activity-access UI and is not merged; this mission will only retain changes that fit the final safety architecture.
- `public_access_control` imports advanced setup modules to monkey-patch their permission helper. Those imports execute module-level command attachment, explaining why commands reported as skipped can still be attached indirectly.
- `public_setup_review` attaches `/dank setup-review` and `/dank db-check` at import time.
- Public `/dank` allowlists drift between registration and pre-sync cleanup code.
- `server_design_majority_layout.py` contains duplicated repair-safety helper definitions; the later stale copy overrides an improved frame-matching implementation.
- Member sync/reconciliation falls back from a failed `fetch_members()` call to `guild.members` and then uses that potentially partial cache to mark absent database rows departed. This is unsafe negative membership evidence.
- Authoritative activity tracking already fails closed when its scope audit reports a gap, but the current scope audit returns only the first inaccessible channel and does not report exact missing permissions. Recent evidence coverage can also overstate channel coverage by filtering inaccessible channels before counting attempts.
- `sitecustomize.py`, `usercustomize.py`, and `.github/workflows/ci.yml` on current `main` contain legitimate permanent runtime/CI behavior and are not temporary PR #92 machinery.

### Changes
- Created the dedicated mission branch from current `main`.
- No production behavior changes committed yet beyond this active-task record.

### Validation
- Pending implementation.
- Required before completion: compile, focused Dank Design tests, full `pytest tests/`, every `tools/test_*.py`, public setup audit, public command/friction audit, invite permission audit, setup safety audit, role truth audit, event boundary audit, relevant Dank Design audits, `git diff --check`, GitHub Actions, and final changed-file/conflict inspection.

### Cleanup Status
- PR #92 final diff inspected: no temporary CI/debug/patch machinery remains there.
- Current main CI workflow inspected: permanent workflow only.
- Current `sitecustomize.py` and `usercustomize.py` inspected: legitimate runtime compatibility/safety behavior; do not alter for CI tricks.
- Duplicate/stale production helpers and indirect command-registration side effects still require cleanup.

### Blockers
- Local container cannot reach GitHub over DNS, so repository edits and CI validation must use the connected GitHub tooling rather than pretending local validation ran.

### Backlog
- Locked. Unrelated tasks are not accepted until DS-STABILITY-001 meets its Definition of Done or the user explicitly uses the required FORCE SWITCH format.

### Definition of Done
- [ ] Dank Design uses category-local styles, exact supported font families, raw separator-spacing identity, deterministic conservative inference, saved-lock precedence, protected-name safety, category-header preservation, exact `keep_existing`, and explanatory preview behavior.
- [ ] SpamGuard has one authoritative default truth; new/missing-row guilds default ON and bootstrap safely; explicit OFF persists; DB errors are distinct and never mistaken for missing rows; six required behavioral cases pass.
- [ ] Public global command surface is intentional, documented, and regression-tested; advanced/internal aliases cannot attach indirectly in public mode.
- [ ] Repetitive startup notices emit once per relevant phase/startup.
- [ ] Activity permission gaps list affected channels and exact missing permissions; coverage percentages account for inaccessible scope; inactivity cleanup remains fail-closed when coverage is incomplete.
- [ ] Failed Discord member enumeration and partial caches cannot mass-mark members departed; successful authoritative reconciliation remains intact.
- [ ] Temporary/debug/CI artifacts are absent; startup customization files and CI retain only legitimate permanent behavior.
- [ ] Full validation and GitHub Actions pass on the final clean branch head.
- [ ] Final diff audit lists every changed file and why it belongs.
- [ ] No merge or manual deploy occurs without explicit user approval.
