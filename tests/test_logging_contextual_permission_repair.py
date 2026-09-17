from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import discord
import pytest
from discord import app_commands

from stoney_verify.commands_ext import public_logging_contextual_permission_repair as repair
from stoney_verify.commands_ext import public_setup_gate as gate


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _target_rows(targets: Any) -> list[tuple[int, str, str]]:
    return [
        (int(item.channel_id), str(item.feature), str(item.label))
        for item in targets
    ]


def test_modlog_target_is_saved_id_only_and_uses_logs_profile() -> None:
    cfg = SimpleNamespace(modlog_channel_id=123)
    assert _target_rows(repair._modlog_targets(cfg)) == [
        (123, "logs", "Moderation log channel")
    ]


def test_modlog_target_never_guesses_a_channel_when_mapping_is_absent() -> None:
    cfg = SimpleNamespace(modlog_channel_id=0, mod_log_channel_id=0, logs_channel_id=0)
    assert repair._modlog_targets(cfg) == ()


def test_member_log_targets_use_exact_resolved_routes_and_profiles() -> None:
    join = SimpleNamespace(id=11)
    exit_route = SimpleNamespace(id=22)
    staff = SimpleNamespace(id=33)

    assert _target_rows(repair._member_log_targets(join, exit_route, staff)) == [
        (11, "welcome", "Live join card channel"),
        (22, "logs", "Live exit card channel"),
        (33, "logs", "Staff member-audit channel"),
    ]


def test_member_log_target_mapping_drops_unresolved_routes_without_guessing() -> None:
    assert repair._member_log_targets(None, None, None) == ()


def test_modlog_manual_issues_keep_mapping_and_view_audit_log_manual() -> None:
    guild = SimpleNamespace(
        get_channel=lambda _channel_id: None,
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                view_audit_log=False,
                administrator=False,
            )
        ),
    )
    cfg = SimpleNamespace(modlog_channel_id=0)

    issues = repair._modlog_manual_issues(guild, cfg)

    assert any("No Modlog channel is saved" in item for item in issues)
    assert any("View Audit Log" in item for item in issues)


def test_member_log_manual_issues_keep_missing_routes_and_server_permission_manual() -> None:
    guild = SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                manage_guild=False,
                administrator=False,
            )
        )
    )
    cfg = SimpleNamespace(staff_join_audit_channel_id=999)

    issues = repair._member_log_manual_issues(
        guild,
        cfg,
        join_channel=None,
        join_reason="no join-card channel configured",
        exit_channel=None,
        exit_reason="no exit-card channel configured",
        staff_channel=None,
    )

    assert any("join-card route is unavailable" in item for item in issues)
    assert any("exit-card route is unavailable" in item for item in issues)
    assert any("staff member-audit route" in item for item in issues)
    assert any("Manage Server" in item for item in issues)


def test_unconfigured_optional_staff_audit_is_not_invented_as_a_channel_error() -> None:
    guild = SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                manage_guild=True,
                administrator=False,
            )
        )
    )
    cfg = SimpleNamespace()

    issues = repair._member_log_manual_issues(
        guild,
        cfg,
        join_channel=SimpleNamespace(id=1),
        join_reason="configured join-card channel",
        exit_channel=SimpleNamespace(id=2),
        exit_reason="configured exit-card channel",
        staff_channel=None,
    )

    assert not any("staff member-audit route" in item for item in issues)


def test_logging_contextual_integration_never_owns_discord_overwrite_mutation() -> None:
    source = inspect.getsource(repair)
    assert "set_permissions(" not in source
    assert "contextual.repair_context(" in source
    assert "clear_explicit_denies" not in source


def test_late_public_bootstrap_activates_logging_contextual_repair() -> None:
    source = inspect.getsource(gate.register_public_setup_gate)
    assert "apply_logging_contextual_permission_repair" in source
    assert "logging_contextual_repair" in source


