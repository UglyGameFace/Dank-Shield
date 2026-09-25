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
        include_activity_coverage: bool = False,
    ) -> None:
        events.append((interaction, parent, include_activity_coverage))

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(
        setup_permission_repair_services,
        "open_permission_repair",
        open_repair,
    )

    interaction = SimpleNamespace(guild=SimpleNamespace(id=123))
    asyncio.run(recommend._open_permission_repair(interaction))
    assert events == [(interaction, "security", False)]


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
    asyncio.run(button(security_view, "Back").callback(SimpleNamespace()))

    logs_view = setup_permission_repair_services.PermissionRepairResultView(
        parent="logs"
    )
    asyncio.run(button(logs_view, "Back").callback(SimpleNamespace()))

    assert events == ["security", "logs"]


def test_permission_repair_preview_actions_keep_parent_and_scope(monkeypatch) -> None:
    events: list[tuple[str, str, bool]] = []

    async def apply(
        _interaction: Any,
        *,
        parent: str = "security",
        include_activity_coverage: bool = False,
    ) -> None:
        events.append(("apply", parent, include_activity_coverage))

    async def preview(
        _interaction: Any,
        *,
        parent: str = "security",
        include_activity_coverage: bool = False,
    ) -> None:
        events.append(("preview", parent, include_activity_coverage))

    monkeypatch.setattr(setup_permission_repair_services, "apply_permission_repair", apply)
    monkeypatch.setattr(setup_permission_repair_services, "open_permission_repair", preview)

    view = setup_permission_repair_services.PermissionRepairPreviewView(
        parent="logs",
        include_activity_coverage=True,
    )
    apply_button = button(view, "Fix All Safe Access")
    assert apply_button.custom_id == "dank_setup_permission:apply"
    asyncio.run(apply_button.callback(SimpleNamespace()))
    asyncio.run(button(view, "Preview Again").callback(SimpleNamespace()))

    assert events == [
        ("apply", "logs", True),
        ("preview", "logs", True),
    ]


def test_permission_repair_result_is_concise_and_has_no_advanced_diagnostic_dump() -> None:
    result = {
        "applied": False,
        "target_count": 55,
        "changed": [f"#channel-{index} — @Dank Shield" for index in range(12)],
        "unchanged": ["#already-safe"],
        "failed": [],
        "manual_actions": [f"#blocked-{index}: Discord blocks repair" for index in range(20)],
        "missing_mappings": ["Welcome channel mapping is missing"],
        "notes": ["One concise note"],
        "include_activity_coverage": False,
    }

    embed = setup_permission_repair_services.result_embed(result)
    rendered = "\n".join(
        [str(embed.description or "")]
        + [str(field.name) + "\n" + str(field.value) for field in embed.fields]
    )

    assert "Advanced Diagnostic" not in rendered
    assert "Needs your attention" in rendered
    assert "…and" in rendered
    assert "Specific Channel" in rendered
    assert len(rendered) < 3500


def _component_by_id(view: discord.ui.View, custom_id: str) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "custom_id", "") or "") == custom_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_permission_repair_primary_action_matches_preview_truth() -> None:
    safe = setup_permission_repair_services.PermissionRepairPreviewView(
        result={
            "changed": ["#verification — @Dank Shield"],
            "failed": [],
            "manual_actions": [],
            "missing_mappings": [],
            "error": "",
        }
    )
    safe_apply = _component_by_id(safe, "dank_setup_permission:apply")
    assert safe_apply.label == "Apply Safe Fixes"
    assert safe_apply.disabled is False

    manual = setup_permission_repair_services.PermissionRepairPreviewView(
        result={
            "changed": [],
            "failed": [],
            "manual_actions": ["#verification: Manage Permissions is denied."],
            "missing_mappings": [],
            "error": "",
        }
    )
    manual_apply = _component_by_id(manual, "dank_setup_permission:apply")
    assert manual_apply.label == "Manual Discord Fix Required"
    assert manual_apply.disabled is True

    recovery = setup_permission_repair_services.PermissionRepairPreviewView(
        result={
            "changed": [],
            "failed": [],
            "manual_actions": ["1 target is already self-locked."],
            "missing_mappings": [],
            "error": "",
            "emergency_recovery_recommended": True,
        }
    )
    recovery_apply = _component_by_id(recovery, "dank_setup_permission:apply")
    assert recovery_apply.label == "Recovery Access Needed"
    assert recovery_apply.disabled is True

    healthy = setup_permission_repair_services.PermissionRepairPreviewView(
        result={
            "changed": [],
            "failed": [],
            "manual_actions": [],
            "missing_mappings": [],
            "error": "",
        }
    )
    healthy_apply = _component_by_id(healthy, "dank_setup_permission:apply")
    assert healthy_apply.label == "Access Healthy"
    assert healthy_apply.disabled is True


