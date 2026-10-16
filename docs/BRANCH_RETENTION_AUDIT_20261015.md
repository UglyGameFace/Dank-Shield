# Dank Shield branch hygiene: reviewed historical retirement

Status: fourth wave owner-executed and verified; fifth wave prepared for exact-head CI, manual owner preview, and separate owner-confirmed deletion. This record belongs to the **single active GitHub branch-cleanup task**. It does not authorize merging incomplete product features or changing the Discloud database/application.

## Verified checkpoint

- Original inventory: **517** after first cleanup PR branch was created.
- First owner-authorized run removed **222**, second removed **137**.
- After both executions, GitHub had **159 branches**: 74 exact-head merged into `main`, 8 exact-head merged into another base, 19 closed-but-unmerged PR heads, 15 changed-after-merge PR heads, 42 without a matching PR, and `main`.
- There were **no open PRs** at that checkpoint. "No open PR" is *not* proof a branch's work is complete.
- The reviewed retirement manifest in the existing `guarded-merged-branch-cleanup.yml` has **17 frozen branch/SHA pairs**: 13 old exact-head merged-to-`main` PRs and 4 closed PRs explicitly superseded by a merged PR or used only for validation.
- The workflow still requires an owner-dispatched **preview-reviewed**, then a separate **delete-reviewed** action with the exact confirmation. Merge does not delete branches. The actual run must recheck refs before deletion.

## Closed-but-unmerged PR disposition (19)

| PR | Branch | Retention decision |
| --- | --- | --- |
| #93 | `fix/startup-spamguard-command-activity-health` | Reviewed retirement; body identifies merged #94 as replacement |
| #95 | `fix/runtime-startup-hygiene-spamguard-default` | Reviewed retirement; body identifies merged #94 as replacement |
| #172 | `ci/ds-setup-020-macos-trigger` | Reviewed retirement; documented temporary validation for merged #171 |
| #173 | `ci/ds-setup-020-final-validation` | Reviewed retirement; documented temporary validation for merged #171 |
| #9 | `feature/setup-picker-wizard` | Reported superseded by `main`; hold pending file/scope comparison |
| #10 | `scale/production-readiness-10k` | Unsafe legacy monkey patches; **do not merge**; preserve until historical requirements are triaged |
| #45 | `hotfix/quiet-member-update-modlog` | Fourth-wave reviewed retirement: PR #46 merged the intended listener fix; PR #45 explicitly rejects its obsolete monkey patches |
| #114 | `feat/native-welcome-cards` | Superseded but stacked historical PR #113 changes; hold until preservation confirmed |
| #217 | `chore/finalize-ds-sec-045-record` | Fourth-wave reviewed retirement: bookkeeping-only closeout; functional implementation #214 merged |
| #219 | `chore/align-active-task-base-pr216` | Fourth-wave reviewed retirement: one-file bookkeeping alignment superseded by merged #216 |
| #231 | `chore/close-interaction-ownership-audit` | Fourth-wave reviewed retirement: bookkeeping-only closure; canonical implementation #229 merged |
| #237 | `chore/close-antinuke-authorized-bot-trust` | Explicitly suspended closure after ongoing AntiNuke failures; do not close task by deleting branch |
| #263 | `fix/welcome-card-long-tail-unicode` | Hold: validation incomplete for Unicode/font rendering |
| #285 | `fix/quiet-notice-auto-clear-recovery-20260921` | Hold: production quiet-notice behavior needs validation |
| #310 | `fix/ticket-panel-legacy-interaction-recovery-20260924` | Hold: exact live legacy panel path and replacement coverage need verification |
| #355 | `fix/live-captions-finalization-no-interrupt-20260927` | Hold: later merged #356/#357 exist, but direct equivalence not established |
| #458 | `feat/cinema-library-intelligence` | **Hold:** mixed branch is **five commits ahead** of `backup/cinema-library-standalone-interleaved-20261006`. Backup alone does *not* preserve entire head; clean #459 is merged but remaining commits need comparison |
| #544 | `fix/prod-shard-guild-loops-no-batching-20261013` | Hold: production-branch-specific backport; inspect deployment/reunification evidence |
| #555 | `fix/source-backup-large-toc-stream-20261013` | Hold: merged #557 fixed TOC pipe handling, but equivalence and source backup correctness remain unverified |

**Do not bulk-merge any of these branches to close them.** Closed/old does not imply their unique commits are wanted on the current `main`. Feature fixes are separate future implementation work and do not bypass single-task locking.

## Additional retained refs

- **Discloud selector:** `aaa-cinema-pre-pregateway-bind` is documented as the currently linked application branch. Keep until independent Discloud cutover verification.
- **Explicit recovery:** `backup/stoneyverify-stable-7fdc331-20261008` is a documented rollback checkpoint. Other `aaa-*`, `archive/*`, `backup/*`, `recovery/*`, `integration/*`, `reconcile/*` and deployment/migration branch refs remain outside this reviewed batch.
- **15 changed-after-merge heads:** do not prune merely from PR's merged status because the current head SHA differs from the merged PR head. Some branches are dozens of commits or more ahead of `main`.
- **42 no-PR refs:** do not infer disposability from their name or absence of a PR. Several are unique-commit histories or checkpoints.
- **Recent merged PR branches:** retain temporarily during Discloud/Android acceptance and database cutover. Their merged code in `main` does not itself prove production deployment.

## Validation / completion gate

