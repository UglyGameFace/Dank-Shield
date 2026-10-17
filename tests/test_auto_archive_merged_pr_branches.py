"""Safety contract for automatic archive-first lifecycle of merged PR heads.

This is deliberately separate from the owner-only historic branch sweeper.
Only recent, temporary merged PR heads can be automatically retired.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTO = ROOT / ".github/workflows/auto-archive-merged-pr-branches.yml"
MANUAL = ROOT / ".github/workflows/guarded-merged-branch-cleanup.yml"


def _auto() -> str:
    return AUTO.read_text(encoding="utf-8")


def test_automatic_cleanup_runs_only_after_main_push() -> None:
    text = _auto()
    assert "  push:\n    branches:\n      - main\n" in text
    assert "github.repository == 'UglyGameFace/Dank-Shield'" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "repoInfo.default_branch !== 'main'" in text
    assert "cancel-in-progress: false" in text
    assert "  contents: write" in text
    assert "  pull-requests: read" in text
    for trigger in ("pull_request:", "schedule:", "workflow_run:"):
        assert "\n  " + trigger not in text


def test_only_short_lived_merged_pr_heads_are_eligible() -> None:
    text = _auto()
    assert "mergedAfter = '2026-10-15T00:00:00Z'" in text
    assert "const allowedPrefixes = ['audit/', 'hotfix/', 'chore/']" in text
    assert "pr.merged_at < mergedAfter" in text
    assert "pr.base.ref !== 'main'" in text
    assert "pr.head.repo?.full_name !== fullName" in text
    assert "pr.head.repo?.id !== repoInfo.id" in text
    assert "pr.head.sha !== branch.commit.sha" in text
    assert "state: 'closed'" in text
    assert "sort: 'updated'" in text
    assert "per_page: 100" in text
    assert "const closedPRs = await github.paginate(" in text
    assert "github.rest.pulls.list, {" in text
    assert "const closedPRs = (await github.rest.pulls.list(" not in text


def test_active_pr_protected_branch_and_changed_head_fail_closed() -> None:
    text = _auto()
    assert "openHeads.has(name)" in text
    assert "branch.protected" in text
    assert "before.protected || before.commit.sha !== sha" in text
    assert "again.protected || again.commit.sha !== sha || nowInUse" in text
    assert "github.rest.repos.getBranch" in text
    assert "github.rest.pulls.list" in text
    assert "if (candidates.length > 12)" in text
    assert "Some branch retirements were held" in text


def test_archived_commit_is_verified_before_branch_removal() -> None:
    text = _auto()
    assert "branch-retirement-auto/" in text
    assert "github.rest.git.createRef" in text
    assert "github.rest.git.getRef" in text
    assert "archive.object.type !== 'commit'" in text
    assert "archive.object.sha !== sha" in text
    assert "github.rest.git.deleteRef" in text
    assert text.index("github.rest.git.createRef") < text.index(
        "archive.object.sha !== sha"
    ) < text.index("github.rest.git.deleteRef")
    assert "ref: 'heads/' + name" in text
    assert "ref: 'refs/' + ref, sha" in text
    assert "if (error.status !== 404) throw error" in text


def test_discloud_backup_and_sensitive_refs_never_auto_delete() -> None:
    text = _auto()
    for hold in (
        "aaa-cinema-pre-pregateway-bind",
        "backup/",
        "archive/",
        "recovery/",
        "reconcile/",
        "integration/",
        "release/",
        "deploy/",
        "staging/",
        "migration",
        "database",
        "supabase",
        "production",
        "snapshot",
        "stable",
        "audit/protection-picker-migration",
    ):
        assert hold in text
    assert "name.startsWith(prefix)" in text
    assert "protectedSegments.has(segment.toLowerCase())" in text
    assert "name === 'chore/auto-archive-merged-pr-heads-20261016'" in text


def test_historical_owner_reviewed_cleanup_stays_manual() -> None:
    text = MANUAL.read_text(encoding="utf-8")
    assert "  workflow_dispatch:" in text
    assert "DELETE_REVIEWED_HISTORICAL_BRANCHES" in text
    assert "github.actor == 'UglyGameFace'" in text
    for forbidden in ("\n  push:", "\n  pull_request:", "\n  schedule:"):
        assert forbidden not in text


def test_automatic_workflow_does_not_touch_discloud_or_bot_runtime() -> None:
    text = _auto()
    assert "github.rest.git.deleteRef" in text
    assert "stoneyverify" not in text
    assert "discloud.com" not in text
    assert "upsert_guild_config" not in text
    assert "git push" not in text
    assert "GITHUB_TOKEN" not in text  # github-script supplies scoped token
