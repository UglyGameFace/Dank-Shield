from __future__ import annotations

from pathlib import Path

PANEL = Path("stoney_verify/commands_ext/public_verify_basic_panel.py").read_text(encoding="utf-8")
CENTER = Path("stoney_verify/commands_ext/public_verify_command_center.py").read_text(encoding="utf-8")
SETUP_COMPACT = Path("stoney_verify/setup_ui/public_setup_compact.py").read_text(encoding="utf-8")
SETUP_RECOMMEND = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")


def _panel_region() -> str:
    start = PANEL.index("async def verify_panel")
    end = PANEL.index("def _attach", start)
    return PANEL[start:end]


def test_verify_panel_uses_native_guard_without_losing_thinking_defer() -> None:
    region = _panel_region()

    assert "from stoney_verify.interaction_guard import run_guarded_interaction" in PANEL
    assert "async def action() -> None:" in region
    assert "await _verify_panel_action(interaction)" in region
    assert "await run_guarded_interaction(" in region
    assert "defer=False" in region
    assert 'action_name="verify.panel.post"' in region
    assert "Verify Panel update stopped unexpectedly" in region
    assert "inspect the target channel before retrying" in region

    action = region[region.index("async def _verify_panel_action"):]
    assert "await interaction.response.defer(ephemeral=True, thinking=True)" in action
    assert action.index("await interaction.response.defer") < action.index("await _staff_only")
    assert action.index("await _staff_only") < action.index("await _pick_channel")
    assert action.index("await _pick_channel") < action.index("await post_basic_verify_panel")


def test_verify_panel_preserves_canonical_posting_and_disabled_mode_behavior() -> None:
    action = _panel_region()
    action = action[action.index("async def _verify_panel_action"):]

    for required in (
        "_install_basic_verify_runtime(getattr(interaction, \"client\", None))",
        "await post_basic_verify_panel(",
        'if result == "disabled":',
        "await get_guild_config(target.guild.id, refresh=True)",
        "basic_verify_disabled_reason(target.guild, cfg)",
        "Runtime handler ready.",
        "Runtime handler was not confirmed; restart the bot if the button still fails.",
        'await _send(interaction, f"Panel {result} in {target.mention}.{suffix}")',
    ):
        assert required in action


def test_verify_panel_no_longer_swallows_unexpected_posting_exceptions() -> None:
    action = _panel_region()
    action = action[action.index("async def _verify_panel_action"):]

    assert "except Exception as exc" not in action
    assert "Could not post panel:" not in action
    assert "post_basic_verify_panel" in action


def test_verify_panel_remains_the_shared_canonical_entrypoint() -> None:
    assert 'callback=verify_panel' in PANEL
    assert "await verify_panel(interaction)" in CENTER
    assert "await verify_panel(interaction)" in SETUP_COMPACT
    assert "await verify_panel(interaction)" in SETUP_RECOMMEND
