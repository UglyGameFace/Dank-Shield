# Dank Shield branch hygiene: reviewed historical retirement

Status: third-wave PR under review. This record belongs to the **single active GitHub branch-cleanup task**. It does not authorize merging incomplete product features or changing the Discloud database/application.

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
| #45 | `hotfix/quiet-member-update-modlog` | Hold for provenance and current runtime comparison |
| #114 | `feat/native-welcome-cards` | Superseded but stacked historical PR #113 changes; hold until preservation confirmed |
| #217 | `chore/finalize-ds-sec-045-record` | Hold pending task record supersession review |
| #219 | `chore/align-active-task-base-pr216` | Hold pending bookkeeping/history review |
| #231 | `chore/close-interaction-ownership-audit` | Hold pending bookkeeping/history review |
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

**Next step:** merge the reviewed workflow PR after CI; run `preview-reviewed` and inspect all 17 intended candidates before authorizing `delete-reviewed`. Then verify GitHub count, protected refs, and linked Discloud branch, and triage the remaining unfinished PRs before proposing any further destructive work.
