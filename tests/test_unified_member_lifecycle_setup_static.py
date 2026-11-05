from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
center = (ROOT / "stoney_verify/member_lifecycle_setup_center.py").read_text()
welcome = (ROOT / "stoney_verify/commands_ext/public_welcome_group.py").read_text()
logs = (ROOT / "stoney_verify/commands_ext/public_setup_logs.py").read_text()
router = (ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py").read_text()
setup = (ROOT / "stoney_verify/commands_ext/public_setup_recommend.py").read_text()


def test_single_existing_entrypoint() -> None:
    assert 'await open_member_lifecycle_setup(interaction)' in welcome
    assert 'name="join-leave"' in welcome
    assert "open_member_lifecycle_setup" in center
    assert "await open_member_lifecycle_setup(interaction)" in setup
    assert "await welcome_setup_ui.open_welcome_setup(interaction)" not in setup


def test_canonical_log_aliases_preserved() -> None:
    assert "_join_leave_alias_payload(str(channel.id))" in center
    assert "def _join_leave_alias_payload(" in logs
    assert "JOIN_LEAVE_KEYS" in center
    assert "JOIN_LEAVE_KEYS" in router


def test_permission_checked_before_write() -> None:
    assert "if not await _require_setup_permission(interaction)" in center
    assert "perms.view_channel, perms.send_messages, perms.embed_links" in center
    assert "invalidate_guild_config(int(guild.id))" in center


def test_separate_existing_engines_and_modlog() -> None:
    assert "WelcomeEventsCenterView(guild=guild, cfg=cfg)" in center
    assert "Staff Modlog remains independent" in center
    assert "Static Welcome Help" in center
    assert "a successful same-channel card suppresses the duplicate operational event" in center.lower()


if __name__ == "__main__":
    for test in (
        test_single_existing_entrypoint,
        test_canonical_log_aliases_preserved,
        test_permission_checked_before_write,
        test_separate_existing_engines_and_modlog,
    ):
        test()
        print("PASS", test.__name__)


def test_lifecycle_database_paths_acknowledge_before_io() -> None:
    # Slash entry, card navigation, log selection, and health all touch config.
    assert center.count("await interaction.response.defer(") >= 4
    assert "await interaction.edit_original_response(" in center
    assert "await interaction.response.edit_message(embed=_summary(guild, cfg)" not in center
