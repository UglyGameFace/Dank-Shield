"""Static guardrails for opt-in branch hygiene.

The historical owner's manual cleanup workflow is deliberately separate.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTO = ROOT / ".github/workflows/retire-merged-temporary-branches.yml"
GUARDED = ROOT / ".github/workflows/guarded-merged-branch-cleanup.yml"


def test_auto_retirement_only_opt_in_namespaces() -> None:
    workflow = AUTO.read_text(encoding="utf-8")
    assert "migration\\/dankshield-" in workflow
    assert "work\\/dankshield-" in workflow
    assert "'backup/'" not in workflow
    assert "'feat/'" not in workflow
    assert "'fix/'" not in workflow
    assert "github.rest.pulls.list" in workflow
    assert "pr.merged_at" in workflow
    assert "pr.head?.sha !== branch.commit.sha" in workflow
    assert "pr.base?.ref !== defaultBranch" in workflow
    assert "prs.length !== 1" in workflow
    assert "openPRs.length" in workflow
    assert "fresh.protected || fresh.commit.sha !== branch.commit.sha" in workflow
    assert "gitRef.object.sha !== branch.commit.sha" in workflow


def test_never_force_clean_unverified_or_prod_branches() -> None:
    workflow = AUTO.read_text(encoding="utf-8")
    assert "aaa-cinema-pre-pregateway-bind" in workflow
    assert "backup/stoneyverify-stable" in workflow
    assert "vars.DANK_DISCLOUD_DEPLOY_BRANCH" in workflow
    assert "if (candidates.length > 20)" in workflow
    assert "allBranches.length > 250" in workflow
    assert "after.length >= 75" in workflow
    assert "github.rest.git.deleteRef" in workflow
    assert "github.event.pull_request" not in workflow
    assert "inputs.mode" in workflow
    assert "REQUESTED_MODE" in workflow
    assert "context.actor !== 'UglyGameFace'" in workflow
    assert "cancel-in-progress: false" in workflow


def test_historical_owner_review_workflow_is_unchanged() -> None:
    workflow = GUARDED.read_text(encoding="utf-8")
    assert "DELETE_VERIFIED_NEXT_BATCH" in workflow
    assert "DELETE_REVIEWED_HISTORICAL_BRANCHES" in workflow
    assert "CLEANUP_CONFIRMATION" in workflow
