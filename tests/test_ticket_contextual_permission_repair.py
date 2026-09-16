from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import discord

from stoney_verify.commands_ext import public_ticket_command_center as center
from stoney_verify.commands_ext import public_ticket_contextual_permission_repair as repair
from stoney_verify.commands_ext import public_ticket_panel_clean as panel
from stoney_verify.commands_ext import _PUBLIC_ADMIN_EXTRA_MODULES, _PUBLIC_CORE_MODULES


def _target_rows(targets: Any) -> list[tuple[int, str, str]]:
    return [
        (int(item.channel_id), str(item.feature), str(item.label))
        for item in targets
    ]


def test_ticket_infrastructure_mapping_is_saved_id_only_and_feature_specific() -> None:
    cfg = SimpleNamespace(
        ticket_category_id=101,
        ticket_archive_category_id=202,
        ticket_panel_channel_id=303,
        transcripts_channel_id=404,
    )

    assert _target_rows(repair._ticket_config_targets(cfg)) == [
        (101, "tickets", "Active Tickets category"),
        (202, "tickets", "Ticket archive category"),
        (303, "general", "Public ticket panel channel"),
        (404, "logs", "Ticket transcripts channel"),
    ]


def test_ticket_infrastructure_mapping_never_guesses_missing_resources() -> None:
    cfg = SimpleNamespace(
        ticket_category_id=0,
        ticket_archive_category_id=0,
        ticket_panel_channel_id=0,
        transcripts_channel_id=0,
    )

    assert repair._ticket_config_targets(cfg) == ()


def test_ticket_manual_issues_keep_missing_mapping_and_staff_role_manual() -> None:
    guild = SimpleNamespace(
        get_channel=lambda _channel_id: None,
        get_role=lambda _role_id: None,
    )
    cfg = SimpleNamespace(ticket_category_id=0, staff_role_id=0)

    issues = repair._ticket_manual_issues(guild, cfg)

    assert any("Active Tickets category is not configured" in item for item in issues)
    assert any("Ticket staff role is not configured" in item for item in issues)


def test_current_ticket_center_keeps_full_action_menu_and_adds_contextual_repair() -> None:
    view = repair.ContextualTicketActionCenterView(1, channel_id=0, guild=None)

    repair_buttons = [
        item
        for item in view.children
        if str(getattr(item, "custom_id", "") or "")
        == "dank:ticket:center:contextual_repair:v1"
    ]
    channel_pickers = [
        item
        for item in view.children
        if str(getattr(item, "custom_id", "") or "")
        == "dank:ticket:center:channel:v1"
    ]
    action_selects = [
        item
        for item in view.children
        if str(getattr(item, "custom_id", "") or "")
        == "dank:ticket:center:action:v1"
    ]

    assert len(repair_buttons) == 1
    assert repair_buttons[0].label == "Check / Fix Access"
    assert len(channel_pickers) == 1
    assert len(action_selects) == 1
    assert len(action_selects[0].options) == 16


def test_ticket_health_view_exposes_manual_state_when_required_mapping_is_absent() -> None:
    guild = SimpleNamespace(
        id=55,
        get_channel=lambda _channel_id: None,
        get_role=lambda _role_id: None,
    )
    cfg = SimpleNamespace(ticket_category_id=0, staff_role_id=0)

    view = repair.TicketPanelHealthView(owner_id=9, guild=guild, cfg=cfg)
    button = next(
        item
        for item in view.children
        if str(getattr(item, "custom_id", "") or "")
        == "dank:tickets:contextual_repair:infrastructure:v1"
    )

    assert button.label == "Manual Fix Needed"
    assert button.emoji is not None
    assert button.disabled is False


def test_runtime_binding_replaces_only_ticket_ui_entry_points(monkeypatch) -> None:
    async def old_health(_interaction: Any) -> None:
        return None

    class OldView(discord.ui.View):
        pass

    monkeypatch.setattr(panel, "_send_health", old_health)
    monkeypatch.setattr(center, "TicketActionCenterView", OldView)
    monkeypatch.setattr(repair, "_PATCHED", False)

    assert repair.apply_ticket_contextual_permission_repair() is True
    assert panel._send_health is repair._contextual_send_health
    assert center.TicketActionCenterView is repair.ContextualTicketActionCenterView


def test_ticket_contextual_integration_never_owns_discord_overwrite_mutation() -> None:
    import inspect

    source = inspect.getsource(repair)
    assert "set_permissions(" not in source
    assert "contextual.repair_context(" in source
    assert "clear_explicit_denies" not in source


def test_tickettool_readiness_remains_admin_only_not_pulled_into_public_runtime() -> None:
    assert "public_tickettool_check" not in _PUBLIC_CORE_MODULES
    assert "public_tickettool_check" in _PUBLIC_ADMIN_EXTRA_MODULES
