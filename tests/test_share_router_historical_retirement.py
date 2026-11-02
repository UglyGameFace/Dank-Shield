"""Share Router historical branch may retire only after exact evidence readback.

PR #709 merged before five additional media commits landed on its old
branch. A merged PR alone does not prove those later commits were integrated.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP = (ROOT / ".github/workflows/retire-merged-temporary-branches.yml").read_text()


def test_share_router_historical_head_is_sha_pinned_and_owner_reviewed() -> None:
    assert "fix/share-router-existing-channel-nondestructive-20261024" in CLEANUP
    assert "b4a1ab8d68b2cf2e96aac9148b6cf04a400d4934" in CLEANUP
    assert "const rejectedHeads = reconciled ? [" in CLEANUP
    assert "pull_number:709" in CLEANUP
    assert "414b9188395c0366745bf868a8ceb6c105a001de" in CLEANUP
    assert "shareRouterAncestry.behind_by !== 0" in CLEANUP
    assert "laterMedia.behind_by !== 0 || laterMedia.ahead_by !== 5" in CLEANUP
    assert "if (!isManual || !ownerCommentRelay" in CLEANUP
    assert "DELETE_RECONCILED_" in CLEANUP


def test_share_router_retirement_checks_later_media_and_existing_channel_behavior() -> None:
    for required in (
        "stoney_verify/share_router_media_resolver.py",
        "tests/test_share_router_media_resolver.py",
        "Share Router media changes",
        "save_existing_share_route",
        "save_managed_share_route",
        "create_custom_share_source",
        "create_or_repair_hidden_share_hub",
        "Share Router existing-channel behavior diverged",
        "libtorrent==2.1.1",
        "test_plain_proxy_message_has_no_torrent_engine_or_route",
    ):
        assert required in CLEANUP
    assert "historical !== integrated" in CLEANUP
    assert "oldDeps.filter(dep => dep !== 'libtorrent==2.1.1')" in CLEANUP
    assert "newRuntime.includes('def _route_torrent_media(')" in CLEANUP


def test_share_router_retirement_preserves_archive_first_lease_checks() -> None:
    section = CLEANUP.split("// Explicit owner rejection:", 1)[1].split(
        "const allBranches = await", 1
    )[0]
    assert "currentMain !== mainSha" in section
    assert "current.commit.sha !== item.sha" in section
    assert "lease.object.sha !== item.sha" in section
    assert "archive.object.sha !== item.sha" in section
    assert "protectedHeads.has(target.name)" in section
    assert section.index("github.rest.git.createRef") < section.index(
        "github.rest.git.deleteRef"
    )
