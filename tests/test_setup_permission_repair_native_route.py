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

    async def open_repair(
        interaction: Any,
        *,
        parent: str = "security",
    ) -> None:
        events.append((interaction, parent))

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(
        setup_permission_repair_services,
        "open_permission_repair",
        open_repair,
    )

    interaction = SimpleNamespace(guild=SimpleNamespace(id=123))
    asyncio.run(recommend._open_permission_repair(interaction))
    assert events == [(interaction, "security")]



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


def test_permission_repair_back_preserves_setup_parent(monkeypatch) -> None:
    events: list[str] = []

    async def security(_interaction: Any) -> None:
        events.append("security")

    async def logs(_interaction: Any) -> None:
        events.append("logs")

    monkeypatch.setattr(recommend, "_open_advanced_security", security)
    monkeypatch.setattr(recommend, "_open_advanced_logs_activity", logs)

    security_view = setup_permission_repair_services.PermissionRepairPreviewView(
        parent="security"
    )
    run_target = button(security_view, "Back").callback(SimpleNamespace())
    asyncio.run(run_target)

    logs_view = setup_permission_repair_services.PermissionRepairResultView(
        parent="logs"
    )
    run_target = button(logs_view, "Back").callback(SimpleNamespace())
    asyncio.run(run_target)

    assert events == ["security", "logs"]


def test_permission_repair_preview_actions_keep_parent(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    async def apply(_interaction: Any, *, parent: str = "security") -> None:
        events.append(("apply", parent))

    async def preview(_interaction: Any, *, parent: str = "security") -> None:
        events.append(("preview", parent))

    monkeypatch.setattr(setup_permission_repair_services, "apply_permission_repair", apply)
    monkeypatch.setattr(setup_permission_repair_services, "open_permission_repair", preview)

    view = setup_permission_repair_services.PermissionRepairPreviewView(parent="logs")
    asyncio.run(button(view, "Apply Safe Fixes").callback(SimpleNamespace()))
    asyncio.run(button(view, "Preview Again").callback(SimpleNamespace()))

    assert events == [("apply", "logs"), ("preview", "logs")]


def test_permission_repair_readiness_copy_uses_current_setup_language(monkeypatch) -> None:
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    monkeypatch.setattr(legacy, "_result_embed", lambda _result: discord.Embed())
    monkeypatch.setattr(
        legacy,
        "_line_list",
        lambda items, *, empty: "\n".join(items) or empty,
    )

    deep = SimpleNamespace(blockers=[], warnings=[], ok=[])
    embed = setup_permission_repair_services.result_embed({}, deep_audit=deep)
    rendered = "\n".join(
        [str(embed.description or "")]
        + [str(field.value) for field in embed.fields]
    )

    assert "Test Your Setup" in rendered
    assert "Test / Launch" not in rendered
    assert "Setup Plan & Server Items" in rendered
