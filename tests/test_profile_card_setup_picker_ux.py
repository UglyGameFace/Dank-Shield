from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_UI = (ROOT / "stoney_verify/profile_card_setup_ui.py").read_text(encoding="utf-8")
SETUP_CORE = (ROOT / "stoney_verify/profile_card_setup_ui_core.py").read_text(encoding="utf-8")
SETUP_HOME = (ROOT / "stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
PROFILE_COMMANDS_CORE = (ROOT / "stoney_verify/commands_ext/public_profile_cards_core.py").read_text(encoding="utf-8")
SIGNATURE_RENDERER = (ROOT / "stoney_verify/profile_signature_renderer.py").read_text(encoding="utf-8")
WELCOME = (ROOT / "stoney_verify/commands_ext/public_welcome_group.py").read_text(encoding="utf-8")
JOIN_LEAVE = (ROOT / "stoney_verify/welcome_event_services.py").read_text(encoding="utf-8")


def test_canonical_setup_has_a_member_profile_feature_center():
    assert "open_profile_card_setup" in SETUP_HOME
    assert "profile_card_setup_ui" in SETUP_HOME
    assert 'title="🪪 Compact Profile Signatures"' in SETUP_UI


def test_profile_setup_uses_a_real_multi_channel_picker_without_ids():
    assert "LiveProfileChannelSelect = _core.LiveProfileChannelSelect" in SETUP_UI
    assert "class LiveProfileChannelSelect(discord.ui.ChannelSelect)" in SETUP_CORE
    assert "channel_types=[discord.ChannelType.text]" in SETUP_CORE
    assert "max_values=_MAX_LIVE_CHANNELS" in SETUP_CORE
    assert 'label="Save Selected Channels"' in SETUP_CORE
    assert "raw Discord ID" not in SETUP_UI


def test_welcome_configuration_is_not_mixed_into_profile_signatures():
    assert 'label="Add Welcome Channel"' not in SETUP_UI
    assert 'config.get("welcome_channel_id")' not in SETUP_UI
    assert "_AddWelcomeChannelButton" not in SETUP_UI
    assert "welcome/start-here messages" in SETUP_UI
    assert "controls only live profile signatures" in SETUP_UI


def test_setup_refuses_bad_channel_permissions_before_enabling():
    for permission in (
        "View Channel",
        "Send Messages",
        "Embed Links",
        "Read Message History",
    ):
        assert permission in SETUP_CORE
    save_block = SETUP_CORE.split("class _SaveChannelsButton", 1)[1].split(
        "class _AddWelcomeChannelButton", 1
    )[0]
    assert "Fix these channel permissions before enabling live cards" in save_block
    assert "upsert_guild_config" in save_block
    assert save_block.index("Fix these channel permissions") < save_block.index(
        "upsert_guild_config"
    )


def test_slow_setup_actions_acknowledge_before_database_or_render_work():
    preview = SETUP_UI.split("class _PreviewButton", 1)[1].split(
        "class ProfileCardSetupView", 1
    )[0]
    open_setup = SETUP_UI.split("async def open_profile_card_setup", 1)[1].split(
        "__all__", 1
    )[0]
    assert preview.index("await interaction.response.defer(ephemeral=True, thinking=True)") < preview.index(
        "get_guild_config("
    )
    assert "await interaction.edit_original_response(" in preview
    assert open_setup.index("await interaction.response.defer()") < open_setup.index(
        "get_guild_config("
    )


def test_setup_can_disable_and_clean_only_bot_owned_cards():
    assert 'label="Disable All"' in SETUP_CORE
    assert "runtime.disable_channel" in SETUP_CORE
    assert "runtime.reconcile()" in SETUP_CORE
    assert "never edits, deletes, copies, or reposts user messages" in SETUP_UI


def test_live_profile_signatures_are_visually_compact_and_separate_from_welcome():
    assert "SIGNATURE_WIDTH = 1080" in SIGNATURE_RENDERER
    assert "SIGNATURE_HEIGHT = 220" in SIGNATURE_RENDERER
    assert "small horizontal member signature" in SETUP_UI
    assert "join cards" in SETUP_UI
    assert "join/leave announcements" in SETUP_UI


def test_welcome_command_uses_explicit_join_leave_name_not_events():
    assert '@welcome_group.command(name="join-leave"' in WELCOME
    assert '@welcome_group.command(name="events"' not in WELCOME
    assert "Join & Leave Announcements" in JOIN_LEAVE
    assert "static welcome/start-here" in JOIN_LEAVE
    assert "live profile cards" in JOIN_LEAVE


def test_direct_profile_commands_remain_fallbacks_to_same_config():
    assert "profile_live_cards" in PROFILE_COMMANDS_CORE
    assert "LIVE_CHANNEL_IDS_KEY" in PROFILE_COMMANDS_CORE
    assert "upsert_guild_config" in PROFILE_COMMANDS_CORE
    assert "full picker is in /dank setup" in PROFILE_COMMANDS_CORE
