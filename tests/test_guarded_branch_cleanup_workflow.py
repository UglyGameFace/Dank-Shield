"""Only the canonical SHA-leased branch retirement workflow may delete refs.

Legacy owner-dispatched wave and final-pair implementations are retired; their
history remains available in Git, but their deletion authority must not return.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CANONICAL = WORKFLOWS / "retire-merged-temporary-branches.yml"
RELAY = WORKFLOWS / "owner-reviewed-branch-retirement-command.yml"


def test_legacy_deletion_workflows_are_absent() -> None:
    assert not (WORKFLOWS / "guarded-merged-branch-cleanup.yml").exists()
    assert not (WORKFLOWS / "guarded-final-two-branches.yml").exists()
    assert CANONICAL.exists()
    assert RELAY.exists()


def test_only_canonical_workflow_owns_deletion_and_archive() -> None:
    text = CANONICAL.read_text(encoding="utf-8")
    assert "  workflow_call:" in text
    assert "  workflow_dispatch:" in text
    assert "github.rest.git.deleteRef" in text
    assert "github.rest.git.createRef" in text
    assert "branch-retirement-reviewed-20261031/" in text
    assert "const token = 'DELETE_REVIEWED_' + fingerprint;" in text
    assert "currentMain !== mainSha" in text
    assert "current.commit.sha !== item.sha" in text
    assert "nowOpen.some(pr => pr.head.ref === item.name || pr.base.ref === item.name)" in text
    assert "archive.object.sha !== item.sha" in text
    assert "LINKED_PRODUCTION_BRANCH" in text
    assert "MANUAL_DEPLOY_ACK" in text


def test_owner_comment_is_relay_only_not_another_delete_engine() -> None:
    text = RELAY.read_text(encoding="utf-8")
    assert "github.actor == 'UglyGameFace'" in text
    assert "context.payload.issue?.number !== 788" in text
    assert "DANK_SHIELD_CLEANUP_V1 " in text
    assert "I_VERIFIED_LIVE_DISCLOUD_BRANCH" in text
    assert "uses: ./.github/workflows/retire-merged-temporary-branches.yml" in text
    assert "github.rest.git.deleteRef" not in text
    assert "github.rest.git.createRef" not in text


def test_current_setup_picker_preserves_closed_pr9_configuration_contract() -> None:
    # PR #9's old /stoney wizard is replaced by the current /dank setup picker.
    # Do not reintroduce the older command group or its duplicate registration.
    root = Path(__file__).resolve().parents[1]
    source = (root / "stoney_verify/commands_ext/public_setup_picker.py").read_text(
        encoding="utf-8"
    )
    command_modules = (root / "stoney_verify/commands_ext/__init__.py").read_text(
        encoding="utf-8"
    )
    assert '@dank_group.command(name="setup-picker"' in source
    assert '("public_setup_picker", "register_public_setup_picker_commands",' in command_modules
    assert "stoney_group.add_command" not in source
    for key in (
        "ticket_category_id", "ticket_archive_category_id", "staff_role_id",
        "vc_staff_role_id", "transcripts_channel_id",
        "verify_channel_id", "unverified_role_id", "verified_role_id",
        "resident_role_id", "vc_verify_channel_id", "vc_verify_queue_channel_id",
        "modlog_channel_id", "raidlog_channel_id", "join_log_channel_id",
        "force_verify_log_channel_id", "configured_by_id",
        "configured_by_name", "configured_at",
    ):
        assert f'"{key}"' in source
    for required in (
        "_require_setup_permission", "discord.ui.ChannelSelect",
        "discord.ui.RoleSelect", "_validate_ticket_setup",
        "_validate_verify_setup", "_validate_log_setup",
        "upsert_guild_config", "invalidate_guild_config",
        "class SetupPickerHome", "class TicketPicker",
        "class VerifyPicker", "class VCPicker", "class LogPicker",
    ):
        assert required in source
