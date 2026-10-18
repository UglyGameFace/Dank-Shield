from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "stoney_verify/commands_ext/public_cheers_owner.py").read_text(encoding="utf-8")
REGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def test_owner_only_cheers_commands_appear_in_public_command_profile():
    assert '("public_cheers_owner", "register_public_cheers_owner_commands",' in REGISTRY
    assert '"public_cheers_owner",' in REGISTRY
    for cmd in ("configure", "ping", "status", "disable"):
        assert f'@cheers_group.command(name="{cmd}"' in MODULE
    assert MODULE.count("if not await _only_app_owner(interaction):") == 4


def test_owner_not_role_admin_controls_the_moderation_gate():
    assert "await interaction.client.is_owner(interaction.user)" in MODULE
    assert "guild is None" in MODULE
    assert "Only the Dank Shield application owner" in MODULE
    assert "interaction.permissions.administrator" not in MODULE
    assert "manage_guild" not in MODULE


def test_role_mentions_are_explicit_and_exclude_everyone():
    assert "role.is_default() or role.managed" in MODULE
    assert "perms.send_messages and perms.embed_links" in MODULE
    assert "role.mentionable or perms.mention_everyone" in MODULE
    assert "discord.AllowedMentions(everyone=False, users=False, roles=[role])" in MODULE
    assert "LOBBY_URL" in MODULE
    assert "https://the420lobby.com/#spark-relay" in MODULE
    assert "/toke" in MODULE


def test_cooldown_is_persisted_per_guild_and_no_website_secrets():
    assert "upsert_guild_config" in MODULE
    assert 'KEY = "cheers_lobby_v1"' in MODULE
    assert "get_guild_config(guild.id, refresh=True)" in MODULE
    assert "_MIN_COOLDOWN = 15" in MODULE
    assert 'last_sent_at' in MODULE
    assert "if not await _save(guild.id, setting):" in MODULE
    for secret in ("DISCORD_TOKEN", "BOT_API_SHARED_SECRET", "DANK_CINEMA_"):
        assert secret not in MODULE


def test_member_recruitment_panel_persists_and_shares_owner_throttle():
    assert "class CheersCallView(discord.ui.View)" in MODULE
    assert "timeout=None" in MODULE
    assert 'custom_id="dank_shield:cheers:call_crew:v1"' in MODULE
    assert "_bot.add_view(CheersCallView())" in MODULE
    assert "_publish_panel(channel, previous)" in MODULE
    assert "panel_message_id" in MODULE
    assert "existing.edit(" in MODULE
    assert "discord.AllowedMentions.none()" in MODULE
    assert MODULE.count("await _deliver_recruitment_ping(") == 2
    assert "async with lock:" in MODULE
    assert "get_guild_config(guild.id, refresh=True)" in MODULE
    assert "Next CHEERS role ping available" in MODULE
    assert "await interaction.response.defer(ephemeral=True, thinking=True)" in MODULE
