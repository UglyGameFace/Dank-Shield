from __future__ import annotations

import asyncio
import inspect
from collections import Counter
from types import SimpleNamespace

import discord

from stoney_verify import permission_repair, setup_permission_repair_services
from stoney_verify import guild_config, setup_engine
from stoney_verify.startup_guards import setup_permission_repair_guard as legacy


def _component(view: discord.ui.View, custom_id: str):
    matches = [
        child
        for child in view.children
        if str(getattr(child, "custom_id", "") or "") == custom_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_selected_target_screen_visibly_preserves_feature_and_mode_before_target() -> None:
    guild = SimpleNamespace(me=None)
    state = permission_repair.PermissionRepairState(
        guild=guild,
        actor_id=123,
        feature="tickets",
        mode="full",
    )

    embed = permission_repair.build_preview_embed(state)
    rendered = str(embed.description or "")
    assert "Target:** `Not selected`" in rendered
    assert "Feature:** Tickets" in rendered
    assert "Repair mode:** Full Dank Shield control" in rendered

    view = permission_repair.TargetPermissionRepairView(state)
    target = _component(view, "dank_permission_repair:target")
    feature = _component(view, "dank_permission_repair:feature")
    mode = _component(view, "dank_permission_repair:mode")
    children = _component(view, "dank_permission_repair:children")

    assert "Choose a channel or category" in str(target.placeholder)
    assert str(feature.placeholder) == "Feature: Tickets"
    assert str(mode.placeholder) == "Repair mode: Full Dank Shield control"
    assert [option.value for option in feature.options if option.default] == ["tickets"]
    assert [option.value for option in mode.options if option.default] == ["full"]
    assert children.disabled is True
    assert str(children.label) == "Include Category Children: OFF"


def test_selected_target_view_stays_within_discord_component_limits() -> None:
    state = permission_repair.PermissionRepairState(
        guild=SimpleNamespace(me=None),
        actor_id=1,
    )
    view = permission_repair.TargetPermissionRepairView(state)
    rows = Counter(int(getattr(child, "row", 0) or 0) for child in view.children)

    assert len(view.children) <= 25
    assert rows
    assert max(rows.values()) <= 5


def test_selected_target_mutations_use_message_update_not_permanent_thinking_placeholder() -> None:
    fix_source = inspect.getsource(permission_repair.TargetPermissionRepairView.fix_missing)
    deny_source = inspect.getsource(permission_repair.ExplicitDenyConfirmView.confirm)
    undo_source = inspect.getsource(permission_repair.UndoTokenModal.on_submit)

    assert "await _safe_defer_update(interaction)" in fix_source
    assert "thinking=True" not in fix_source
    assert "_edit_original_or_followup" in fix_source

    assert "await _safe_defer_update(interaction)" in deny_source
    assert "thinking=True" not in deny_source
    assert "_edit_original_or_followup" in deny_source

    # Modal submissions need a deferred response, but both success and queue
    # duplicate/busy/failure paths must clear that deferred original.
    assert "thinking=True" in undo_source
    assert "if not isinstance(result, TargetRepairResult):" in undo_source
    assert "await interaction.edit_original_response(" in undo_source
    assert "_edit_original_or_followup" in undo_source


def test_setup_apply_reports_only_successful_discord_writes(monkeypatch) -> None:
    class FakeChannel:
        def overwrites_for(self, _target):
            return discord.PermissionOverwrite()

        async def set_permissions(self, *_args, **_kwargs) -> None:
            raise RuntimeError("simulated Discord write failure")

    channel = FakeChannel()
    target = object()
    item = SimpleNamespace(
        channel=channel,
        overwrites={target: discord.PermissionOverwrite(view_channel=True)},
        label="Test target",
    )
    guild = SimpleNamespace(id=777)

    monkeypatch.setattr(setup_permission_repair_services, "_bot_blockers", lambda _guild: [])

    async def build_targets(_guild, *, include_activity_coverage=False):
        assert include_activity_coverage is False
        return [item], [], [], []

    monkeypatch.setattr(setup_permission_repair_services, "_build_expanded_targets", build_targets)
    monkeypatch.setattr(legacy, "_bot_member", lambda _guild: object())
    monkeypatch.setattr(legacy, "_channel_manage_missing", lambda _channel, _me: False)
    monkeypatch.setattr(legacy, "_channel_label", lambda _channel: "#test")
    monkeypatch.setattr(legacy, "_target_label", lambda _target: "@Dank Shield")
    monkeypatch.setattr(legacy, "_overwrite_changed", lambda _current, _expected: True)

    async def get_config(_guild_id, refresh=False):
        assert refresh is True
        return SimpleNamespace()

    monkeypatch.setattr(guild_config, "get_guild_config", get_config)
    monkeypatch.setattr(
        setup_engine,
        "build_setup_health_report",
        lambda _guild, _cfg: SimpleNamespace(findings=[]),
    )

    result = asyncio.run(
        setup_permission_repair_services.preview_or_apply(
            guild,
            apply=True,
            include_activity_coverage=False,
        )
    )

    assert result["changed"] == []
    assert len(result["failed"]) == 1
    assert "RuntimeError" in result["failed"][0]
    assert result["ok"] is False


def test_setup_permission_result_summarizes_instead_of_dumping_every_target() -> None:
    result = {
        "applied": False,
        "target_count": 55,
        "changed": [f"#fix-{index} — @Dank Shield" for index in range(20)],
        "unchanged": [f"#safe-{index}" for index in range(20)],
        "failed": [],
        "manual_actions": [f"#blocked-{index}: blocked" for index in range(34)],
        "missing_mappings": [],
        "notes": [f"note {index}" for index in range(15)],
        "include_activity_coverage": False,
    }

    embed = setup_permission_repair_services.result_embed(result)
    rendered = "\n".join(
        [str(embed.description or "")]
        + [f"{field.name}\n{field.value}" for field in embed.fields]
    )

    assert "Advanced Diagnostic" not in rendered
    assert "#blocked-33" not in rendered
    assert "#fix-19" not in rendered
    assert "…and" in rendered
    assert "Specific Channel" in rendered
