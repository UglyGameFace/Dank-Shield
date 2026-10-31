# Dank Shield Release Governance

This document is the canonical production-promotion contract for Dank Shield.

## Release identity

The canonical release identity is the **full 40-character commit SHA at the current head of `main`** that passed the required checks. Branch names, local working trees, PR head names, Discord messages, and deployment timestamps are not release identities.

GitHub tags/releases may be added later for human-friendly versioning, but they do not replace the validated current `main` SHA as the deployment authority.

## Canonical promotion sequence

Production-changing work follows this order:

1. Create a focused branch from current `main`.
2. Open a pull request into `main`.
3. Validate the exact PR head with the repository's required checks.
4. Review the final diff for unrelated, generated, secret-bearing, conflict, or temporary changes.
5. Merge the validated PR.
6. Let **Dank Shield CI** validate the resulting `main` SHA.
7. Only while that CI-green SHA is still the current `main` head may production promotion run.
8. Only after separate explicit owner approval, verified independent off-Discloud backup, tested rollback/canary plan, and a manual workflow dispatch, if `supabase_live/migrations/` contains pending migrations, the production migration workflow first checks the **live DankShield project ID**, imports only that project's already-applied migration history into an isolated temporary directory, then previews and applies only the reviewed new SQL through the Supabase CLI. The old `supabase/migrations/` folder is retained strictly as historical old-project source and must never be pushed to live DankShield.
9. Runtime/deployment acceptance evidence is recorded against the same `main` SHA.

A PR being green does not authorize production mutation by itself. The merged `main` SHA must also pass canonical CI because the merge result is the release candidate. A successful CI result also expires for promotion if a newer commit becomes the canonical `main` head before the deployment job reaches its gate.

## Production database gate

`.github/workflows/deploy-supabase-migrations.yml` is the sole GitHub Actions owner for production Supabase migration application.

Production migration runs are **manual-only**. Passing CI, merging a PR, or an unrelated push cannot authorize database mutation. The owner must separately approve the risk and attest to an independently verified off-Discloud backup and a tested rollback/canary plan. The workflow checks these exact manual input tokens before checkout or Supabase authentication:

- `approval`: `APPROVE_CURRENT_MAIN_SUPABASE_MIGRATION`
- `backup_verified`: `VERIFIED_OFF_DISCLOUD_BACKUP`
- `rollback_tested`: `ROLLBACK_TESTED`

These attestations are **not** proof of backup integrity, live canary completion, or host-file recovery. Operators must independently verify and retain evidence outside the workflow before dispatch. No Discloud deployment is performed by this workflow.

The migration job additionally requires:

- The **full 40-character SHA** of the current `main` head, with a successful canonical **Dank Shield CI** push run for that exact SHA. It rejects stale or failing releases.
- Serialized execution in the `production` GitHub environment with `cancel-in-progress: false`.
- `SUPABASE_PROJECT_ID` equal to dedicated DankShield project `viamriafkbqhquvrsnua`; any other project fails before link or SQL.
- A throwaway CLI directory seeded only from the dedicated live project's already-applied migrations, not the old IdleGrowOp/Supabase history.
- The immutable 27-version baseline and three reviewed compact-RPC versions, with unknown/missing remote history rejected.
- A migration list and `supabase db push --dry-run --include-all` before any actual `supabase db push --include-all`.

## Manual migration release

Open **Deploy Supabase migrations** using `workflow_dispatch` only after reviewing the pending SQL and recording the independent backup, recovery rehearsal, and canary plan. Supply the exact current-main SHA and all three confirmations. The workflow independently verifies successful canonical CI and target identity. Never use this to replay old-project migrations, bypass release risk approval, or assume the bot was deployed to Discloud.

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

Do not fix a production incident by pushing unreviewed code directly to `main` or by applying ad-hoc schema SQL outside the migration chain. Use a focused emergency branch/PR, run the same required checks, and promote the resulting current `main` SHA through the same gate.

A faster emergency path may reduce review scope, but it does not remove CI, exact-SHA provenance, current-head validation, or the migration owner.


## Former Supabase project's migration boundary (2026-10-22)

The Discloud app `stoneyverify` now uses the dedicated DankShield Supabase project
`viamriafkbqhquvrsnua`. The old project `hyncpbvqyjojxhjzcdzg`
still exists and retains historic data; **do not delete it, migrate its data,
or replay its migrations** as part of this GitHub workflow correction.

The previous GitHub deployment secret pointed at the old project and reported
a successful schema deployment there, even while the live bot continued to
receive missing-table errors from the new project. Both 4:20 migrations were
subsequently applied and verified on the live project, under their actual remote
migration versions `20261023014508` and `20261023014518`. These versions
are part of the immutable checked baseline.

After merging the workflow fix, the repo owner must set the **GitHub Actions
`production` environment** values to the actual live project credentials:
`SUPABASE_PROJECT_ID` exactly `viamriafkbqhquvrsnua`,
`SUPABASE_DB_PASSWORD` for that project, and a
`SUPABASE_ACCESS_TOKEN` authorized on the Dank Shield organization.
If the secrets are repository-level rather than environment-level, update
those instead; an environment secret with the same name can override a
repository secret. Never paste keys into PRs, commit files, or the chat.

Code guardrails prevent any old-project write even if the secret is accidentally
left pointing to the retired project. CI history and live migration history
must pass their independently enforced checks before automatic SQL promotion.
The old project may later be retired separately after all runtime consumers and
rollback dependencies have been audited.

## October 31 migration-history and authentication status

The dedicated project's connected migration-history listing contains the original
27 baseline entries plus the three compact-RPC entries from October 28.
Those three were approved for recognition in the offline history verifier,
not for SQL replay. The original baseline remains unchanged. Remote SQL bytes
for the three approved versions have not been compared with repository source.
The October 30 manual advert, Atlas and heartbeat repairs need separate
history reconciliation and are not silently marked applied by this approval.

GitHub Actions migration run 41107334032 failed after successful project
identity and CLI link checks: the fetch step received PostgreSQL 28P01.
The owner must correct the dedicated project's SUPABASE_DB_PASSWORD in the
GitHub production environment (which overrides same-named repository secrets).
No password or other credential belongs in this document or chat. No migration
promotion or Discloud deployment is authorized by this reconciliation.
