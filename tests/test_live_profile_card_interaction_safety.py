from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = (ROOT / "stoney_verify/commands_ext/public_profile_cards.py").read_text(encoding="utf-8")
RUNTIME = (ROOT / "stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
COMMAND_REGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def _body(name: str, next_name: str) -> str:
    return COMMANDS.split(f"async def {name}", 1)[1].split(f"async def {next_name}", 1)[0]


def test_private_storage_commands_defer_before_database_io():
    assert "async def _defer_private" in COMMANDS
    assert "async def _send_private" in COMMANDS
    assert "await interaction.response.defer(ephemeral=True, thinking=True)" in COMMANDS
    assert "await interaction.response.defer()" in COMMANDS

    platform = _body("profile_platform", "profile_platform_remove")
    remove = _body("profile_platform_remove", "send_privacy_aware_profile")
    settings = _body("profile_settings", "profile_platform")
    public_view = _body("send_privacy_aware_profile", "profile_live_cards")
    assert platform.index("await _defer_private(interaction)") < platform.index("save_platform_identity(")
    assert remove.index("await _defer_private(interaction)") < remove.index("remove_platform_identity(")
    assert settings.index("await _defer_private(interaction)") < settings.index("_settings_payload(")
    assert public_view.index("await _defer_private(interaction)") < public_view.index("get_guild_config(")


def test_global_and_server_privacy_toggles_defer_then_refresh():
    global_toggle = COMMANDS.split("class _GlobalPrivacyToggleButton", 1)[1].split(
        "class _GuildPrivacyToggleButton", 1
    )[0]
    guild_toggle = COMMANDS.split("class _GuildPrivacyToggleButton", 1)[1].split(
        "class _PreviewProfileButton", 1
    )[0]
    refresh = COMMANDS.split("async def refresh", 1)[1].split(
        "class _GlobalPrivacyToggleButton", 1
    )[0]
    assert "upsert_profile_user_preferences(" in global_toggle
    assert "all_guilds=True" in global_toggle
    assert "upsert_profile_guild_settings(" in guild_toggle
    assert "None if hidden else False" in guild_toggle
    assert "await _defer_private(interaction, component_update=True)" in global_toggle
    assert "await _defer_private(interaction, component_update=True)" in guild_toggle
    assert "await interaction.edit_original_response(**payload)" in refresh


def test_settings_payload_reads_each_private_record_once():
    payload = COMMANDS.split("async def _settings_payload", 1)[1].split(
        "def _settings_embed", 1
    )[0]
    assert payload.count("get_profile_user(") == 1
    assert payload.count("get_profile_guild_settings(") == 1
    assert "get_effective_profile_settings(" not in payload
    assert "effective_preferences(" in payload


def test_global_user_cleanup_uses_indexed_persisted_state_query():
    all_guilds = RUNTIME.split("async def remove_user_cards_all_guilds", 1)[1].split(
        "async def _remove_channel_card_state", 1
    )[0]
    helper = RUNTIME.split("async def _remove_user_card_states", 1)[1].split(
        "async def remove_user_cards", 1
    )[0]
    assert "await self._remove_user_card_states(int(user_id))" in all_guilds
    assert "list_live_card_states_for_user(" in helper
    assert "list_live_card_states()" not in helper
    assert "for guild in" not in all_guilds
    assert "get_guild_config" not in helper


def test_public_dank_surface_allowlist_includes_profile_group():
    allowlist = COMMAND_REGISTRY.split("_ALLOWED_DANK_CHILDREN =", 1)[1].split(
        "_COMPACT_SUPPRESS_PREFIXES", 1
    )[0]
    assert '"profile"' in allowlist
