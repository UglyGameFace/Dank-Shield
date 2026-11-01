# Owner-reviewed branch retirement from GitHub comments

The existing `.github/workflows/retire-merged-temporary-branches.yml` remains the sole automated deletion authority. The owner-comment relay invokes it as a reusable workflow with the authenticated owner comment context. This avoids a bot-owned workflow dispatch failing the existing owner-actor checks.

## Owner preview

Comment on merged PR #788 (https://github.com/UglyGameFace/Dank-Shield/pull/788) while signed in as UglyGameFace:

`DANK_SHIELD_CLEANUP_V1 {"mode":"preview-reviewed-merged"}`

Read the resulting Retire merged temporary branches workflow summary, review each branch SHA, and copy the exact DELETE_REVIEWED_... token. Preview is read-only.

## Owner deletion after live-ref verification

Independently check the ACTUAL live Discloud app GitHub linked branch. Do not infer it from main, old documentation, a guessed name, or a historical PR. Only then post another owner-authored PR #788 comment:

`DANK_SHIELD_CLEANUP_V1 {"mode":"delete-reviewed-merged","live_discloud_branch":"ACTUAL_VERIFIED_LIVE_BRANCH","live_discloud_branch_ack":"I_VERIFIED_LIVE_DISCLOUD_BRANCH","confirmation":"DELETE_REVIEWED_0123456789ABCDEF"}`

The branch and token in that sample are placeholders, not verified values. A valid token is bound to exact main, candidate branch names and SHA hashes, and the deployment ref. Moving main or candidate refs invalidates the token.

The existing target workflow checks the inherited owner comment, owner, actual branch-ref existence, configured-ref agreement, PR merge and exact head, 48-hour cooldown, squash/rebase file equivalence, open-PR dependencies, SHA leases and archive tags before deletion. Unintegrated work, current deployment, backups and rollback refs must remain preserved.

Never set DANK_DISCLOUD_DEPLOY_BRANCH to main just to bypass missing deployment evidence. A branch name is not independent proof of what Discloud runs.

Run preview/delete in small batches. Zero active branches besides main is a longer-term target only after all unique changes are integrated or archived under durable non-branch refs. Neither this relay nor the cleanup workflow deploys code, mutates the database, or changes credentials.
