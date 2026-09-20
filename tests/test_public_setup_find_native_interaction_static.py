from __future__ import annotations

from pathlib import Path

SETUP_FIND = Path("stoney_verify/commands_ext/public_setup_find.py").read_text(encoding="utf-8")


def _result_view_region() -> str:
    start = SETUP_FIND.index("class SetupSearchResultView")
    end = SETUP_FIND.index('@dank_group.command(name="setup-find"', start)
    return SETUP_FIND[start:end]


def test_setup_find_result_apply_uses_native_interaction_guard() -> None:
    region = _result_view_region()

    assert "from stoney_verify.interaction_guard import run_guarded_interaction, safe_send_interaction" in SETUP_FIND
    assert "async def apply(self, interaction: discord.Interaction, obj_id: int) -> None:" in region
    assert "async def action() -> None:" in region
    assert "await self._apply_result(interaction, obj_id)" in region
    assert "await run_guarded_interaction(" in region
    assert 'action_name="setup.find.apply"' in region
    assert "defer=True" in region
    assert "Setup value save stopped unexpectedly" in region
    assert "verify the current saved value before retrying" in region


def test_setup_find_result_write_stays_inside_guarded_action() -> None:
    region = _result_view_region()
    body_start = region.index("async def _apply_result")
    body = region[body_start:]

    for required in (
        "_resolve_object(guild, self.spec, obj_id)",
        "_validate_object(guild, self.spec, obj)",
        "upsert_guild_config(guild.id, _payload_for(interaction, self.spec, obj))",
        "invalidate_guild_config(guild.id)",
        "get_guild_config(guild.id, refresh=True)",
        "_config_embed(guild, cfg, title=f\"✅ Saved {self.spec.label}\")",
    ):
        assert required in body

    assert body.index("_validate_object") < body.index("upsert_guild_config")
    assert body.index("upsert_guild_config") < body.index("invalidate_guild_config")
    assert body.index("invalidate_guild_config") < body.index("get_guild_config")
    assert body.index("get_guild_config") < body.index("_config_embed")


def test_setup_find_post_defer_results_edit_original_response() -> None:
    region = _result_view_region()
    body = region[region.index("async def _apply_result"):]

    assert "interaction.response.edit_message" not in body
    assert body.count("await interaction.edit_original_response(") >= 3
    assert 'action_name="setup.find.apply.no_guild"' in body
    assert "await safe_send_interaction(" in body


def test_setup_find_validation_and_payload_semantics_remain_present() -> None:
    region = _result_view_region()
    body = region[region.index("async def _apply_result"):]

    assert "Setup Search Result Expired" in body
    assert "Setup Value Rejected" in body
    assert 'name="Blockers"' in body
    assert 'name="Warnings"' in body
    assert "_payload_for(interaction, self.spec, obj)" in body
    assert "Run /dank setup after setup changes to verify the full configuration." in body
