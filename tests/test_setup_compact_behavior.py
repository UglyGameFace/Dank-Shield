from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_setup_compact as compact
from stoney_verify.commands_ext import public_setup_gate as gate


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def field_names(embed: discord.Embed) -> list[str]:
    return [str(field.name) for field in embed.fields]


def test_required_tests_include_only_enabled_feature_flows() -> None:
    state = {
        "tickets": True,
        "verification": True,
        "basic_verify": True,
        "voice_verify": False,
        "id_verify": True,
        "spam_guard": False,
        "logs": True,
    }
    assert compact.required_test_keys(state) == (
        "tickets",
        "simple_verify",
        "id_verify",
        "logs",
    )


def test_generic_verification_test_is_used_when_no_specific_mode_is_known() -> None:
    assert compact.required_test_keys({"verification": True}) == ("verification",)


def test_compact_home_uses_one_status_field_and_one_direct_area_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def progress(guild: Any) -> tuple[str, int, int, str]:
        _ = guild
        return "✅ ready", 4, 4, "Open testing."

    class State:
        setup_choice = "custom_setup"
        setup_label = "Your features"
        completed = False

        @staticmethod
        def enabled_labels() -> list[str]:
            return ["Tickets", "Simple Verify", "Logs"]

    async def config(guild_id: int, refresh: bool = False) -> object:
        _ = guild_id, refresh
        return object()

    monkeypatch.setattr(compact, "_ORIGINAL_PROGRESS", progress)
    monkeypatch.setattr(compact, "get_guild_config", config)
    monkeypatch.setattr(compact, "service_state_from_config", lambda cfg: State())

    embed, view = run(compact._main_payload(SimpleNamespace(id=10)))

    assert embed.title == "🚀 Dank Shield Setup"
    assert field_names(embed) == ["Next"]
    assert embed.footer.text is None
    assert len(
        [child for child in view.children if isinstance(child, discord.ui.Select)]
    ) == 1
    assert "Test Features" in [
        child.label
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def test_health_check_hides_passing_wall_and_shows_only_next_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def old_health(guild: Any) -> discord.Embed:
        _ = guild
        embed = discord.Embed(title="Old")
        embed.add_field(
            name="Fix These First",
            value=(
                "🚫 Missing role\n"
                "🚫 Missing channel\n"
                "🚫 Missing permission\n"
                "🚫 Another blocker"
            ),
        )
        embed.add_field(
            name="Already Good",
            value="✅ One\n✅ Two\n✅ Three",
        )
        embed.add_field(
            name="Optional Later",
            value="⚠️ Optional style",
        )
        return embed

    async def target(guild: Any) -> tuple[str, str, str, str]:
        _ = guild
        return "roles", "Roles", "Fix roles", "roles"

    monkeypatch.setattr(compact, "_ORIGINAL_HEALTH", old_health)
    monkeypatch.setattr(compact.setup, "_guided_setup_target", target)

    embed = run(compact._health_embed(object()))

    assert embed.title == "🚫 Configuration Needs Attention"
    assert field_names(embed) == ["Fix next", "Optional later"]
    assert "Already Good" not in field_names(embed)
    assert "…and 1 more" in str(embed.fields[0].value)


def test_ready_configuration_check_does_not_claim_features_were_tested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def old_health(guild: Any) -> discord.Embed:
        _ = guild
        return discord.Embed(title="Old")

    async def target(guild: Any) -> tuple[str, str, str, str]:
        _ = guild
        return "ready", "Ready", "Ready", ""

    monkeypatch.setattr(compact, "_ORIGINAL_HEALTH", old_health)
    monkeypatch.setattr(compact.setup, "_guided_setup_target", target)

    embed = run(compact._health_embed(object()))

    assert embed.title == "✅ Configuration Check Passed"
    assert "does **not** claim" in str(embed.description)
    assert "Test Features" in str(embed.fields[0].value)


def test_ticket_menu_keeps_controls_but_removes_member_and_mapping_walls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {"name": "Verification", "is_default": False, "is_enabled": True},
        {"name": "General Support", "is_default": True, "is_enabled": True},
    ]
    original_view = SimpleNamespace(rows=rows, db_error="")

    async def old_payload(
        guild: Any,
        *,
        title: str,
    ) -> tuple[discord.Embed, Any]:
        _ = guild
        old = discord.Embed(title=title)
        old.add_field(name="Member Preview", value="very long")
        old.add_field(name="Saved Ticket Choices", value="very long")
        return old, original_view

    monkeypatch.setattr(compact, "_ORIGINAL_CATEGORY_PAYLOAD", old_payload)
    monkeypatch.setattr(
        compact.setup.solid,
        "_category_governance_text",
        lambda values: "✅ safe",
    )

    embed, view = run(
        compact._category_payload(object(), title="🎫 Ticket Menu")
    )

    assert view is original_view
    assert field_names(embed) == []
    assert "Choices enabled: **2**" in str(embed.description)
    assert "Default fallback: **General Support**" in str(embed.description)
    assert "Member Preview" not in field_names(embed)
    assert "Saved Ticket Choices" not in field_names(embed)


def test_setup_gate_activates_compact_patch_without_registering_another_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        compact,
        "apply_compact_setup_patch",
        lambda: calls.append("compact"),
    )
    monkeypatch.setattr(gate, "_patch_all", lambda: 4)

    gate.register_public_setup_gate(None, None)

    assert calls == ["compact"]
    assert gate._PATCHED is True


def test_finish_refuses_to_complete_until_all_enabled_tests_are_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=55),
        user=SimpleNamespace(id=99),
    )

    async def allowed(value: Any) -> bool:
        _ = value
        return True

    async def defer(value: Any) -> None:
        _ = value

    async def ready(value: Any) -> tuple[str, str, str, str]:
        _ = value
        return "ready", "Ready", "Ready", ""

    async def state(value: Any) -> dict[str, Any]:
        _ = value
        return {
            "tickets": True,
            "verification": False,
            "basic_verify": False,
            "voice_verify": False,
            "id_verify": False,
            "spam_guard": False,
            "logs": True,
            "completed": False,
        }

    async def render(
        value: Any,
        state_value: Any,
        confirmed_value: Any,
    ) -> None:
        _ = value, state_value, confirmed_value
        events.append("render")

    async def completed(guild_id: int, actor: Any) -> Any:
        _ = guild_id, actor
        events.append("complete")
        return SimpleNamespace(
            enabled_labels=lambda: ["Tickets", "Logs"]
        )

    async def edit(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        events.append("edit")

    monkeypatch.setattr(
        compact.setup.solid,
        "_require_setup_permission",
        allowed,
    )
    monkeypatch.setattr(
        compact.setup.solid,
        "_safe_defer_update",
        defer,
    )
    monkeypatch.setattr(compact.setup, "_guided_setup_target", ready)
    monkeypatch.setattr(compact, "_launch_state", state)
    monkeypatch.setattr(compact, "_render_tests", render)
    monkeypatch.setattr(compact, "mark_setup_completed", completed)
    monkeypatch.setattr(
        compact.setup.solid,
        "_edit_or_followup",
        edit,
    )

    run(compact._finish(interaction, {"tickets"}))
    assert events == ["render"]

    events.clear()
    run(compact._finish(interaction, {"tickets", "logs"}))
    assert events == ["complete", "edit"]
