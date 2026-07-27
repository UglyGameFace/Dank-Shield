from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")
PROFILE_COMMANDS = (ROOT / "stoney_verify/commands_ext/public_profile_cards.py").read_text(encoding="utf-8")
PROFILE_COMMANDS_CORE = (ROOT / "stoney_verify/commands_ext/public_profile_cards_core.py").read_text(encoding="utf-8")
PROFILE_RUNTIME = (ROOT / "stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
PROFILE_RUNTIME_CORE = (ROOT / "stoney_verify/profile_card_runtime_core.py").read_text(encoding="utf-8")
PROFILE_SERVICE = (ROOT / "stoney_verify/profile_card_service.py").read_text(encoding="utf-8")
PROFILE_SETUP = (ROOT / "stoney_verify/profile_card_setup_ui.py").read_text(encoding="utf-8")
SIGNATURE_RENDERER = (ROOT / "stoney_verify/profile_signature_live_renderer.py").read_text(encoding="utf-8")
SETUP_HOME = (ROOT / "stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
EXISTING_PROFILE = (ROOT / "stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")


def test_live_profile_cards_extend_the_existing_profile_group_only():
    assert "from .public_self_roles_group import profile_group" in PROFILE_COMMANDS
    assert "app_commands.Group(" not in PROFILE_COMMANDS
    assert "profile_group.add_command" in PROFILE_COMMANDS
    assert "remove_command(" not in PROFILE_COMMANDS
    assert "replace_command" not in PROFILE_COMMANDS
    assert 'name="profile"' in EXISTING_PROFILE


def test_registration_uses_commands_py_and_no_startup_guard():
    assert "register_public_profile_cards" in COMMANDS
    assert COMMANDS.count("register_public_profile_cards(bot,") == 2
    assert "startup_guards" not in PROFILE_COMMANDS
    assert "startup_guards" not in PROFILE_RUNTIME
    assert "monkey" not in PROFILE_COMMANDS.lower()
    assert "monkey" not in PROFILE_RUNTIME.lower()


def test_profile_runtime_registration_has_an_independent_failure_boundary():
    startup = COMMANDS.split("# Register split slash commands", 1)[1].split(
        "# Register centralized component interaction handler", 1
    )[0]
    assert startup.count("try:") >= 2
    assert "failed to register public profile cards" in startup

    extra = COMMANDS.split("def register_extra_commands", 1)[1].split(
        "# Events", 1
    )[0]
    assert "register_extra_commands profile cards failed" in extra


def test_profile_registration_preserves_existing_command_lifecycle_observers():
    for observer in (
        "async def on_guild_channel_create",
        "async def on_guild_channel_update",
        "async def on_thread_create",
        "async def on_thread_update",
    ):
        assert observer in COMMANDS
    assert '"register_extra_commands"' in COMMANDS
    assert '"handle_possible_submission"' in COMMANDS


def test_runtime_owns_exactly_one_additive_message_listener():
    assert PROFILE_COMMANDS.count('bot.add_listener(runtime.on_message, "on_message")') == 1
    assert "@bot.event" not in PROFILE_COMMANDS
    assert "@bot.event" not in PROFILE_RUNTIME
    assert "on_message" not in EXISTING_PROFILE


def test_live_card_never_edits_or_reposts_user_messages():
    assert ".edit(" not in PROFILE_RUNTIME_CORE
    assert "message.content" not in PROFILE_RUNTIME_CORE
    assert "await message.delete()" in PROFILE_RUNTIME_CORE
    assert "message.author" in PROFILE_RUNTIME_CORE
    assert "parse_live_card_footer(message)" in PROFILE_RUNTIME_CORE
    assert "allowed_mentions=discord.AllowedMentions.none()" in PROFILE_RUNTIME_CORE
    assert 'payload["file"] = rendered.file' in PROFILE_RUNTIME


def test_privacy_is_resolved_before_compact_signature_rendering():
    assert "get_effective_profile_settings" in PROFILE_RUNTIME
    assert "visible_platform_entries" in PROFILE_RUNTIME
    assert "require_live_enabled" in PROFILE_RUNTIME
    assert "server_allowed_fields" in PROFILE_RUNTIME
    assert "effective_preferences" in PROFILE_SERVICE
    assert "render_member_profile_signature" in PROFILE_RUNTIME
    assert "base = _profile_card(member)" not in PROFILE_RUNTIME
    assert "from .commands_ext.public_self_roles_group import _profile_card" not in PROFILE_RUNTIME


def test_all_existing_public_profile_entry_points_use_privacy_aware_composer():
    assert "async def send_privacy_aware_profile" in PROFILE_COMMANDS
    assert EXISTING_PROFILE.count("send_privacy_aware_profile") >= 4
    assert 'if suffix == "privacy"' in EXISTING_PROFILE
    assert EXISTING_PROFILE.count('label="Signature Settings"') >= 2
    assert "open_profile_signature_studio" in EXISTING_PROFILE
    assert "invalidate_member_live_cards" in EXISTING_PROFILE


def test_privacy_changes_remove_stale_cards_immediately():
    assert "async def remove_user_cards(" in PROFILE_RUNTIME_CORE
    assert "async def remove_user_cards_all_guilds(" in PROFILE_RUNTIME_CORE
    assert PROFILE_COMMANDS_CORE.count("invalidate_member_live_cards(") >= 3
    helper = PROFILE_RUNTIME_CORE.split("async def _remove_user_card_states", 1)[1].split(
        "async def remove_user_cards", 1
    )[0]
    assert "removed = await self._delete_stored_message" in helper
    assert "if not removed:" in helper
    assert helper.index("if not removed:") < helper.index(
        "await delete_live_card_state(state_guild_id, channel_id, resolved_user_id)"
    )


def test_server_field_restrictions_invalidate_every_existing_guild_card():
    assert "async def invalidate_guild_cards(" in PROFILE_RUNTIME_CORE
    live_fields = PROFILE_COMMANDS_CORE.split("async def profile_live_fields", 1)[1].split(
        "async def profile_live_status", 1
    )[0]
    assert "runtime.invalidate_guild_cards(guild)" in live_fields


def test_server_setup_uses_only_the_profile_signature_channel_picker():
    assert "open_profile_card_setup" in SETUP_HOME
    assert "class LiveProfileChannelSelect(discord.ui.ChannelSelect)" in PROFILE_SETUP
    assert "await _save_selected_channels(interaction, view, selected)" in PROFILE_SETUP
    assert "_core._SaveChannelsButton" not in PROFILE_SETUP
    assert "Enable Live Signatures" in PROFILE_SETUP
    assert "Disable Live Signatures" in PROFILE_SETUP
    assert "LIVE_CHANNEL_IDS_KEY" in PROFILE_SETUP
    assert "raw Discord ID" not in PROFILE_SETUP
    assert "Add Welcome Channel" not in PROFILE_SETUP
    assert "welcome_channel_id" not in PROFILE_SETUP
    assert "welcome/start-here messages" in PROFILE_SETUP


def test_signature_visual_is_legible_compact_and_shares_style_not_welcome_behavior():
    assert "SIGNATURE_WIDTH = 1080" in SIGNATURE_RENDERER
    assert "SIGNATURE_HEIGHT = 300" in SIGNATURE_RENDERER
    assert "render_profile_signature" in SIGNATURE_RENDERER
    assert "render_welcome_card" not in SIGNATURE_RENDERER
    assert "on_member_join" not in SIGNATURE_RENDERER
    assert "public_welcome_card" not in PROFILE_COMMANDS
    assert "Connected profiles" in PROFILE_RUNTIME
    assert "live_card_marker_url" in PROFILE_RUNTIME
