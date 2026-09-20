from __future__ import annotations

from pathlib import Path

CENTER = Path("stoney_verify/commands_ext/public_verify_command_center.py").read_text(encoding="utf-8")


def _role_select_region() -> str:
    start = CENTER.index("class VerifyRoleSelect")
    end = CENTER.index("class VerifySingleRoleMapView", start)
    return CENTER[start:end]


def test_role_mapping_select_uses_native_interaction_guard() -> None:
    region = _role_select_region()

    assert "from stoney_verify.interaction_guard import run_guarded_interaction" in CENTER
    assert "from ..guild_config import get_guild_config, invalidate_guild_config" in CENTER
    assert "async def action() -> None:" in region
    assert "await self._save_mapping(interaction)" in region
    assert "await run_guarded_interaction(" in region
    assert "defer=True" in region
    assert 'action_name=f"verify.center.role_map.{self.parent_view.logical}"' in region
    assert "Verification role mapping stopped unexpectedly" in region
    assert "verify the currently saved role before retrying" in region


def test_role_mapping_preserves_staff_hierarchy_and_explicit_override_write() -> None:
    region = _role_select_region()
    body = region[region.index("async def _save_mapping"):]

    for required in (
        "if not isinstance(role, discord.Role):",
        "if not await _require_staff(interaction):",
        "_ROLE_CONFIG_KEYS",
        "_ROLE_LABELS",
        "_bot_can_manage_role",
        "_save_role_config",
        'source="verification center role mapping"',
        "explicit_override=True",
    ):
        assert required in body

    assert body.index("_require_staff") < body.index("_bot_can_manage_role")
    assert body.index("_bot_can_manage_role") < body.index("_save_role_config")


def test_role_mapping_verifies_persistence_before_success() -> None:
    region = _role_select_region()
    body = region[region.index("async def _save_mapping"):]

    assert "invalidate_guild_config(int(guild.id))" in body
    assert "await get_guild_config(int(guild.id), refresh=True)" in body
    assert "saved_role_id = _safe_int(_cfg_value(refreshed, config_key), 0)" in body
    assert "if saved_role_id != int(role.id):" in body
    assert 'raise RuntimeError("Verification role mapping could not be confirmed after save.")' in body
    assert 'await _private(interaction, f"✅ **{label}** now uses {role.mention}.")' in body

    save_at = body.index("await _save_role_config(")
    invalidate_at = body.index("invalidate_guild_config(int(guild.id))")
    refresh_at = body.index("await get_guild_config(")
    verify_at = body.index("if saved_role_id != int(role.id):")
    success_at = body.index('await _private(interaction, f"✅ **{label}** now uses {role.mention}.")')

    assert save_at < invalidate_at < refresh_at < verify_at < success_at


def test_role_mapping_does_not_send_success_on_unverified_save_path() -> None:
    region = _role_select_region()
    body = region[region.index("async def _save_mapping"):]

    verification = 'raise RuntimeError("Verification role mapping could not be confirmed after save.")'
    success = 'await _private(interaction, f"✅ **{label}** now uses {role.mention}.")'
    assert body.index(verification) < body.index(success)


def test_role_mapping_keeps_server_context_fail_closed() -> None:
    region = _role_select_region()
    body = region[region.index("async def _save_mapping"):]

    assert "guild = interaction.guild" in body
    assert "if guild is None:" in body
    assert 'raise RuntimeError("Verification role mapping requires a server context.")' in body


def test_role_mapping_drops_stale_cache_before_authoritative_read() -> None:
    region = _role_select_region()
    body = region[region.index("async def _save_mapping"):]

    invalidate = "invalidate_guild_config(int(guild.id))"
    refresh = "await get_guild_config(int(guild.id), refresh=True)"
    success = 'await _private(interaction, f"✅ **{label}** now uses {role.mention}.")'

    assert invalidate in body
    assert body.index(invalidate) < body.index(refresh) < body.index(success)
