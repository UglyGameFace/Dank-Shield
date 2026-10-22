# Active task: guarded retirement of remaining Dank Shield branches (2026-10-22)

## Outcome and scope
Reduce historical GitHub branch clutter without losing user work, live deployment fallbacks, or unique unmerged commits. Do not touch Discloud, database credentials, media runtime or Discord command behavior. Single active implementation task: branch hygiene; Discord compatibility improvements are backlog only.

## Current state and source of truth
- Main at audit: `c68547d9e81f13cb3954aec920149e975f2a362c`, following merged PR #685.
- 34 branches remained at audit: `main`, 9 branches with zero commits ahead of main, 24 diverged branches with unique commit history; no open PRs.
- Exact-head `fix/zero-ahead-retirement-compare-head-20261022` is 0 commits ahead, 1 behind, merged PR #685 tip `94d087f5281a8738ba245732f17da877714985e4`. This is the ONLY new deletion candidate in the current PR.
- Eight other ancestor-only refs include backup / known-good / recovery names; retain until each live deploy/rollback dependency and archive preservation are assessed. Unique-commit refs remain held; do not delete based on age or label.
- Example hold: `feat/cinema-rolling-supervised-worker-20261014` has one unique commit and introduces `stoney_verify/cinema_supervised_start.py`, which is absent from current main.
- The manual cleanup run #39801196415 succeeded on attempt 2 and retired the previously reviewed 22-ref batch. The scheduled ephemeral PR workflow also succeeded earlier.
- Prior recovery-drill failure was superseded: run #39721416434 passed isolated encrypted candidate PG17 backup, restore and record comparison. This does **not** authorize production cutover.

## Execution owner
Existing `.github/workflows/guarded-merged-branch-cleanup.yml` manual `preview-zero-ahead` / `delete-zero-ahead` job owns this retirement, not a new deletion tool, cron, or bot startup shim. It checks ancestry, main/tip stability, open PRs, exact owner and confirmation, then archives each verified exact SHA in a Git tag before deleting the branch ref.

## Changes in this task branch
- Preserve the original frozen 22-name manifest and add a separately pinned follow-up candidate for merged PR #685.
- Reconfirm exact merged PR identity and SHA on execution. All existing safety gates stay active.
- Add focused regression assertions for the extra candidate and original freeze.
- Record current audit and holds here. No branch has been deleted by this PR.

## Validation and completion gates
- **Pending:** focused tests, workflow static checks, CI and final changed-file/diff review.
- Merge only after checks pass, then the repository owner must dispatch `preview-zero-ahead` on `main`, review its eligible/held list, and separately dispatch `delete-zero-ahead` with existing exact confirmation `DELETE_VERIFIED_ZERO_AHEAD_BRANCHES`.
- Verify the tag SHA equals `94d087f5281a8738ba245732f17da877714985e4`, the reviewed fix branch is absent afterward, and the remaining branch count reflects exactly one deletion. Do not rerun a broad deletion mode on unreconciled heads.
- Continue one-by-one audit of diverged history afterward. Do not claim overall branch cleanup complete.

## Backlog
Discord compatibility tracker is scheduled weekly. Future bot/API compatibility changes wait until branch-retirement task is closed.

## Risks and blockers
Actions-dispatch and Git ref deletion are not available directly through the connected GitHub interface in this chat; owner-authorized manual workflow is the supported deletion path. Protected/runtime-linked and unmerged-content branches must not be removed without separate evidence. Recheck live state before destructive operations.
