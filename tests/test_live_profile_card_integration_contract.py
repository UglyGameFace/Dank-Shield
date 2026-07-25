from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")
PROFILE_COMMANDS = (ROOT / "stoney_verify/commands_ext/public_profile_cards.py").read_text(encoding="utf-8")
PROFILE_RUNTIME = (ROOT / "stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
PROFILE_SERVICE = (ROOT / "stoney_verify/profile_card_service.py").read_text(encoding="utf-8")
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


def test_runtime_owns_exactly_one_additive_message_listener():
    assert PROFILE_COMMANDS.count('bot.add_listener(runtime.on_message, "on_message")') == 1
    assert "@bot.event" not in PROFILE_COMMANDS
    assert "@bot.event" not in PROFILE_RUNTIME
    assert "on_message" not in EXISTING_PROFILE


def test_live_card_never_edits_or_reposts_user_messages():
    assert ".edit(" not in PROFILE_RUNTIME
    assert "message.content" not in PROFILE_RUNTIME
    assert "await message.delete()" in PROFILE_RUNTIME
    assert "message.author" in PROFILE_RUNTIME
    assert "parse_live_card_footer(message)" in PROFILE_RUNTIME
    assert "allowed_mentions=discord.AllowedMentions.none()" in PROFILE_RUNTIME


def test_privacy_is_resolved_in_service_layer_before_rendering():
    assert "get_effective_profile_settings" in PROFILE_RUNTIME
    assert "visible_platform_entries" in PROFILE_RUNTIME
    assert "require_live_enabled" in PROFILE_RUNTIME
    assert "server_allowed_fields" in PROFILE_RUNTIME
    assert "effective_preferences" in PROFILE_SERVICE


def test_all_existing_public_profile_entry_points_use_privacy_aware_composer():
    assert "async def send_privacy_aware_profile" in PROFILE_COMMANDS
    assert EXISTING_PROFILE.count("send_privacy_aware_profile") >= 4
    assert 'if suffix == "privacy"' in EXISTING_PROFILE
    assert EXISTING_PROFILE.count('label="Privacy & Platforms"') >= 2
    assert "invalidate_member_live_cards" in EXISTING_PROFILE


def test_privacy_changes_remove_stale_cards_immediately():
    assert "async def remove_user_cards(" in PROFILE_RUNTIME
    assert "async def remove_user_cards_all_guilds(" in PROFILE_RUNTIME
    assert PROFILE_COMMANDS.count("invalidate_member_live_cards(") >= 3
    assert "Failed Discord deletions keep" in PROFILE_RUNTIME


def test_server_field_restrictions_invalidate_every_existing_guild_card():
    assert "async def invalidate_guild_cards(" in PROFILE_RUNTIME
    live_fields = PROFILE_COMMANDS.split("async def profile_live_fields", 1)[1].split(
        "async def profile_live_status", 1
    )[0]
    assert "runtime.invalidate_guild_cards(guild)" in live_fields


def test_server_setup_uses_discord_channel_picker_and_existing_guild_config():
    assert "channel: discord.TextChannel" in PROFILE_COMMANDS
    assert "upsert_guild_config" in PROFILE_COMMANDS
    assert "get_guild_config" in PROFILE_COMMANDS
    assert "raw Discord ID" not in PROFILE_COMMANDS
    assert "profile_live_card_channel_ids" in PROFILE_RUNTIME


def test_join_only_welcome_card_files_are_not_profile_dependencies():
    assert "welcome_card" not in PROFILE_RUNTIME
    assert "welcome_card" not in PROFILE_SERVICE
    assert "public_welcome_card" not in PROFILE_COMMANDS
