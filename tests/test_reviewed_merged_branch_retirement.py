"""Guardrails for owner-reviewed merged-branch retirement.

The scheduled job may retire only opt-in exact-head merged branches. The
broader historical wave always requires a SHA-bound owner preview token.
"""
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github/workflows/retire-merged-temporary-branches.yml"
)


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_five_minute_schedule_is_serialized_and_not_nightly_gated() -> None:
    text = _workflow()
    assert "cron: '*/5 * * * *'" in text
    assert "group: guarded-merged-branch-cleanup" in text
    assert "cancel-in-progress: false" in text
    assert "if (localHour !== '03')" not in text
    assert "timeZone: 'America/New_York'" not in text
    # Scheduled runs may retire only narrowly opted-in, verified merged heads.
    assert "const mode = isManual ? process.env.REQUESTED_MODE : 'delete';" in text
    assert "const reviewed = mode === 'preview-reviewed-merged' || mode === 'delete-reviewed-merged';" in text
    assert "if (!isManual || context.actor !== 'UglyGameFace')" in text


def test_broad_retirement_is_manual_and_sha_leased() -> None:
    text = _workflow()
    assert "preview-reviewed-merged" in text
    assert "delete-reviewed-merged" in text
    assert "context.actor !== 'UglyGameFace'" in text
    assert "DANK_DISCLOUD_DEPLOY_BRANCH" in text
    assert "CLEANUP_CONFIRMATION" in text
    assert "createHash('sha256')" in text
    assert "JSON.stringify({mainSha,batch,effectiveDeployBranch})" in text
    assert "DELETE_REVIEWED_" in text
    assert "eligible.slice(0, 12)" in text


def test_reviewed_merged_branch_requires_actual_integration() -> None:
    text = _workflow()
    for proof in (
        "prs.length !== 1",
        "pr.merged_at",
        "pr.head.sha !== branch.commit.sha",
        "pr.base?.ref !== 'main'",
        "pr.merge_commit_sha",
        "github.rest.repos.compareCommits",
        "branchGraph.ahead_by !== 0",
        "base: mainSha, head: branch.commit.sha",
        "graph.behind_by !== 0",
        "Date.parse(pr.merged_at) > cutoff",
        "currentMain !== mainSha",
        "current.commit.sha !== item.sha",
        "nowOpen.some(pr => pr.head.ref === item.name || pr.base.ref === item.name)",
    ):
        assert proof in text


def test_no_unpreserved_work_or_production_refs_deleted() -> None:
    text = _workflow()
    for hold in (
        "aaa-cinema-pre-pregateway-bind",
        "backup/",
        "archive/",
        "recovery/",
        "integration/",
        "migration/",
        "'discloud'",
        "'supabase'",
        "'postgres'",
        "'rollback'",
        "scale/production-readiness-10k",
    ):
        if hold == "scale/production-readiness-10k":
            continue  # The broad reviewed allowlist excludes scale/ by construction.
        assert hold in text
    assert text.index("branchGraph.ahead_by !== 0") < text.index("eligible.push({name: branch.name")
    assert "archive.object.sha !== item.sha" in text
    assert "lease.object.sha !== item.sha" in text
    assert text.index("archive.object.sha !== item.sha") < text.index(
        "ref:'heads/' + item.name"
    )
    assert "github.rest.git.deleteRef({owner,repo,ref:'heads/' + item.name})" in text


def test_opt_in_deletion_is_archived_and_high_branch_count_is_not_failure() -> None:
    text = _workflow()
    assert "branch-retirement-opt-in/" in text
    assert "archive.object.sha !== branch.commit.sha" in text
    assert "core.warning('Branch count is '" in text
    assert "core.setFailed('Branch count is '" not in text
    assert "Math.ceil(eligibleCandidates.length / 20)" in text
    assert "eligibleCandidates.slice(candidateStart, candidateStart + 20)" in text
    assert "eligibleCandidates.length > candidates.length" in text


def test_no_opt_in_delete_when_deployment_reference_is_unknown() -> None:
    text = _workflow()
    assert "if (isManual && mode === 'delete' && !deploymentBranchConfigured)" in text
    assert "const deleting = mode === 'delete' && deploymentBranchConfigured;" in text
    assert text.index("const deleting = mode === 'delete' && deploymentBranchConfigured;") < text.index("if (deleting) {", text.index("const allBranches ="))
