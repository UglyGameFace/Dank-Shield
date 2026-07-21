from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from stoney_verify import config_history_ui
from stoney_verify.commands_ext import public_members_group


def test_config_history_renders_per_viewer_discord_time() -> None:
    rendered = config_history_ui._format_timestamp(
        "2026-07-21T00:40:31+00:00"
    )

    assert rendered == "<t:1784594431:f> • <t:1784594431:R>"
    assert "UTC" not in rendered


def test_config_history_dropdown_does_not_show_unlocalized_time() -> None:
    option = config_history_ui._version_option(
        {
            "version_id": 8,
            "config_table": "guild_configs",
            "source": "manual_backup",
            "is_manual": True,
            "created_at": "2026-07-21T00:40:31+00:00",
        }
    )

    assert option is not None
    assert "Manual backup" in str(option.description or "")
    assert "UTC" not in str(option.description or "")
    assert "<t:" not in str(option.description or "")


def test_relative_notice_schedule_needs_no_timezone() -> None:
    now = datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)

    parsed = public_members_group._parse_notice_datetime(
        "+2h",
        timezone_name="",
        now=now,
    )

    assert parsed == datetime(2026, 7, 21, 2, 0, tzinfo=timezone.utc)


def test_absolute_notice_schedule_requires_explicit_timezone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        public_members_group._parse_notice_datetime(
            "2026-07-20 8:00 PM",
            timezone_name="",
            now=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        )


def test_absolute_notice_schedule_respects_named_timezone() -> None:
    parsed = public_members_group._parse_notice_datetime(
        "2026-07-20 8:00 PM",
        timezone_name="America/New_York",
        now=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert parsed == datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)


def test_notice_modal_does_not_silently_default_every_guild_to_new_york() -> None:
    modal = public_members_group.NoticeScheduleModal(
        scope="review",
        report=SimpleNamespace(),
    )

    assert modal.timezone_name.required is False
    assert str(modal.timezone_name.default or "") == ""
    assert "typed calendar dates" in modal.timezone_name.label.lower()
