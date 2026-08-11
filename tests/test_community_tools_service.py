from __future__ import annotations

import asyncio
from dataclasses import fields
from datetime import timedelta
from types import SimpleNamespace

import pytest

from stoney_verify.community_tools_service import (
    InvalidCommunityToolValue,
    StickyConfig,
    StickyPoll,
    normalize_https_url,
    normalize_poll,
    normalize_sticky,
    utc_now,
)
from stoney_verify.community_tools_runtime import StickyRuntime, should_refresh_sticky


def _sticky(**kwargs) -> StickyConfig:
    payload = {
        "guild_id": 1,
        "channel_id": 2,
        "content": "Remember the rules.",
    }
    payload.update(kwargs)
    return StickyConfig(**payload)


def test_sticky_normalization_preserves_documented_default_cadence_and_clamps_unsafe_speed() -> None:
    normal = normalize_sticky(_sticky())
    assert normal.interval_seconds == 15
    assert normal.message_threshold == 5

    clamped = normalize_sticky(_sticky(interval_seconds=1, message_threshold=999))
    assert clamped.interval_seconds == 15
    assert clamped.message_threshold == 100


def test_sticky_modes_require_real_content_and_poll_never_uses_webhook_persona() -> None:
    with pytest.raises(InvalidCommunityToolValue):
        normalize_sticky(_sticky(content="", mode="plain"))

    poll = normalize_sticky(
        _sticky(
            mode="poll",
            content="Question?",
            use_webhook=True,
            sender_name="Pretend Staff",
            sender_avatar_url="https://cdn.example.com/avatar.png",
        )
    )
    assert poll.use_webhook is False
    assert poll.sender_name == ""
    assert poll.sender_avatar_url == ""


def test_external_asset_urls_reject_local_private_targets_and_credentials() -> None:
    assert normalize_https_url("https://cdn.example.com/image.png") == "https://cdn.example.com/image.png"
    for bad in (
        "http://example.com/image.png",
        "https://localhost/image.png",
        "https://127.0.0.1/image.png",
        "https://10.0.0.1/image.png",
        "https://user:pass@example.com/image.png",
        "https://example.com:8443/image.png",
    ):
        with pytest.raises(InvalidCommunityToolValue):
            normalize_https_url(bad)


def test_sticky_schema_has_no_raw_webhook_secret_field() -> None:
    names = {field.name for field in fields(StickyConfig)}
    assert "webhook_url" not in names
    assert "webhook_token" not in names
    assert {"use_webhook", "sender_name", "sender_avatar_url"} <= names


def test_sticky_poll_is_one_choice_per_user_and_counts_votes() -> None:
    poll = normalize_poll(
        StickyPoll(
            guild_id=1,
            channel_id=2,
            question="Pick one",
            options=("A", "B", "C"),
            votes={"10": 0, "11": 2, "12": 2},
        )
    )
    assert poll.total_votes == 3
    assert poll.counts() == (1, 0, 2)


def test_sticky_poll_requires_two_to_seven_unique_choices() -> None:
    with pytest.raises(InvalidCommunityToolValue):
        normalize_poll(
            StickyPoll(guild_id=1, channel_id=2, question="Nope", options=("same", "same"), votes={})
        )
    with pytest.raises(InvalidCommunityToolValue):
        normalize_poll(
            StickyPoll(
                guild_id=1,
                channel_id=2,
                question="Too many",
                options=tuple(str(index) for index in range(8)),
                votes={},
            )
        )


def test_refresh_trigger_uses_time_or_message_threshold() -> None:
    now = utc_now()
    fresh = normalize_sticky(_sticky(last_sent_at=now, interval_seconds=30, message_threshold=5))
    assert should_refresh_sticky(fresh, message_count=4, now=now) is False
    assert should_refresh_sticky(fresh, message_count=5, now=now) is True
    assert should_refresh_sticky(fresh, message_count=1, now=now + timedelta(seconds=30)) is True


def test_disabled_sticky_never_refreshes() -> None:
    config = normalize_sticky(_sticky(enabled=False, last_sent_at=utc_now()))
    assert should_refresh_sticky(config, message_count=100, now=utc_now() + timedelta(hours=1)) is False


def test_activity_worker_honors_prior_trigger_and_releases_burst_lock() -> None:
    async def scenario() -> None:
        runtime = StickyRuntime(SimpleNamespace())
        runtime._pending_refreshes.add(2)
        calls: list[tuple[int, bool]] = []

        async def fake_refresh(channel, *, expected_config=None, force=False):
            calls.append((int(channel.id), bool(force)))
            assert expected_config is not None
            return None

        runtime.refresh_channel = fake_refresh  # type: ignore[method-assign]
        await runtime._refresh_from_activity(SimpleNamespace(id=2), _sticky())
        assert calls == [(2, True)]
        assert 2 not in runtime._pending_refreshes

    asyncio.run(scenario())
