from __future__ import annotations

from collections import Counter
from pathlib import Path

import discord

from stoney_verify import setup_activity_access
from stoney_verify.members_new.activity_scope import ActivityScopeProblem, ActivityScopeReport


SOURCE = Path("stoney_verify/setup_activity_access.py").read_text(encoding="utf-8")
RECOMMEND = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")


def _rows(view: discord.ui.View) -> Counter[int]:
    return Counter(int(getattr(child, "row", 0) or 0) for child in view.children)


def _button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [child for child in view.children if str(getattr(child, "label", "") or "") == label]
    assert len(matches) == 1
    assert isinstance(matches[0], discord.ui.Button)
    return matches[0]


def test_logs_and_safety_exposes_plain_check_bot_access_feature() -> None:
    assert 'label="Check Bot Access"' in RECOMMEND
    assert 'custom_id="dank_setup_advanced_monitoring:bot_access"' in RECOMMEND
    assert "await _open_bot_access_check(interaction)" in RECOMMEND
    assert 'label="Fix Channel Permissions"' in RECOMMEND


def test_setup_route_uses_owned_read_only_access_service() -> None:
    assert "from stoney_verify import setup_activity_access" in RECOMMEND
    assert "setup_activity_access.open_activity_access_check(interaction)" in RECOMMEND
    assert "audit_activity_scope(guild)" in SOURCE


def test_access_check_reports_exact_missing_permissions_and_coverage() -> None:
    report = ActivityScopeReport(
        total_channels=4,
        accessible_channels=2,
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
        ),
        bot_member_resolved=True,
    )

    embed = setup_activity_access.build_activity_access_embed(report)
    rendered = "\n".join(
        [embed.description or ""]
        + [str(field.name) + "\n" + str(field.value) for field in embed.fields]
    )

    assert "50% activity scope" in rendered
    assert "#moderator-only" in rendered
    assert "View Channel" in rendered
    assert "Read Message History" in rendered
    assert "#song-recommendations" in rendered
    assert "purge-safe" in rendered


def test_access_check_is_read_only_and_never_self_grants_permissions() -> None:
    assert ".set_permissions(" not in SOURCE
    assert "PermissionOverwrite(" not in SOURCE
    assert "current.view_channel = True" not in SOURCE
    assert "current.read_message_history = True" not in SOURCE
    assert "current.manage_threads = True" not in SOURCE
    assert "changes nothing" in SOURCE
    assert "does not silently grant itself access" in SOURCE


def test_repair_button_routes_to_existing_preview_first_permission_tool() -> None:
    view = setup_activity_access.ActivityAccessView(needs_repair=True)
    fix = _button(view, "Fix Channel Permissions")
    assert fix.disabled is False
    assert "setup_permission_repair_services.open_permission_repair" in SOURCE

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
