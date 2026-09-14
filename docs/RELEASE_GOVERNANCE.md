# Dank Shield Release Governance

This document is the canonical production-promotion contract for Dank Shield.

## Release identity

The canonical release identity is the **full 40-character commit SHA in `main`** that passed the required checks. Branch names, local working trees, PR head names, Discord messages, and deployment timestamps are not release identities.

GitHub tags/releases may be added later for human-friendly versioning, but they do not replace the validated `main` SHA as the deployment authority.

## Canonical promotion sequence

Production-changing work follows this order:

1. Create a focused branch from current `main`.
2. Open a pull request into `main`.
3. Validate the exact PR head with the repository's required checks.
4. Review the final diff for unrelated, generated, secret-bearing, conflict, or temporary changes.
5. Merge the validated PR.
6. Let **Dank Shield CI** validate the resulting `main` SHA.
7. Only after that exact `main` CI run succeeds may production promotion run.
8. If `supabase/migrations/` contains pending migrations, the production migration workflow previews them and then applies them through the Supabase CLI.
9. Runtime/deployment acceptance evidence is recorded against the same `main` SHA.

A PR being green does not authorize production mutation by itself. The merged `main` SHA must also pass canonical CI because the merge result is the release candidate.

## Production database gate

`.github/workflows/deploy-supabase-migrations.yml` is the sole GitHub Actions owner for production Supabase migration application.

Automatic production migration runs must satisfy all of these conditions:

- Triggered only after the **Dank Shield CI** workflow completes on `main`.
- The triggering CI conclusion is `success`.
- The triggering CI event is a `push`, not a pull-request run.
- The migration workflow checks out the exact triggering `main` SHA.
- The target SHA is verified to belong to canonical `main` history.
- The `production` GitHub environment is used.
- Production migration runs remain serialized with `cancel-in-progress: false`.
- `supabase migration list` and `supabase db push --dry-run --include-all` run before the real `supabase db push --include-all`.

The deploy workflow intentionally runs after every successful canonical `main` CI completion. When no migrations are pending, the Supabase migration commands are a no-op. This is safer than guessing from only the last commit in a multi-commit push and accidentally missing a pending migration.

## Manual migration recovery

`workflow_dispatch` exists for controlled recovery, not as a CI bypass.

A manual run must use a **full 40-character SHA** that belongs to `main` history. Before dispatching it, verify that the chosen SHA has a successful canonical Dank Shield CI run.

Use manual dispatch only when the automatic post-CI promotion did not run, was interrupted, or must be safely replayed. Do not use it to deploy an unmerged branch, a failing commit, or an arbitrary local SHA.

## Database rollback boundary

Applied database migrations are treated as forward history.

- Do not edit, delete, rename, or reorder a migration that has already reached production.
- Do not rely on an automatic destructive down-migration path.
- If a schema change is wrong, create a new corrective migration and validate it through the normal PR/CI/promotion path.
- Application-code rollback is allowed only when the older code is compatible with the current production schema.
- For destructive or irreversible data changes, take/verify a recoverable database backup before promotion and document the recovery procedure in the PR.

This keeps code rollback and schema correction from becoming the same emergency operation.

## Required GitHub `main` ruleset

Repository contents cannot enforce GitHub's hosting-layer merge rules. The repository owner must configure a ruleset or branch protection targeting `main` with these minimum controls:

- Require changes to reach `main` through a pull request.
- Require all jobs from **Dank Shield CI** before merge:
  - `Python compile check`
  - `Claim-first ticket security`
  - `Managed category SQL smoke test`
- Require the branch to be current with `main` before merge when GitHub offers that option for the selected ruleset/check configuration.
- Block force pushes to `main`.
- Block deletion of `main`.

Dank Shield currently has a single-owner maintenance pattern. Do not configure a required-review count that makes the repository impossible for the owner to merge. The PR requirement and required checks provide the non-bypass path; human approvals can be increased when a second maintainer is available.

## Production environment controls

The GitHub `production` environment should be restricted to canonical production promotion. Prefer:

- production secrets stored on the `production` environment rather than broadly scoped repository secrets;
- deployment branch/tag policy restricted to `main` where available;
- required reviewer protection only when another maintainer can reliably approve without deadlocking emergency recovery;
- administrator bypass disabled once the production recovery process has been exercised and another safe recovery route exists.

Environment rules supplement the CI gate. They do not replace it.

## Required release evidence

For each production-impacting PR, retain enough evidence to answer exactly what reached production:

- PR number and title;
- exact PR head SHA that passed pre-merge validation;
- merged `main` SHA;
- canonical `main` CI result;
- production migration workflow result when migrations were pending;
- deployment/live acceptance result when runtime behavior changed;
- known limitations or deferred acceptance work.

## Emergency rule

Do not fix a production incident by pushing unreviewed code directly to `main` or by applying ad-hoc schema SQL outside the migration chain. Use a focused emergency branch/PR, run the same required checks, and promote the resulting `main` SHA through the same gate.

A faster emergency path may reduce review scope, but it does not remove CI, exact-SHA provenance, or the migration owner.