def test_permission_repair_embed_never_invites_apply_when_no_safe_change_exists() -> None:
    manual = {
        "applied": False,
        "target_count": 1,
        "changed": [],
        "unchanged": [],
        "failed": [],
        "manual_actions": ["#verification: Manage Permissions is denied."],
        "missing_mappings": [],
        "notes": [],
        "include_activity_coverage": False,
    }
    embed = setup_permission_repair_services.result_embed(manual)
    assert embed.title == "⚠️ Manual Discord Fix Required"
    assert "no safe overwrite changes" in str(embed.description or "").lower()
    assert "press **Apply Safe Fixes**" not in str(embed.description or "")

    healthy = dict(manual)
    healthy["manual_actions"] = []
    healthy_embed = setup_permission_repair_services.result_embed(healthy)
    assert healthy_embed.title == "✅ Bot Access Ready"


def test_permission_repair_open_stops_before_preview_when_ack_claim_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow(_interaction: Any) -> bool:
        return True

    async def reject_claim(_interaction: Any, *, action_name: str) -> bool:
        assert action_name == "access_repair_preview"
        return False

    async def forbidden_preview(*_args, **_kwargs):
        raise AssertionError("preview must not run after a failed interaction claim")

    monkeypatch.setattr(
        setup_permission_repair_services,
        "_claim_repair_interaction",
        reject_claim,
    )
    monkeypatch.setattr(
        setup_permission_repair_services,
        "preview_or_apply",
        forbidden_preview,
    )

    from stoney_verify.commands_ext import public_setup_solid as solid

    monkeypatch.setattr(solid, "_require_setup_permission", allow)
    interaction = SimpleNamespace(guild=SimpleNamespace(id=123))

    asyncio.run(
        setup_permission_repair_services.open_permission_repair(interaction)
    )


def test_permission_repair_apply_stops_before_queue_when_ack_claim_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def allow(_interaction: Any) -> bool:
        return True

    async def reject_claim(_interaction: Any, *, action_name: str) -> bool:
        assert action_name == "access_repair_apply"
        return False

    async def forbidden_apply(*_args, **_kwargs):
        raise AssertionError("mutation preview/apply must not run after failed claim")

    monkeypatch.setattr(
        setup_permission_repair_services,
        "_claim_repair_interaction",
        reject_claim,
    )
    monkeypatch.setattr(
        setup_permission_repair_services,
        "preview_or_apply",
        forbidden_apply,
    )

    from stoney_verify.commands_ext import public_setup_solid as solid

    monkeypatch.setattr(solid, "_require_setup_permission", allow)
    interaction = SimpleNamespace(guild=SimpleNamespace(id=123))

    asyncio.run(
        setup_permission_repair_services.apply_permission_repair(interaction)
    )


def test_permission_repair_self_lockout_surfaces_temporary_admin_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = SimpleNamespace(id=123)
    calls: list[Any] = []

    def fake_emergency(guild_arg: Any, *, row: int = 1) -> discord.ui.Button:
        calls.append(guild_arg)
        return discord.ui.Button(
            label="Temporary Admin Recovery",
            style=discord.ButtonStyle.link,
            url="https://example.com/emergency",
            row=row,
        )

    monkeypatch.setattr(
        setup_permission_repair_services,
        "_emergency_recovery_button",
        fake_emergency,
    )

    result = {
        "applied": False,
        "target_count": 46,
        "changed": [],
        "unchanged": [],
        "failed": [],
        "manual_actions": [
            "46 channel/category target(s) are already self-locked against Dank Shield's Manage Permissions."
        ],
        "missing_mappings": [],
        "notes": [],
        "include_activity_coverage": True,
        "emergency_recovery_recommended": True,
        "emergency_recovery_count": 46,
        "temporary_admin_active": False,
        "reauthorize_recommended": False,
    }

    view = setup_permission_repair_services.PermissionRepairPreviewView(
        guild=guild,
        include_activity_coverage=True,
        result=result,
    )
    assert calls == [guild]
    assert button(view, "Temporary Admin Recovery").style == discord.ButtonStyle.link

    embed = setup_permission_repair_services.result_embed(result)
    rendered = "\n".join(
        [str(embed.title or ""), str(embed.description or "")]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )
    assert "One-Time Recovery Access Needed" in rendered
    assert "Administrator" in rendered
    assert "Fix All Safe Access" in rendered
    assert "member/staff overwrites" in rendered


