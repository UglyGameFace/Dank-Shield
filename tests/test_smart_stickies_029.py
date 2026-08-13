from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
import pytest

from stoney_verify.commands_ext.public_quiet_notice import (
    QuietNoticeCenterView,
    human_duration,
    parse_inactivity_duration,
)
from stoney_verify.commands_ext.public_sticky_preview import StickyPreviewTestView
from stoney_verify.community_quiet_notice_service import (
    QuietNoticeConfig,
    normalize_quiet_notice,
)
from stoney_verify.community_tools_runtime import (
    quiet_notice_embed,
    quiet_notice_view,
    should_send_quiet_notice,
)
from stoney_verify.community_tools_service import InvalidCommunityToolValue, StickyConfig


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def _quiet(**changes: object) -> QuietNoticeConfig:
    base = QuietNoticeConfig(
        guild_id=1001,
        channel_id=2001,
        content="Members may be hanging out in our partner community.",
        inactivity_seconds=7200,
        partner_name="Partner Place",
        partner_url="https://discord.gg/example",
        auto_clear=True,
        last_activity_at=datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
    )
    values = {**base.__dict__, **changes}
    return QuietNoticeConfig(**values)


def test_quiet_notice_duration_parser_is_human_friendly_and_bounded() -> None:
    assert parse_inactivity_duration("30m") == 1800
    assert parse_inactivity_duration("2h") == 7200
    assert parse_inactivity_duration("1 day") == 86400
    assert parse_inactivity_duration("45") == 2700
    assert human_duration(7200) == "2 hours"
    with pytest.raises(InvalidCommunityToolValue):
        parse_inactivity_duration("2 bananas")
    with pytest.raises(InvalidCommunityToolValue):
        parse_inactivity_duration("1m")


def test_quiet_notice_normalization_preserves_safe_partner_destination() -> None:
    safe = normalize_quiet_notice(_quiet())
    assert safe.guild_id == 1001
    assert safe.channel_id == 2001
    assert safe.partner_name == "Partner Place"
    assert safe.partner_url == "https://discord.gg/example"
    assert safe.auto_clear is True


def test_quiet_notice_fires_once_per_quiet_cycle_and_rearms_after_activity() -> None:
    activity = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    due = _quiet(last_activity_at=activity, last_notice_sent_at=None)
    assert should_send_quiet_notice(due, now=activity + timedelta(hours=2, seconds=1)) is True

    already_sent = _quiet(
        last_activity_at=activity,
        last_notice_sent_at=activity + timedelta(hours=2, seconds=1),
        last_notice_message_id=9001,
    )
    assert should_send_quiet_notice(already_sent, now=activity + timedelta(hours=5)) is False

    new_activity = activity + timedelta(hours=3)
    assert should_send_quiet_notice(
        already_sent,
        last_activity_at=new_activity,
        now=new_activity + timedelta(hours=2, seconds=1),
    ) is True


def test_quiet_notice_preview_has_optional_partner_link_and_clear_truth() -> None:
    config = _quiet()
    embed = quiet_notice_embed(config)
    assert "quiet here" in str(embed.title).lower()
    assert "Partner Place" in str(embed.fields[0].value)
    assert "clears when human activity returns" in str(embed.footer.text)
    view = quiet_notice_view(config)
    assert view is not None
    assert "Open Partner Place" in _labels(view)


def test_quiet_notice_center_guides_setup_preview_pause_remove_and_back() -> None:
    labels = _labels(QuietNoticeCenterView(1, _quiet()))
    assert {"Setup / Edit", "Preview / Test", "Pause / Resume", "Remove", "Back to Stickies"} <= labels

    empty = QuietNoticeCenterView(1, None)
    disabled = {
        str(getattr(item, "label", "") or "")
        for item in empty.children
        if bool(getattr(item, "disabled", False))
    }
    assert {"Preview / Test", "Pause / Resume", "Remove"} <= disabled


def test_normal_sticky_preview_exposes_non_persistent_30_second_test() -> None:
    sticky = StickyConfig(guild_id=1001, channel_id=2001, content="Hello", mode="plain")
    labels = _labels(StickyPreviewTestView(1, sticky, None))
    assert "Post 30s Test" in labels
