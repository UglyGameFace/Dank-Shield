from __future__ import annotations

import asyncio
from collections import Counter
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import setup_permission_repair_services
from stoney_verify.commands_ext import public_setup_recommend as recommend


def button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "label", "") or "") == label
    ]
    assert len(matches) == 1
    return matches[0]


def test_native_permission_repair_route_calls_owned_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[Any] = []

    async def allow(_interaction: Any) -> bool:
        return True

    async def open_repair(interaction: Any) -> None:
        events.append(interaction)

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(
        setup_permission_repair_services,
        "open_permission_repair",
        open_repair,
    )

    interaction = SimpleNamespace(guild=SimpleNamespace(id=123))
    asyncio.run(recommend._open_permission_repair(interaction))
    assert events == [interaction]


def test_security_button_uses_native_permission_repair_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def route(interaction: Any) -> None:
        events.append("repair")

    monkeypatch.setattr(recommend, "_open_permission_repair", route)
    view = recommend.AdvancedSecurityView()
    repair = button(view, "Fix Channel Permissions")

    assert repair.custom_id == "dank_setup_security:repair"
    assert repair.row == 1
    asyncio.run(repair.callback(SimpleNamespace()))
    assert events == ["repair"]


def test_security_and_logs_keep_access_check_distinct_from_repair() -> None:
    security = recommend.AdvancedSecurityView()
    logs = recommend.AdvancedLogsActivityView()

    assert button(security, "Check Bot Access").custom_id == "dank_setup_security:access"
    assert button(security, "Fix Channel Permissions").custom_id == "dank_setup_security:repair"
    assert button(logs, "Check Activity Access").custom_id == "dank_setup_logs:access"
    assert not any(
        str(getattr(child, "label", "") or "") == "Fix Channel Permissions"
        for child in logs.children
    )


def test_manage_setup_rows_are_discord_safe() -> None:
    for view in (
        recommend.ManageSetupView(),
        recommend.AdvancedSettingsHubView(),
        recommend.AdvancedSecurityView(),
        recommend.AdvancedLogsActivityView(),
    ):
        rows = Counter(int(getattr(child, "row", 0) or 0) for child in view.children)
        assert rows
        assert all(count <= 5 for count in rows.values())
        assert len(view.children) <= 25


def test_owned_permission_repair_service_remains() -> None:
    assert callable(setup_permission_repair_services.open_permission_repair)
    assert callable(setup_permission_repair_services.apply_permission_repair)
    assert callable(setup_permission_repair_services.result_embed)