def test_permission_repair_warns_to_restore_normal_permissions_after_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {
        "applied": True,
        "target_count": 46,
        "changed": ["#general — @Dank Shield"],
        "unchanged": [],
        "failed": [],
        "manual_actions": [],
        "missing_mappings": [],
        "notes": [],
        "include_activity_coverage": True,
        "emergency_recovery_recommended": False,
        "emergency_recovery_count": 0,
        "temporary_admin_active": True,
        "reauthorize_recommended": False,
    }

    embed = setup_permission_repair_services.result_embed(result)
    rendered = "\n".join(
        [str(embed.title or ""), str(embed.description or "")]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )
    assert "Administrator currently enabled" in rendered
    assert "Restore Normal Permissions" in rendered
    assert "remove Administrator in Server Settings" in rendered
    assert "does not require Administrator for normal operation" in rendered

    guild = SimpleNamespace(id=123)

    def fake_restore(guild_arg: Any, *, row: int = 1) -> discord.ui.Button:
        assert guild_arg is guild
        return discord.ui.Button(
            label="Restore Normal Permissions",
            style=discord.ButtonStyle.link,
            url="https://example.com/normal",
            row=row,
        )

    monkeypatch.setattr(
        setup_permission_repair_services,
        "_restore_normal_permissions_button",
        fake_restore,
    )
    view = setup_permission_repair_services.PermissionRepairResultView(
        guild=guild,
        include_activity_coverage=True,
        result=result,
    )
    assert button(view, "Restore Normal Permissions").style == discord.ButtonStyle.link


def test_permission_repair_result_reauthorize_matches_server_level_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    def fake_reauthorize(guild: Any, *, row: int = 1) -> discord.ui.Button:
        calls.append(guild)
        return discord.ui.Button(
            label="Reauthorize Dank Shield",
            style=discord.ButtonStyle.link,
            url="https://example.com/reauthorize",
            row=row,
        )

    monkeypatch.setattr(
        setup_permission_repair_services,
        "_reauthorize_button",
        fake_reauthorize,
    )
    guild = SimpleNamespace(id=123)

    healthy = setup_permission_repair_services.PermissionRepairResultView(
        guild=guild,
        result={"reauthorize_recommended": False},
    )
    assert calls == []
    assert all(
        getattr(child, "label", "") != "Reauthorize Dank Shield"
        for child in healthy.children
    )

    blocked = setup_permission_repair_services.PermissionRepairResultView(
        guild=guild,
        result={"reauthorize_recommended": True},
    )
    assert calls == [guild]
    assert any(
        getattr(child, "label", "") == "Reauthorize Dank Shield"
        for child in blocked.children
    )


def test_activity_repair_preview_copy_matches_activity_primary_action() -> None:
    result = {
        "applied": False,
        "target_count": 3,
        "changed": ["#private-thread-parent — @Dank Shield"],
        "failed": [],
        "manual_actions": [],
        "missing_mappings": [],
        "notes": [],
        "unchanged": [],
        "include_activity_coverage": True,
    }

    embed = setup_permission_repair_services.result_embed(result)
    assert "Fix All Safe Access" in str(embed.description or "")
    assert "Apply Safe Fixes" not in str(embed.description or "")

    view = setup_permission_repair_services.PermissionRepairPreviewView(
        include_activity_coverage=True,
        result=result,
    )
    apply = _component_by_id(view, "dank_setup_permission:apply")
    assert apply.label == "Fix All Safe Access"
    assert apply.disabled is False

    manual = dict(result)
    manual["changed"] = []
    manual["manual_actions"] = ["#private-thread-parent: Manage Permissions is denied."]
    manual_view = setup_permission_repair_services.PermissionRepairPreviewView(
        include_activity_coverage=True,
        result=manual,
    )
    manual_apply = _component_by_id(manual_view, "dank_setup_permission:apply")
    assert manual_apply.label == "Manual Discord Fix Required"
    assert manual_apply.disabled is True
