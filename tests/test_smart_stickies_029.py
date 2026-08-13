from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.commands_ext.public_community_tools import StickyCenterView, StickySettingsView
from stoney_verify.commands_ext.public_quiet_notice import (
    QuietNoticeCenterView,
    human_duration,
    parse_inactivity_duration,
)
from stoney_verify.commands_ext.public_sticky_preview import StickyDraftPreviewView, StickyPreviewTestView
from stoney_verify.community_quiet_notice_service import (
    QuietNoticeConfig,
    normalize_quiet_notice,
)
from stoney_verify.community_tools_runtime import (
    StickyRuntime,
    quiet_notice_embed,
    quiet_notice_view,
    should_send_quiet_notice,
)
from stoney_verify.community_tools_service import InvalidCommunityToolValue, StickyConfig, StickyPoll


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


def test_runtime_observes_human_activity_anywhere_in_configured_guild() -> None:
    async def scenario() -> None:
        baseline = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        observed = baseline + timedelta(seconds=10)
        runtime = StickyRuntime(SimpleNamespace())
        config = _quiet(last_activity_at=baseline)
        runtime.set_quiet_config(config)
        # Prevent the background watcher from being created by this focused unit test.
        task = runtime._quiet_watch_task
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            runtime._quiet_watch_task = None

        message = SimpleNamespace(
            guild=SimpleNamespace(id=1001),
            author=SimpleNamespace(bot=False),
            webhook_id=None,
            channel=SimpleNamespace(id=9999),
            created_at=observed,
        )
        await runtime.on_message(message)
        assert runtime._guild_last_activity[1001] == observed
        assert 9999 not in runtime._configs

    asyncio.run(scenario())


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


def test_sticky_draft_requires_explicit_publish_after_preview() -> None:
    sticky = StickyConfig(guild_id=1001, channel_id=2001, content="Hello", mode="plain")
    labels = _labels(StickyDraftPreviewView(1, sticky))
    assert {"Publish Sticky", "Post 30s Test", "Discard Draft"} <= labels


def test_main_sticky_center_stays_focused_and_advanced_actions_live_in_settings() -> None:
    sticky = StickyConfig(guild_id=1001, channel_id=2001, content="Hello", mode="plain")
    main_labels = _labels(StickyCenterView(1, config=sticky, poll=None))
    assert {
        "Create / Edit",
        "Preview / Test",
        "Sticky Settings",
        "Sticky Poll",
        "Quiet Server Notice",
        "Server Stickies",
        "Community Tools",
    } <= main_labels
    assert {"Pause / Resume", "Speed / Cadence", "Custom Sender", "Remove"}.isdisjoint(main_labels)

    settings_labels = _labels(StickySettingsView(1, sticky, None))
    assert {"Pause / Resume", "Speed / Cadence", "Custom Sender", "Remove", "Back to Sticky"} <= settings_labels


def test_existing_sticky_poll_changes_main_action_to_poll_controls() -> None:
    sticky = StickyConfig(guild_id=1001, channel_id=2001, content="Pick one", mode="poll")
    poll = StickyPoll(
        guild_id=1001,
        channel_id=2001,
        question="Pick one",
        options=("A", "B"),
        votes={},
    )
    labels = _labels(StickyCenterView(1, config=sticky, poll=poll))
    assert "Poll Controls" in labels
    assert "Sticky Poll" not in labels
