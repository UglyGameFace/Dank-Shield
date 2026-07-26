from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP_UI = (ROOT / "stoney_verify/profile_card_setup_ui.py").read_text(encoding="utf-8")
SETUP_HOME = (ROOT / "stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
PROFILE_COMMANDS = (ROOT / "stoney_verify/commands_ext/public_profile_cards.py").read_text(encoding="utf-8")
WELCOME = (ROOT / "stoney_verify/commands_ext/public_welcome_group.py").read_text(encoding="utf-8")
JOIN_LEAVE = (ROOT / "stoney_verify/welcome_event_services.py").read_text(encoding="utf-8")


def test_canonical_setup_has_a_member_profile_feature_center():
    assert 'label="Member Profiles & Live Cards"' in SETUP_HOME
    assert "open_profile_card_setup" in SETUP_HOME
    assert "profile_card_setup_ui" in SETUP_HOME


def test_profile_setup_uses_a_real_multi_channel_picker_without_ids():
    assert "class LiveProfileChannelSelect(discord.ui.ChannelSelect)" in SETUP_UI
    assert "channel_types=[discord.ChannelType.text]" in SETUP_UI
    assert "max_values=_MAX_LIVE_CHANNELS" in SETUP_UI
    assert 'label="Save Selected Channels"' in SETUP_UI
    assert "raw Discord ID" not in SETUP_UI


def test_welcome_channel_can_be_staged_from_the_same_setup_panel():
    assert 'label="Add Welcome Channel"' in SETUP_UI
    assert 'config.get("welcome_channel_id")' in SETUP_UI
    assert "Press Save Selected Channels" in SETUP_UI
    assert "welcome/start-here channel" in SETUP_UI


def test_setup_refuses_bad_channel_permissions_before_enabling():
    for permission in (
        "View Channel",
        "Send Messages",
        "Embed Links",
        "Read Message History",
    ):
        assert permission in SETUP_UI
    save_block = SETUP_UI.split("class _SaveChannelsButton", 1)[1].split(
        "class _AddWelcomeChannelButton", 1
    )[0]
    assert "Fix these channel permissions before enabling live cards" in save_block
    assert "upsert_guild_config" in save_block
    assert save_block.index("Fix these channel permissions") < save_block.index(
        "upsert_guild_config"
    )


def test_setup_can_disable_and_clean_only_bot_owned_cards():
    assert 'label="Disable All"' in SETUP_UI
    assert "runtime.disable_channel" in SETUP_UI
    assert "runtime.reconcile()" in SETUP_UI
    assert "User messages are never edited, deleted, or reposted" in SETUP_UI


def test_live_profile_cards_are_explained_as_separate_from_all_welcome_features():
    assert "not** the static welcome/start-here message" in SETUP_UI
    assert "join-only welcome image" in SETUP_UI
    assert "join/leave announcement" in SETUP_UI
    assert "never posts just because someone joined" in SETUP_UI
    assert "not a join event" in SETUP_UI


def test_welcome_command_uses_explicit_join_leave_name_not_events():
    assert '@welcome_group.command(name="join-leave"' in WELCOME
    assert '@welcome_group.command(name="events"' not in WELCOME
    assert "Join & Leave Announcements" in JOIN_LEAVE
    assert "static welcome/start-here" in JOIN_LEAVE
    assert "live profile cards" in JOIN_LEAVE


def test_direct_profile_commands_remain_fallbacks_to_same_config():
    assert "profile_live_cards" in PROFILE_COMMANDS
    assert "LIVE_CHANNEL_IDS_KEY" in PROFILE_COMMANDS
    assert "upsert_guild_config" in PROFILE_COMMANDS
    assert "full picker is in /dank setup" in PROFILE_COMMANDS
