"""Historical verification reminder branch retirement is evidence-gated.

The four post-#727 commits were superseded by merged #728. Current main
retains saved-role truth, full-roster previews, 25-person channel/DM batches,
and six-hour PostgreSQL cooldown without loosening the deployed SQL constraint.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()
RELAY = (ROOT / ".github/workflows/owner-reviewed-branch-retirement-command.yml").read_text()


def test_historical_manual_reminder_retirement_requires_proven_replacement():
    for required in (
        "fix/verify-manual-all-pending-20261025",
        "381dc5bab68f9a1589dac60be675ffeff162d993",
        "199de140f929de2c2de29d80e79800bbcd29346e",
        "pull_number:727",
        "pull_number:728",
        "2887dc757dd46516b872a93e92db58700b451290",
        "930f1796c3c1dd5d81d505f9ee4b4946de0952e7",
        "manualHistory.ahead_by !== 4",
        "manualHistory.behind_by !== 0",
        "manualAncestry.behind_by !== 0",
        "historicalPaths.slice().sort()",
    ):
        assert required in WORKFLOW


def test_manual_reminder_proof_preserves_canonical_role_truth_and_safe_budget():
    for required in (
        "member_is_pending_verification(member, config=cfg)",
        "if not 1 <= len(members) <= 25:",
        "least(public.dank_verify_manual_member_delivery.attempts + 1, 2)",
        "interval '6 hours'",
        "attempts between 1 and 2",
        "test_preview_scans_124_members_and_finds_all_24_with_custom_named_role",
        "test_manual_dm_sends_same_full_group_without_public_post",
        "test_manual_claim_parallel_shards_only_one_wins",
        "Manual reminder replacement evidence changed:",
    ):
        assert required in WORKFLOW


def test_manual_reminder_retirement_remains_owner_only_archive_first():
    section = WORKFLOW.split("// Explicit owner rejection:", 1)[1].split(
        "const allBranches = await", 1
    )[0]
    assert "if (!isManual || !ownerCommentRelay" in WORKFLOW
    assert "protectedHeads.has(target.name)" in section
    assert "currentMain !== mainSha" in section
    assert "current.commit.sha !== item.sha" in section
    assert "archive.object.sha !== item.sha" in section
    assert "lease.object.sha !== item.sha" in section
    assert section.index("github.rest.git.createRef") < section.index("github.rest.git.deleteRef")
    assert "DELETE_RECONCILED_" in WORKFLOW
    assert "'preview-reconciled','delete-reconciled'" in RELAY
