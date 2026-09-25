from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import security_stats
from stoney_verify.commands_ext import public_protection_center as protection
from stoney_verify.commands_ext import public_server_stats as stats_ui
from stoney_verify.commands_ext import public_command_surface_v2 as home_surface
from stoney_verify.commands_ext.public_command_surface_v2 import CompactDankHomeView
from stoney_verify.commands_ext.public_setup_group import dank_group


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def _custom_ids(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "custom_id", "") or "")
        for item in view.children
        if str(getattr(item, "custom_id", "") or "")
    }


def _item(view: discord.ui.View, custom_id: str):
    return next(
        item
        for item in view.children
        if str(getattr(item, "custom_id", "") or "") == custom_id
    )


def test_dank_home_has_first_class_server_stats_destination() -> None:
    assert "Server Stats" in _labels(CompactDankHomeView(1))


def test_server_stats_center_exposes_management_and_customization_controls() -> None:
    view = stats_ui.ServerStatsView(owner_id=1, cfg={})
    labels = _labels(view)
    assert {
        "Enable Stats",
        "Disable & Remove",
        "Refresh Now",
        "Design Sync: Off",
        "Category Name",
        "Numbers: Compact",
        "Placement: Top",
        "Reset Look",
        "Dank Shield Home",
        "Close",
    } <= labels

    assert {
        "dank_server_stats:visible",
        "dank_server_stats:label",
        "dank_server_stats:enable",
        "dank_server_stats:disable",
        "dank_server_stats:refresh",
        "dank_server_stats:design_sync",
        "dank_server_stats:category_name",
        "dank_server_stats:number_style",
        "dank_server_stats:placement",
        "dank_server_stats:reset",
        "dank_server_stats:home",
        "dank_server_stats:close",
    } <= _custom_ids(view)


def test_server_stats_center_offers_every_authoritative_counter() -> None:
    view = stats_ui.ServerStatsView(owner_id=1, cfg={})
    visible = _item(view, "dank_server_stats:visible")
    assert {str(option.value) for option in visible.options} == set(
        security_stats.DEFAULT_SECURITY_STATS_VISIBLE_KEYS
    )
    by_key = {str(option.value): option for option in visible.options}
    assert "GUILD_CREATE member_count" in str(by_key["members"].description)
    assert "ticket records" in str(by_key["open_tickets"].description)


def test_protection_legacy_stats_button_routes_to_canonical_stats_center(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls: list[object] = []
        guard_calls: list[tuple[str, bool]] = []

        async def fake_open(interaction) -> None:
            calls.append(interaction)

        async def fake_guard(
            interaction,
            action_name: str,
            action,
            *,
            defer: bool,
        ) -> None:
            guard_calls.append((action_name, defer))
            await action()

        monkeypatch.setattr(stats_ui, "open_server_stats_center", fake_open)
        monkeypatch.setattr(protection, "_guard_protection_action", fake_guard)

        view = protection.ProtectionCenterView(author_id=1, cfg={}, spam={})
        button = _item(view, "dank_protection:live_stats")
        interaction = SimpleNamespace(user=SimpleNamespace(id=1))

        assert str(button.label) == "Server Stats: SET UP"
        await button.callback(interaction)

        assert calls == [interaction]
        assert guard_calls == [("protection.server_stats", False)]

    asyncio.run(scenario())


def test_dank_home_server_stats_button_opens_canonical_center(monkeypatch) -> None:
    async def scenario() -> None:
        calls: list[object] = []

        async def fake_open(interaction) -> None:
            calls.append(interaction)

        monkeypatch.setattr(stats_ui, "open_server_stats_center", fake_open)

        view = home_surface.CompactDankHomeView(1)
        button = next(
            item
            for item in view.children
            if str(getattr(item, "label", "") or "") == "Server Stats"
        )
        interaction = SimpleNamespace(user=SimpleNamespace(id=1))

        await button.callback(interaction)

        assert calls == [interaction]

    asyncio.run(scenario())


def test_server_stats_home_destination_does_not_create_a_slash_child() -> None:
    assert "Server Stats" in _labels(CompactDankHomeView(1))
    assert dank_group.get_command("server-stats") is None


def test_category_name_modal_reuses_existing_interaction_panel(monkeypatch) -> None:
    async def scenario() -> None:
        events: list[tuple[str, object]] = []

        async def allow(_interaction) -> bool:
            return True

        async def save(guild_id: int, updates) -> dict:
            events.append(("save", (guild_id, dict(updates))))
            return dict(updates)

        async def get_cfg(guild_id: int, refresh: bool = False) -> dict:
            events.append(("get", (guild_id, refresh)))
            return {security_stats.SECURITY_STATS_ENABLED_KEY: False}

        async def render(interaction, *, content=None) -> None:
            events.append(("render", (interaction, content)))

        class Response:
            async def defer(self) -> None:
                events.append(("defer", None))

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1),
            guild=SimpleNamespace(id=77),
            response=Response(),
        )

        monkeypatch.setattr(stats_ui, "_require_setup_permission", allow)
        monkeypatch.setattr(stats_ui, "_save_preferences", save)
        monkeypatch.setattr(stats_ui, "get_guild_config", get_cfg)
        monkeypatch.setattr(stats_ui, "_render_center", render)

        modal = stats_ui.CategoryNameModal(current="Old Stats", owner_id=1)
        modal.category_name._value = "My Server Numbers"
        await modal.on_submit(interaction)

        assert events[0] == ("defer", None)
        assert events[1] == (
            "save",
            (
                77,
                {
                    security_stats.SECURITY_STATS_CATEGORY_NAME_KEY:
                    "My Server Numbers"
                },
            ),
        )
        assert events[-1][0] == "render"
        rendered_interaction, note = events[-1][1]
        assert rendered_interaction is interaction
        assert note == "✅ Server Stats category name saved."

    asyncio.run(scenario())