- The reviewed manifest requires the exact documented head SHA; no glob or date-based widening.
- For the 13 merged entries, the exact PR head SHA and merged-into-`main` status are checked.
- For the 4 retired unmerged entries, the original PR must still be closed/unmerged and its documented successor PR must still be merged into `main`. This is **manual retirement**, not a generic rule for unmerged branches.
- Open PR references, protected refs, `main`, and changed SHAs are excluded, with a second head/protection check just before deletion.
- Preview and invalid-confirmation simulations must pass, and repo CI must be green; actual deletion and post-run counts remain to be verified separately.

**Historical third-wave instruction (completed):** This records the process for the earlier 17-candidate retirement. The current live checkpoint and next steps are in the latest section below.


## October 15 fourth-wave checkpoint (after PR #584 execution)

- GitHub branch listing now has **143** refs (517 original, 374 removed in three owner-executed waves).
- Full inventory matches: 62 exact-head merged into main, 8 exact-head merged into other bases, 15 closed-unmerged, 15 changed-after-merge, 42 with no PR and main.
- **Nine further exact-SHA branches** are manually reviewed for retirement using the existing `preview-reviewed`/`delete-reviewed` path. Prior 17 names stay recorded but no longer exist:
  - Exact-head PRs merged into main: #582, #583, #584, #293. Their review diffs and merge commits remain available.
  - Closed but unmerged: #45 (explicitly replaced by merged #46; discarded runtime monkey patches), #217 (bookkeeping-only, functional #214 merged), #219 (bookkeeping-only, #216 merged), #231 (bookkeeping-only, #229 merged). No product code is being merged as part of branch cleanup.
  - No-PR: `chore/close-release-governance-009` at `9112528e42e77ec348abe69d9207e37a64294380`, independently confirmed a Git ancestor of `main` with zero ahead commits and no matching PR.
- Every deletion requires branch head SHA to match the frozen manifest, non-protected ref, no open PR reference, appropriate closed/merged PR status and verified replacement, or real no-PR Git ancestry. GitHub rechecks SHA/protection immediately before deletion.
- The linked Discloud branch and all backups, recoveries, diverged branches, and outstanding product implementation branches remain excluded. Five production-base merged PR heads are ancestors of main, but their operational acceptance remains uncertain, so they are deliberately **not** in this wave.
- **PR #9** has a matching setup picker on current main but remains held pending deeper functional equivalence review. #10, #114, #237, #263, #285, #310, #355, #458, #544 and #555 are not silently retired. The oldest unmerged bookkeeping or topic branches were reviewed individually; no direct production code is changed.
- Operational acceptance: no new bot deploy, CI and actual deletion are separate checks. After this PR passes CI and merges, dispatch **preview-reviewed** first and inspect the nine candidate names, then separately **delete-reviewed** with `DELETE_REVIEWED_HISTORICAL_BRANCHES`. Recheck resulting branch count and all protected names.


## October 15 fifth-wave checkpoint (after PR #585 owner runs)

The last two owner-dispatched guarded workflow runs (on PR #585 merge SHA `f3c231e7d9b854263f6f5f427d1d1559a0288624`) succeeded. Exact GitHub listing after both: **135** remaining branches. All nine fourth-wave names were absent.

Remaining inventory after fourth-wave execution: 59 exact-tip merged-to-main PR refs, 8 exact-tip merged to another base, 11 closed-but-unmerged PR refs, 15 heads changed since merger, 41 refs with no matching PR, and main. This inventory is not a recommendation to bulk-delete 135 refs.

**Fifth wave is limited to four exact head/SHA pairs:**

| Branch | Merged PR | Basis for individually reviewed retirement |
| --- | ---: | --- |
| `chore/verify-supabase-sync` | #154 | July 31 temporary read-only history/dry-run verifier; merged to main, followed by removal PR #155 |
| `chore/remove-supabase-sync-verifier` | #155 | July 31 cleanup of that temporary workflow, already merged to main |
| `chore/verify-ticket-counter-production` | #159 | July 31 temporary read-only ticket-counter proof workflow, already merged to main |
| `chore/guarded-branch-cleanup-fourth-wave-20261015` | #585 | Fourth-wave owner cleanup already executed successfully on merged main, no longer a working change branch |

All four were confirmed on GitHub as **closed and merged into main with exact current head SHA unchanged** and no open PR references. These are retired branch refs only; the merged commits, PR records and migration/test history stay in GitHub. The script independently re-verifies before deletion.

A direct Git comparison shows some historical squash-merged verifier heads remain graph-diverged from main. Their exact GitHub merged-to-main PR record, frozen SHA and owner preview are therefore the evidence used for these four, not a false claim of Git ancestry.

**Explicit retention:** `aaa-cinema-pre-pregateway-bind`, `main`, backups, archive/recovery/reconcile/integration refs, current Discloud cutover and migrations, 11 closed-unmerged product PR branches, 15 modified-after-merge heads and no-PR diverged branches. Among remaining merged PR refs, schema migrations, AntiNuke recovery, Live Captions rollback, Invite Shield repair and critical Discloud recovery are retained for now pending operator acceptance. Two clean no-PR refs are already ancestors (`audit-000-cinema-pr436-known-good` and `fix/discloud-rest-window-recovery-current-main-20261007`) but are purposefully held as named known-good/recovery checkpoints.

**Gate and next action:** After fifth-wave PR exact-head CI is green, merge into `main`. Owner dispatches `preview-reviewed` to see exactly four candidates and zero deletions, inspects the list, and only then separately dispatches `delete-reviewed` with `DELETE_REVIEWED_HISTORICAL_BRANCHES`. Expect **132** branches after deleting four from the 135 checkpoint plus this PR branch (assuming no other branches are created). Verify current inventory and protected refs. No automatic cleanup, bot deploy, Discloud changes or database writes.
