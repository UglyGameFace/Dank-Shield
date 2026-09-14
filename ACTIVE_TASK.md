# ACTIVE TASK

## DS-AUD-009 — Release governance and production promotion safety

**Status:** IN PROGRESS — ROOT CAUSE CONFIRMED
**Branch:** `fix/release-governance-009`
**Base:** `a9d4a7bd83c773c9f0fa265d6169485ff1f5d17b`

## Outcome

Make production-changing releases fail closed behind validated repository state instead of allowing production mutation to race or bypass CI.

## Scope

- GitHub release/merge governance that can be enforced from the repository.
- Production Supabase migration promotion and rollback/recovery documentation.
- Durable CI regression coverage for the production-promotion contract.
- Explicit documentation of the GitHub-hosted branch/ruleset control that must be enabled outside repository contents.

Application feature behavior, Discord UX, AntiNuke behavior, tickets, setup, and unrelated architectural cleanup are out of scope.

## Findings / root cause

- `main` is currently unprotected and the repository has no GitHub rulesets.
- The canonical `Dank Shield CI` workflow validates pull requests and pushes to `main`, but GitHub currently does not require that validation before `main` changes.
- `.github/workflows/deploy-supabase-migrations.yml` deploys production migrations directly on qualifying pushes to `main`.
- That migration workflow is independent of the `Dank Shield CI` result, so production schema mutation can begin before the same commit has passed canonical CI.
- The migration deploy uses the GitHub `production` environment and a serialized concurrency group, which are useful controls, but neither currently creates a dependency on canonical CI.
- Existing schema-authority tests verify that production changes use the Supabase CLI, but they do not verify the CI-before-production promotion relationship.
- The repository currently has no GitHub releases or tag refs, so release identity/rollback provenance is not established by tags/releases today.

## Execution path

1. A change reaches `main`.
2. `Dank Shield CI` starts from the `push` event.
3. If `supabase/migrations/**` changed, `Deploy Supabase migrations` also starts from the same `push` event.
4. The migration job can therefore reach `supabase db push` without first proving the canonical CI run for that exact `main` SHA succeeded.

## Planned changes

- Rewire production migration deployment to run only after a successful `Dank Shield CI` completion for the exact `main` SHA, while retaining explicit manual dispatch for controlled recovery.
- Preserve production environment isolation, serialized deployment, dry-run preview, required-secret checks, and exact-SHA checkout.
- Add regression coverage that fails if production migration deployment regresses to direct `push` promotion or loses the exact CI-success gate.
- Update release documentation with the canonical promotion sequence, evidence required before release, and rollback/recovery boundaries.
- Record the repository-admin ruleset requirement precisely; do not pretend repository files can enforce a GitHub rule that is currently disabled at the hosting layer.

## Validation required

- Focused release-governance/schema-authority tests pass.
- Workflow YAML and shell logic are reviewed for `workflow_run` and manual-dispatch paths.
- Full exact-head Dank Shield CI passes.
- Final diff contains only DS-AUD-009 release-governance work plus this task record.
- Current GitHub branch/ruleset state is re-checked before completion.

## Cleanup / conflicts

No conflicting release workflow implementation has been found. The existing Supabase deploy workflow is the production schema mutation owner and will be repaired rather than duplicated.

## Blockers / risks

Repository contents cannot by themselves enable GitHub branch protection/rulesets. `main` must ultimately have a hosting-layer rule requiring pull requests and required checks; the connected GitHub surface currently exposes ruleset reads but no ruleset mutation action.

## Suspended task

### DS-SEC-044 — Hostile bot re-entry race and integration persistence

Suspended by explicit FORCE SWITCH after PR #211 merged and exact-head CI passed. Remaining acceptance evidence: after deployment, repeat the hostile/GANG-Nuker re-entry test and confirm no destructive action lands before the hostile identity/integration is removed. No additional AntiNuke investigation is part of DS-AUD-009.

## Next step

Repair the canonical Supabase production-promotion workflow and add regression coverage for the exact CI-success gate.
