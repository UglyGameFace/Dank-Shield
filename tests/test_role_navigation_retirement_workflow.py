from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP = ROOT / ".github/workflows/retire-merged-temporary-branches.yml"


def test_historical_role_navigation_retirement_is_pinned_to_verified_replacement() -> None:
    workflow = CLEANUP.read_text(encoding="utf-8")
    assert "pull_number:809" in workflow
    assert "PR #809 role navigation replacement is not merged into main" in workflow
    assert "roleReplacement.merge_commit_sha" in workflow
    assert "base:roleReplacement.merge_commit_sha,head:currentMain" in workflow
    assert "fix/role-center-navigation-audit-20260929" in workflow
    assert "a9553b9a7cddfb34086970fb98bbf2d675601d2e" in workflow
    assert "tests/test_role_center_navigation_reconciliation.py" in workflow
    assert "public_community_pings.py" in workflow
    assert "public_member_setup.py" in workflow
    assert "archive mismatch" in workflow
    assert "lease.object.sha !== item.sha" in workflow
    assert "await github.rest.git.deleteRef({owner,repo,ref:'heads/' + item.name})" in workflow


def test_role_retirement_never_expands_general_automatic_cleanup_namespaces() -> None:
    workflow = CLEANUP.read_text(encoding="utf-8")
    assert "const rejectedHeads = reconciled ? [" in workflow
    assert "const token = (reconciled ? 'DELETE_RECONCILED_'" in workflow
    assert "currentMain !== mainSha || current.protected" in workflow
    assert "openPRs.some(pr => pr.head.ref === target.name" in workflow
    assert "configuredDeployBranch" in workflow
    assert "effectiveDeployBranch" in workflow
