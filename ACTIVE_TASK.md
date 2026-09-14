# ACTIVE TASK

## DS-AUD-009 — Release governance and production promotion safety

**Status:** IMPLEMENTED ON BRANCH — FINAL EXACT-HEAD REVALIDATION IN PROGRESS
**Branch:** `fix/release-governance-009`
**Base:** `a9d4a7bd83c773c9f0fa265d6169485ff1f5d17b`
**PR:** #212 (draft)

## Outcome

Make production-changing releases fail closed behind validated repository state instead of allowing production mutation to race, bypass CI, or promote stale main history.

## Scope

- GitHub release/merge governance that can be enforced from the repository.
- Production Supabase migration promotion and rollback/recovery documentation.
- Durable CI regression coverage for the production-promotion contract.
- Explicit documentation of the GitHub-hosted branch/ruleset control that must be enabled outside repository contents.

Application feature behavior, Discord UX, AntiNuke behavior, tickets, setup, and unrelated architectural cleanup are out of scope.

## Findings / root cause

- `main` is currently unprotected and the repository has no GitHub rulesets.
- The canonical `Dank Shield CI` workflow validates pull requests and pushes to `main`, but GitHub currently does not require that validation before `main` changes.
- `.github/workflows/deploy-supabase-migrations.yml` previously deployed production migrations directly on qualifying pushes to `main`.
- That migration workflow was independent of the `Dank Shield CI` result, so production schema mutation could begin before the same commit passed canonical CI.
- The migration deploy already used the GitHub `production` environment and a serialized concurrency group, but neither created a dependency on canonical CI.
- Existing schema-authority tests verified that production changes use the Supabase CLI, but did not verify the CI-before-production promotion relationship.
- The repository currently has no GitHub releases or tag refs, so release identity/rollback provenance was not established by tags/releases.
- Initial DS-AUD-009 implementation still left `workflow_dispatch` able to prove only main-history membership; review caught that manual recovery also needed exact canonical-CI proof to avoid becoming a bypass.
- A later final race review found that ancestor-only validation could allow an older successful `main` CI run to promote after a newer commit became canonical `main`. Production promotion must therefore require the exact current `main` head, not merely a valid ancestor.

## Execution path before repair

1. A change reached `main`.
2. `Dank Shield CI` started from the `push` event.
3. If `supabase/migrations/**` changed, `Deploy Supabase migrations` also started from the same `push` event.
4. The migration job could therefore reach `supabase db push` without first proving the canonical CI run for that exact `main` SHA succeeded.

## Implemented

- Rewired automatic production migration deployment to `workflow_run` after `Dank Shield CI` completes on `main`.
- Automatic promotion requires the canonical run conclusion to be `success`, the triggering event to be `push`, and the triggering branch to be `main`.
- Production checkout is pinned to the exact triggering `head_sha`.
- Before any production access, the workflow fetches canonical `main` and rejects the target unless it is still the exact current `origin/main` head; stale successful CI completions therefore fail closed.
- Manual recovery still exists, but now requires the full immutable current `main` SHA and queries GitHub Actions to prove a successful `Dank Shield CI` push run on `main` for that exact SHA.
- Added the minimum `actions: read` token permission needed for manual CI proof while retaining `contents: read`.
- Preserved the existing `production` environment, serialized migration concurrency, secret checks, migration status, dry-run preview, and Supabase CLI deployment ownership.
- Extended `tests/test_schema_authority.py` so direct-push promotion, missing current-head validation, stale ancestor promotion, missing exact-SHA checks, or a manual CI bypass fail regression coverage.
- Added `docs/RELEASE_GOVERNANCE.md` covering current-head exact-SHA release identity, canonical promotion order, stale-run rejection, forward-only migration correction, rollback compatibility, required release evidence, emergency rules, and the hosting-layer `main` ruleset requirement.
- Reused the existing production migration workflow rather than adding a second deployment owner.

## Validation / results

- Prior exact head `7dfff581972a249899badfaaae9e88e4062869e5` passed all six PR workflows, including full Dank Shield CI and Schema Authority SQL.
- That validation was intentionally superseded after final review found and repaired the stale-successful-CI promotion race.
- Final exact-head revalidation is required after the current-head hardening commits.

## Validation required

- Focused release-governance/schema-authority tests pass on the final exact head.
- Workflow YAML and shell logic are reviewed for automatic `workflow_run`, stale-run rejection, and manual-dispatch paths.
- Full final exact-head Dank Shield CI passes.
- Relevant final exact-head companion workflows pass, including Schema Authority SQL.
- Final diff contains only DS-AUD-009 release-governance work plus this task record.
- Current GitHub branch/ruleset state is re-checked before completion.
- After merge, the resulting `main` SHA must pass canonical CI before the new production-promotion workflow can proceed.

## Cleanup / conflicts

No conflicting release workflow implementation was found. The existing Supabase deploy workflow remains the sole production schema mutation owner. No runtime code or migration SQL is modified by DS-AUD-009.

## Blockers / risks

Repository contents cannot by themselves enable GitHub branch protection/rulesets. `main` must ultimately have a hosting-layer rule requiring pull requests and required checks; the connected GitHub surface currently exposes ruleset reads but no ruleset mutation action.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Suspended by explicit FORCE SWITCH after PR #211 merged and exact-head CI passed. Remaining acceptance evidence: after deployment, repeat the hostile/GANG-Nuker re-entry test and confirm no destructive action lands before the hostile identity/integration is removed. No additional AntiNuke investigation is part of DS-AUD-009.

## Next step

Validate the final PR #212 exact head, inspect any failing workflow at the exact failing step, then review the final diff and hosting-layer ruleset state before merge readiness.