def test_app_command_callback_is_read_only_in_supported_discord_runtime() -> None:
    descriptor = inspect.getattr_static(app_commands.Command, "callback")
    assert isinstance(descriptor, property)
    assert descriptor.fset is None


def test_apply_is_atomic_when_member_logs_command_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def old_modlog_health(_interaction: Any) -> None:
        return None

    fake_group = SimpleNamespace(get_command=lambda _name: None)
    monkeypatch.setattr(repair, "_PATCHED", False)
    monkeypatch.setattr(repair, "_ORIGINAL_MODLOG_HEALTH", None)
    monkeypatch.setattr(repair.modlog, "open_modlog_health", old_modlog_health)
    monkeypatch.setattr(repair, "dank_group", fake_group)

    assert repair.apply_logging_contextual_permission_repair() is False
    assert repair.modlog.open_modlog_health is old_modlog_health
    assert repair._ORIGINAL_MODLOG_HEALTH is None


def test_apply_supports_real_read_only_app_command_without_replacing_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def old_modlog_health(_interaction: Any) -> None:
        return None

    async def old_member_logs(
        interaction: discord.Interaction,
        public_welcome: Optional[discord.TextChannel] = None,
        join_leave_log: Optional[discord.TextChannel] = None,
        staff_audit_log: Optional[discord.TextChannel] = None,
    ) -> None:
        _ = interaction, public_welcome, join_leave_log, staff_audit_log

    command = app_commands.command(
        name="member-logs",
        description="Configure member logs.",
    )(old_member_logs)
    fake_group = SimpleNamespace(
        get_command=lambda name: command if name == "member-logs" else None
    )

    monkeypatch.setattr(repair, "_PATCHED", False)
    monkeypatch.setattr(repair, "_ORIGINAL_MODLOG_HEALTH", None)
    monkeypatch.setattr(repair.modlog, "open_modlog_health", old_modlog_health)
    monkeypatch.setattr(repair, "dank_group", fake_group)

    assert repair.apply_logging_contextual_permission_repair() is True
    assert repair.modlog.open_modlog_health is repair.open_contextual_modlog_health
    assert command.callback is old_member_logs
    source = inspect.getsource(repair.apply_logging_contextual_permission_repair)
    assert ".callback =" not in source
    assert "._callback" not in source


def test_authoritative_member_logs_callback_attaches_contextual_repair() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (
        root
        / "stoney_verify"
        / "startup_guards"
        / "member_lifecycle_router_guard.py"
    ).read_text(encoding="utf-8")

    assert "attach_member_logs_contextual_repair" in source
    assert "await attach_member_logs_contextual_repair(interaction)" in source


def test_member_logs_attachment_is_gated_by_atomic_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edits: list[dict[str, Any]] = []

    async def edit_original_response(**kwargs: Any) -> None:
        edits.append(kwargs)

    monkeypatch.setattr(repair, "_PATCHED", False)
    monkeypatch.setattr(repair, "_member_user_authorized", lambda _interaction: True)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=77),
        user=SimpleNamespace(id=88),
        edit_original_response=edit_original_response,
    )

    run(repair.attach_member_logs_contextual_repair(interaction))

    assert edits == []


def test_member_logs_authoritative_response_gets_repair_view_after_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edits: list[dict[str, Any]] = []

    async def config(_guild_id: int, refresh: bool = False) -> object:
        assert refresh is True
        return object()

    async def edit_original_response(**kwargs: Any) -> None:
        edits.append(kwargs)

    fake_view = object()
    monkeypatch.setattr(repair, "_PATCHED", True)
    monkeypatch.setattr(repair, "_member_user_authorized", lambda _interaction: True)
    monkeypatch.setattr(repair, "get_guild_config", config)
    monkeypatch.setattr(repair, "MemberLogsRepairView", lambda **_kwargs: fake_view)

    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=77),
        user=SimpleNamespace(id=88),
        edit_original_response=edit_original_response,
    )

    run(repair.attach_member_logs_contextual_repair(interaction))

    assert edits == [{"view": fake_view}]
