"""Static guardrails for opt-in branch hygiene.

The broad reviewed path remains owner-only; scheduled retirement is limited to
exact-head merged PR branches already fully integrated into main.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTO = ROOT / ".github/workflows/retire-merged-temporary-branches.yml"
LEGACY = ROOT / ".github/workflows/guarded-merged-branch-cleanup.yml"


def test_auto_retirement_only_opt_in_namespaces() -> None:
    workflow = AUTO.read_text(encoding="utf-8")
    assert "migration\\/dankshield-" in workflow
    assert "work\\/dankshield-" in workflow
    # Broad squash/diverged history remains owner-reviewed. Automatic
    # historical retirement must prove zero unique commits first.
    assert "const eligible = [" in workflow
    assert "const reviewed = mode === 'preview-reviewed-merged'" in workflow
    assert "if (reviewed) {" in workflow
    assert "context.actor !== 'UglyGameFace'" in workflow
    assert "const prefixes = ['feat/', 'fix/'" in workflow
    assert "'backup/'" in workflow
    assert "github.rest.pulls.list" in workflow
    assert "pr.merged_at" in workflow
    assert "pr.head?.sha !== branch.commit.sha" in workflow
    assert "pr.base?.ref !== defaultBranch" in workflow
    assert "prs.length !== 1" in workflow
    assert "usedByOpenPR(openPRs)" in workflow
    assert "usedByOpenPR(latestOpenPRs)" in workflow
    assert "integrated.ahead_by !== 0" in workflow
    assert "merged.behind_by !== 0" in workflow
    assert "fresh.protected || fresh.commit.sha !== branch.commit.sha" in workflow
    assert "gitRef.object.sha !== branch.commit.sha" in workflow



def test_fully_integrated_historical_cleanup_is_archive_first_and_bounded() -> None:
    workflow = AUTO.read_text(encoding="utf-8")
    assert "cron: '*/5 * * * *'" in workflow
    assert "  push:\n    branches:\n      - main" in workflow
    assert "Math.floor(Date.now() / 300000)" in workflow
    assert "historical[(start + i) % historical.length]" in workflow
    assert "DANK_DISCLOUD_DEPLOY_BRANCH is verified" in workflow
    assert "const historicalPrefixes = ['feat/', 'fix/', 'hotfix/', 'extract/', 'docs/', 'audit/', 'chore/']" in workflow
    assert "const scanLimit = Math.min(24, historical.length)" in workflow
    assert "(integratedRemoved + archivedOnly) < 8" in workflow
    assert "graph.ahead_by !== 0" in workflow
    assert "['behind', 'identical'].includes(graph.status)" in workflow
    assert "prs.length !== 1" in workflow
    assert "pr.head?.sha !== sha" in workflow
    assert "Date.parse(pr.merged_at) > cutoff" in workflow
    assert "currentMain !== startMain" in workflow
    assert "mainLease !== startMain || lease.object.sha !== sha" in workflow
    assert "branch-retirement-integrated/" in workflow
    assert "archive.object.sha !== sha" in workflow
    assert workflow.index("archive.object.sha !== sha") < workflow.index(
        "await github.rest.git.deleteRef({owner, repo, ref: 'heads/' + name})"
    )
    assert "latestOpen.some(pr =>" in workflow
    for protected in ("'backup/'", "'recovery/'", "'integration/'",
                      "'migration/'", "'supabase'", "'postgres'",
                      "'rollback'", "'discloud'"):
        assert protected in workflow


def test_never_force_clean_unverified_or_prod_branches() -> None:
    workflow = AUTO.read_text(encoding="utf-8")
    assert "aaa-cinema-pre-pregateway-bind" in workflow
    assert "backup/stoneyverify-stable" in workflow
    assert "vars.DANK_DISCLOUD_DEPLOY_BRANCH" in workflow
    assert "Math.ceil(eligibleCandidates.length / 20)" in workflow
    assert "eligibleCandidates.slice(candidateStart, candidateStart + 20)" in workflow
    assert "eligibleCandidates.length > candidates.length" in workflow
    assert "throw new Error('More than 20 temporary branches" not in workflow
    assert "allBranches.length > 250" in workflow
    assert "core.warning('Large repository: processing only a bounded opt-in batch')" in workflow
    assert "throw new Error('Branch count too large" not in workflow
    assert "after.length >= 75" in workflow
    assert "github.rest.git.deleteRef" in workflow
    assert "github.event.pull_request" not in workflow
    assert "inputs.mode" in workflow
    assert "REQUESTED_MODE" in workflow
    assert "context.actor !== 'UglyGameFace'" in workflow
    assert "cancel-in-progress: false" in workflow


def test_historical_owner_review_uses_single_sha_bound_authority() -> None:
    assert not LEGACY.exists()
    workflow = AUTO.read_text(encoding="utf-8")
    assert "preview-reviewed-merged" in workflow
    assert "delete-reviewed-merged" in workflow
    assert "CLEANUP_CONFIRMATION" in workflow
    assert "branch-retirement-reviewed-20261031/" in workflow

def test_unverified_deployment_branch_allows_archival_but_never_deletion() -> None:
    workflow = AUTO.read_text(encoding="utf-8")
    assert "const canRetire = deploymentBranchConfigured;" in workflow
    assert "deploymentBranchConfigured = Boolean(deployed.commit?.sha);" in workflow
    assert "if (deleteReviewed && !deploymentBranchConfigured)" in workflow
    assert "if (!canRetire) {" in workflow
    assert "archivedOnly++;" in workflow
    assert "archiveCreated = true;" in workflow
    assert "if (archiveCreated) newArchives++;" in workflow
    assert "archived-only verified: " in workflow
    assert workflow.index("if (!canRetire) {", workflow.index("archive.object.sha !== sha")) < workflow.index(
        "await github.rest.git.deleteRef({owner, repo, ref: 'heads/' + name})"
    )


def test_owner_opt_in_cleanup_is_preview_bound_and_rechecks_main() -> None:
    workflow = AUTO.read_text(encoding="utf-8")
    assert "const optInPreviewMainSha =" in workflow
    assert "const optInBatch = candidates.map(branch =>" in workflow
    assert "mainSha: optInPreviewMainSha, batch: optInBatch, effectiveDeployBranch" in workflow
    assert "const optInToken = 'DELETE_OPT_IN_' + optInFingerprint;" in workflow
    assert "Opt-in retirement confirmation token:" in workflow
    assert "process.env.CLEANUP_CONFIRMATION !== optInToken" in workflow
    assert "if (isManual && optInMainSha !== optInPreviewMainSha)" in workflow
    assert workflow.index("process.env.CLEANUP_CONFIRMATION !== optInToken") < workflow.index(
        "await github.rest.git.deleteRef({ owner, repo, ref: 'heads/' + name })"
    )
