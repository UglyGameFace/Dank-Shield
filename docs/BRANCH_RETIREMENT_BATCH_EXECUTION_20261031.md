# 2026-11-02: scheduled squash-merge archival proof

The historical scheduled cleanup previously skipped branches whose commit IDs remained unique after a squash/rebase merge. The owner-reviewed path already has a strict immutable merged-tree equivalence check; this integration reuses that same check for scheduled historical candidates **only after** exact merged PR head, base, age (48 hours), and merge-commit ancestry are verified. Changed-path blob SHA, type, and mode must match at the immutable PR merge commit; truncated/oversized or ambiguous deltas are held. Archive tags must match the original SHA before any retirement.

The deletion gate is unchanged: scheduled cleanup cannot delete while `DANK_DISCLOUD_DEPLOY_BRANCH` is unknown. It may create preservation tags after proving integration, and must not delete backups, rollback refs, production/deployment refs, active PR heads or unproven changes. Exact-main CI, independent off-Discloud backup, canary, rollback and explicit release approval remain required for production.

At inspection: main `be029a1d617f551f91457b47ae5eb29d59929843`; 52 live branches before the new working branch; 0 actual deletions in this integration run. A dedicated executable regression tests scheduled decision logic; CI/merge and live tag creation remain separately verifiable.

---

# Branch-retirement execution checkpoint (2026-10-31)

## Action results

- Repository: `UglyGameFace/Dank-Shield`.
- Main at inspection: `23e3e498b744ebe7842d6a6054526685ef61a50e` (PR #777 merge).
- Branches before this change: **120** (two pages of 100 + 20).
- Branches after opening this PR: **121**, including its new working branch. Net branch reduction: **0** (one temporary branch added).\n- Branches actually deleted by this checkpoint: **0**. No direct branch-ref deletion action is exposed by the connected GitHub tool; this PR does not pretend otherwise.
- A sample of 15 merged or integrated feature/fix branches compared against main had `ahead_by=0`, including `feat/action-center-4d2-ledger-readiness-20261023`, `feat/embed-studio-artwork-budgets-audit-20261024`, `fix/intelligence-ban-pagination-20261031`, and `fix/ticket-activity-honest-pagination-20261031`. This is **not** a full 120-branch proof.
- Exact-head archive tags were confirmed for `feat/action-center-4d2-ledger-readiness-20261023`, `feat/embed-studio-artwork-budgets-audit-20261024`, and `feat/community-lfg-persistent-public-panel-20261026`. Tags alone do not prove a branch was deleted.

## Root cause and implementation

The single scheduled/push cleanup workflow already archives verified zero-ahead, exact-head merged branches and may delete them **only when** the configured `DANK_DISCLOUD_DEPLOY_BRANCH` resolves. Its 75-branch check is a warning, **not** a deletion gate. The old `allBranches.length > 250` and `candidates.length > 20` exceptions, however, would stop safe opt-in cleanup entirely once a backlog grew.

This change retains the same eligibility, open-PR, commit-ancestry, archive-tag, exact-SHA, and verified-deployment-ref gates. It replaces size-triggered exceptions with a rotating **maximum 20 opt-in branches per run**, preserving the separate eight-per-run historical cap. Larger backlogs continue across scheduled runs rather than failing. No automatic deletion is authorized merely by branch count.

## Safety and next proof

Before reporting any actual deletions, inspect a successful scheduled run summary for exact before/after counts and deleted branch names, and re-list GitHub branches. The linked Discloud deployment ref has **not** been independently established in this run. If the repository variable is unset or invalid, the workflow remains archive-only; do not bypass this gate. Preserve backup, archive, recovery, production, database, protected, and active-PR refs. No Discloud deployment, credentials change, database migration, or production host-file edit is part of this PR.

Validation: targeted static workflow tests were updated; full GitHub CI and any deletion run must be verified separately. Exact-main CI, independent off-Discloud backup, approved deployment risk, canary and rollback are still required before a production release.

## November 1: owner-attested manual cleanup when repository variable is unset

At the start of this change, GitHub had **126** branches and main was
`2ac0e846efe15ea27515161fd4dd00fc3adcce91`. The new working branch
temporarily increases that count to 127; **no branches have been deleted**.

The scheduled/push cleanup is unchanged: it remains archive-only unless
`DANK_DISCLOUD_DEPLOY_BRANCH` resolves to a GitHub branch. To avoid forcing
the owner to change repository variables for a one-time retirement, the same
workflow now accepts an optional **owner-attested manual** live branch:

1. Independently inspect the existing Discloud application's currently linked
   GitHub branch. Do not infer it from main, a PR, or an old issue.
2. Dispatch **Retire merged temporary branches** with mode
   `preview-reviewed-merged`, `live_discloud_branch` set to that exact
   linked branch, and `live_discloud_branch_ack` set to
   `I_VERIFIED_LIVE_DISCLOUD_BRANCH`. Review the branch/SHA list and copy
   the exact `DELETE_REVIEWED_...` token from the run summary.
3. Dispatch mode `delete-reviewed-merged` with the **same** attested branch,
   acknowledgement, and preview token. If main, candidate SHAs, or the
   attested branch changes, the token or leases fail closed. Repeat bounded
   batches only after reviewing each preview.
4. Re-list branches and verify archive tags and exact deletions from the
   successful run. Keep every unique/unmerged, active PR, deployment, backup,
   recovery, database, and protected ref.

A manual branch input is an **owner attestation**, not independent proof of
the Discloud host's actual runtime SHA. If the configured repository variable
disagrees with the manual input, the workflow refuses to proceed. The
automatic path ignores manual inputs entirely. This change does not run a
deletion, change production configuration, or approve a Discloud release.

The reviewed deletion path additionally requires the exact branch head to have **zero unique commits ahead of current main**. A squash-merged PR is not enough by itself: a divergent branch remains held for content reconciliation, even if its merge commit is on main. This avoids retiring unique user commits merely because a PR was marked merged.
