"""Safety gates for the deliberately reviewed Oct 22 zero-ahead branch batch."""
from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/guarded-merged-branch-cleanup.yml"


def _new_job() -> str:
    text = WORKFLOW.read_text()
    return text.split("  retire-reviewed-zero-ahead-20261022:", 1)[1]


def test_exact_22_ref_manifest_is_frozen_and_excludes_sensitive_refs():
    job = _new_job()
    manifest = job.split("const manifest = [", 1)[1].split("];", 1)[0]
    names = [
        line.strip().strip(",").strip("'")
        for line in manifest.splitlines()
        if line.strip().startswith("'")
    ]
    assert len(names) == len(set(names)) == 22
    assert "feat/dankshield-offline-discloud-rest-readiness-probe" in names
    assert "fix/offline-delta-activity-feed-trigger-replay" in names
    assert "feat/fresh-supabase-tickets-20261020" in names
    assert not any(
        n == "main" or n.startswith(("aaa-", "backup/", "archive/", "integration/", "recovery/"))
        for n in names
    )


def test_new_mode_owner_only_and_manual_with_separate_confirmation():
    full = WORKFLOW.read_text()
    job = _new_job()
    assert "  workflow_dispatch:" in full
    assert "          - preview-zero-ahead\n" in full
    assert "          - delete-zero-ahead\n" in full
    for event in ("push", "schedule", "workflow_run", "pull_request"):
        assert f"  {event}:" not in full
    assert "github.ref == 'refs/heads/main'" in job
    assert "github.actor == 'UglyGameFace'" in job
    assert "DELETE_VERIFIED_ZERO_AHEAD_BRANCHES" in job
    assert "mode === 'delete-zero-ahead'" in job
    assert "mode !== 'preview-zero-ahead'" in job
    assert "if (deleting) for (const branch of eligible)" in job


def test_must_prove_ancestry_and_no_live_pr_again_at_delete_time():
    job = _new_job()
    assert "item.protected || hasActivePR(name)" in job
    assert "c.ahead_by !== 0" in job
    assert "c.behind_by < 1" in job
    assert "c.status !== 'behind'" in job
    assert "c.head_commit?.sha" not in job
    assert "tipAfterCompare.commit.sha !== item.commit.sha" in job
    assert "tipAfterCompare.protected" in job
    assert job.count("await github.rest.repos.getBranch({") >= 3
    assert "if (mainNow !== mainSha)" in job
    assert "current.sha !== branch.sha" in job
    assert "stillOpen.data.some(" in job


def test_archive_exact_sha_before_branch_removal():
    job = _new_job()
    assert "branch-retirement-20261022/" in job
    assert "archive.object?.type !== 'commit'" in job
    assert "archive.object.sha !== branch.sha" in job
    assert job.index("await github.rest.git.createRef") < job.index(
        "await github.rest.git.deleteRef"
    )
    assert "ref: 'heads/' + branch.name" in job
    assert "core.setFailed('Partial retirement; inspect failures')" in job


def test_followup_requires_exact_merged_pr_sha_without_unfreezing_original_22():
    job = _new_job()
    assert "const reviewedFollowup = new Map([" in job
    assert "['fix/zero-ahead-retirement-compare-head-20261022', {" in job
    assert "sha: '94d087f5281a8738ba245732f17da877714985e4'" in job
    assert "mergedPr: 685" in job
    assert "if (item.commit.sha !== followup.sha) return null" in job
    assert "pr.merged_at" in job
    assert "pr.head?.ref !== name" in job
    assert "pr.head?.sha !== followup.sha" in job
    assert "pr.head?.repo?.full_name !== owner + '/' + repo" in job
    assert "pr.base?.ref !== 'main'" in job
    assert "[...manifest, ...reviewedFollowup.keys()]" in job
    assert "manifest.length !== 22" in job
    assert "reviewedFollowup.size !== 1" in job
    assert "manifest.includes(name)" in job