def test_stat_label_modal_reuses_existing_interaction_panel(monkeypatch) -> None:
    async def scenario() -> None:
        events: list[tuple[str, object]] = []

        async def allow(_interaction) -> bool:
            return True

        async def save(guild_id: int, updates) -> dict:
            events.append(("save", (guild_id, dict(updates))))
            return dict(updates)

        async def get_cfg(guild_id: int, refresh: bool = False) -> dict:
            events.append(("get", (guild_id, refresh)))
            return {security_stats.SECURITY_STATS_ENABLED_KEY: False}

        async def render(interaction, *, content=None) -> None:
            events.append(("render", (interaction, content)))

        class Response:
            async def defer(self) -> None:
                events.append(("defer", None))

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1),
            guild=SimpleNamespace(id=77),
            response=Response(),
        )

        monkeypatch.setattr(stats_ui, "_require_setup_permission", allow)
        monkeypatch.setattr(stats_ui, "_save_preferences", save)
        monkeypatch.setattr(stats_ui, "get_guild_config", get_cfg)
        monkeypatch.setattr(stats_ui, "_render_center", render)

        modal = stats_ui.StatLabelModal(
            key="members",
            current=security_stats.DEFAULT_SECURITY_STATS_LABELS["members"],
            owner_id=1,
        )
        modal.icon_text._value = "[👥]"
        modal.label_text._value = "Folks"
        modal.separator_text._value = ": "
        modal.value_template._value = "「{value}」"
        await modal.on_submit(interaction)

        save_events = [payload for name, payload in events if name == "save"]
        assert save_events == [
            (
                77,
                {
                    security_stats.SECURITY_STATS_FORMAT_OVERRIDES_KEY: {
                        "members": {
                            "icon": "[👥]",
                            "label": "Folks",
                            "separator": ": ",
                            "value_template": "「{value}」",
                            "custom_parts": ("icon", "label", "value_template"),
                        }
                    },
                    security_stats.SECURITY_STATS_CUSTOM_LABELS_KEY: {},
                },
            )
        ]
        assert events[0] == ("defer", None)
        assert events[-1][0] == "render"
        rendered_interaction, note = events[-1][1]
        assert rendered_interaction is interaction
        assert note == "✅ **Member count** format saved. Preview: `[👥] Folks: 「0」`"

    asyncio.run(scenario())

def test_counter_format_modal_exposes_each_editable_section() -> None:
    modal = stats_ui.StatLabelModal(
        key="open_tickets",
        current=security_stats.DEFAULT_SECURITY_STATS_LABELS["open_tickets"],
        owner_id=1,
    )
    assert [str(item.label) for item in modal.children] == [
        "Icon / prefix",
        "Label",
        "Label → value separator",
        "Value format — keep {value}",
    ]


def test_design_sync_button_reflects_saved_per_server_choice() -> None:
    view = stats_ui.ServerStatsView(
        owner_id=1,
        cfg={security_stats.SECURITY_STATS_INHERIT_DESIGN_KEY: True},
    )
    button = _item(view, "dank_server_stats:design_sync")
    assert str(button.label) == "Design Sync: On"
    assert button.style == discord.ButtonStyle.success
