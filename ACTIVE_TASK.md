# ACTIVE TASK

## DS-DEPLOY-001 — Supabase migration replay after schema-authority merge

**Status:** IMPLEMENTED ON BRANCH / VALIDATION PENDING
**Branch:** `fix/supabase-out-of-order-migrations`
**Base:** `29e8f3f23240bf4b587dff0fa6d11cfeca68c8b8` (`main`, merge of PR #205)

## Outcome

Production must apply all committed Supabase migrations introduced by PRs #205 and #206, including migrations whose timestamps sort before an already-applied later migration.

## Root cause

The production migration workflow linked to Supabase successfully and confirmed repository/remote migration state, but `supabase db push --dry-run` refused to continue because these two committed migrations are pending before the already-applied `20260913163000` migration:

- `20260913154500_canonical_runtime_schema_authority.sql`
- `20260913160000_reconcile_guild_member_role_state_constraint.sql`

Supabase explicitly requires `--include-all` for this valid out-of-order replay case. Because the dry-run failed, the apply step was skipped.

## Changes

- `.github/workflows/deploy-supabase-migrations.yml`
  - preview now runs `supabase db push --dry-run --include-all`;
  - apply now runs `supabase db push --include-all`.

## Safety / scope

- No migration SQL is being rewritten or reordered.
- Existing migration history remains authoritative.
- The workflow still performs a dry-run before any production apply.
- Required secrets, project linking, and migration-list inspection remain unchanged.
- No unrelated runtime or product code is modified.

## Validation evidence

Failed production run `34771377440` reached Supabase successfully, verified all required secrets, linked the production project, and listed migration history. It failed only at the preview step with Supabase's explicit instruction to rerun with `--include-all` for the two earlier pending migrations.

## Definition of done

- exact-head PR CI is green;
- merged workflow runs on `main`;
- production `Deploy Supabase migrations` completes successfully;
- remote migration status shows `20260913154500`, `20260913160000`, and `20260913163000` applied;
- no other open PRs remain from this repair;
- live Dank Shield deployment can then be adversarially retested against the known-hostile GANG Nuker re-entry scenario.

## Next step

Open a focused PR, validate the exact head, merge only after green, then confirm the production migration run succeeds before declaring the repair complete.
