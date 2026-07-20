from __future__ import annotations

import asyncio
from collections import Counter

import discord

from stoney_verify import setup_activity_access, setup_permission_repair_services
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_solid as solid
from stoney_verify.members_new.activity_scope import ActivityScopeProblem, ActivityScopeReport


def _rows(view: discord.ui.View) -> Counter[int]:
    return Counter(int(getattr(child, "row", 0) or 0) for child in view.children)


def _button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [child for child in view.children if str(getattr(child, "label", "") or "") == label]
    assert len(matches) == 1
    assert isinstance(matches[0], discord.ui.Button)
    return matches[0]


def test_logs_and_safety_exposes_plain_check_bot_access_feature() -> None:
    view = recommend.AdvancedMonitoringRepairView()

    access = _button(view, "Check Bot Access")
    repair = _button(view, "Fix Channel Permissions")

    assert access.custom_id == "dank_setup_advanced_monitoring:bot_access"
    assert access.row == 1
    assert repair.custom_id == "dank_setup_advanced_monitoring:permission_repair"
    assert repair.row == 1


def test_setup_route_calls_owned_activity_access_service(monkeypatch) -> None:
    calls: list[object] = []

    async def allow_setup(_interaction) -> bool:
        return True

    async def open_check(interaction) -> None:
        calls.append(interaction)

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow_setup)
    monkeypatch.setattr(setup_activity_access, "open_activity_access_check", open_check)

    interaction = object()
    asyncio.run(recommend._open_bot_access_check(interaction))

    assert calls == [interaction]


def test_access_check_reports_exact_missing_permissions_and_coverage() -> None:
    report = ActivityScopeReport(
        total_channels=4,
        accessible_channels=1,
        problems=(
            ActivityScopeProblem(
                channel_id=11,
                channel_name="moderator-only",
                channel_kind="text",
                missing_permissions=("View Channel", "Read Message History"),
            ),
            ActivityScopeProblem(
                channel_id=12,
                channel_name="song-recommendations",
                channel_kind="text",
                missing_permissions=("Read Message History",),
            ),
            ActivityScopeProblem(
                channel_id=13,
                channel_name="private-thread-parent",
                channel_kind="text",
                missing_permissions=("Manage Threads",),
            ),
        ),
        bot_member_resolved=True,
    )

    embed = setup_activity_access.build_activity_access_embed(report)
    rendered = "\n".join(
        [embed.description or ""]
        + [str(field.name) + "\n" + str(field.value) for field in embed.fields]
    )

    assert "25% activity scope" in rendered
    assert "#moderator-only" in rendered
    assert "View Channel" in rendered
    assert "Read Message History" in rendered
    assert "#song-recommendations" in rendered
    assert "#private-thread-parent" in rendered
    assert "Manage Threads" in rendered
    assert "purge-safe" in rendered


def test_open_access_check_is_read_only_and_renders_audit_result(monkeypatch) -> None:
    class GuardedChannel:
        mutation_attempted = False

        async def set_permissions(self, *_args, **_kwargs) -> None:
            self.mutation_attempted = True
            raise AssertionError("read-only access check attempted to change permissions")

        async def edit(self, *_args, **_kwargs) -> None:
            self.mutation_attempted = True
            raise AssertionError("read-only access check attempted to edit a channel")

    class FakeGuild:
        def __init__(self) -> None:
            self.channels = [GuardedChannel()]

    class FakeInteraction:
        def __init__(self) -> None:
            self.guild = FakeGuild()

    report = ActivityScopeReport(
        total_channels=1,
        accessible_channels=0,
        problems=(
            ActivityScopeProblem(
                channel_id=99,
                channel_name="private-room",
                channel_kind="text",
                missing_permissions=("View Channel",),
            ),
        ),
        bot_member_resolved=True,
    )
    captured: dict[str, object] = {}

    async def allow_setup(_interaction) -> bool:
        return True

    def audit(guild):
        captured["guild"] = guild
        return report

    async def defer(interaction) -> None:
        captured["deferred"] = interaction

    async def edit_or_followup(interaction, *, embed, view) -> None:
        captured["interaction"] = interaction
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(solid, "_require_setup_permission", allow_setup)
    monkeypatch.setattr(solid, "_safe_defer_update", defer)
    monkeypatch.setattr(solid, "_edit_or_followup", edit_or_followup)
    monkeypatch.setattr(setup_activity_access, "audit_activity_scope", audit)

    interaction = FakeInteraction()
    asyncio.run(setup_activity_access.open_activity_access_check(interaction))

    assert captured["guild"] is interaction.guild
    assert captured["deferred"] is interaction
    assert captured["interaction"] is interaction
    assert isinstance(captured["embed"], discord.Embed)
    assert isinstance(captured["view"], setup_activity_access.ActivityAccessView)
    assert interaction.guild.channels[0].mutation_attempted is False


def test_repair_button_routes_to_existing_preview_first_permission_tool(monkeypatch) -> None:
    calls: list[object] = []

    async def open_repair(interaction) -> None:
        calls.append(interaction)

    monkeypatch.setattr(setup_permission_repair_services, "open_permission_repair", open_repair)

    interaction = object()
    view = setup_activity_access.ActivityAccessView(needs_repair=True)
    fix = _button(view, "Fix Channel Permissions")

    assert fix.disabled is False
    asyncio.run(fix.callback(interaction))
    assert calls == [interaction]

    complete_view = setup_activity_access.ActivityAccessView(needs_repair=False)
    assert _button(complete_view, "Fix Channel Permissions").disabled is True


def test_bot_access_view_keeps_mobile_rows_to_two_buttons_max() -> None:
    view = setup_activity_access.ActivityAccessView(needs_repair=True)
    counts = _rows(view)
    assert counts
    assert max(counts.values()) <= 2
    assert {str(getattr(child, "label", "") or "") for child in view.children} == {
        "Check Again",
        "Fix Channel Permissions",
        "Back to Logs & Safety",
        "Back Home",
    }


def test_complete_access_report_is_clear_and_does_not_offer_fake_problem_count() -> None:
    report = ActivityScopeReport(
        total_channels=7,
        accessible_channels=7,
        problems=(),
        bot_member_resolved=True,
    )
    embed = setup_activity_access.build_activity_access_embed(report)
    rendered = "\n".join(str(field.value) for field in embed.fields)
    assert "100% activity scope" in rendered
    assert "No activity-tracking channel access gaps were detected" in rendered
