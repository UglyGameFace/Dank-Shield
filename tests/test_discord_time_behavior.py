from __future__ import annotations

from datetime import datetime, timedelta, timezone

from stoney_verify.discord_time import (
    coerce_datetime,
    discord_timestamp,
    discord_timestamp_pair,
)


def test_iso_utc_timestamp_uses_discord_localized_markup() -> None:
    value = "2026-07-21T00:40:31+00:00"

    assert discord_timestamp(value, style="f") == "<t:1784594431:f>"
    assert discord_timestamp(value, style="R") == "<t:1784594431:R>"


def test_offset_timestamp_represents_the_same_instant() -> None:
    utc_value = "2026-07-21T00:40:31+00:00"
    eastern_summer_value = "2026-07-20T20:40:31-04:00"

    assert discord_timestamp(utc_value) == discord_timestamp(eastern_summer_value)


def test_naive_timestamp_is_treated_as_utc_storage_value() -> None:
    parsed = coerce_datetime(datetime(2026, 7, 21, 0, 40, 31))

    assert parsed == datetime(2026, 7, 21, 0, 40, 31, tzinfo=timezone.utc)


def test_timestamp_pair_contains_absolute_and_relative_discord_styles() -> None:
    rendered = discord_timestamp_pair("2026-07-21T00:40:31Z")

    assert rendered == "<t:1784594431:f> • <t:1784594431:R>"
    assert "UTC" not in rendered


def test_invalid_timestamp_fails_closed_to_plain_fallback() -> None:
    assert discord_timestamp("not-a-date") == "Unknown time"
    assert discord_timestamp_pair(None, fallback="Time unavailable") == "Time unavailable"


def test_invalid_style_falls_back_to_localized_short_date_time() -> None:
    value = datetime(2026, 7, 21, 0, 40, 31, tzinfo=timezone(timedelta(hours=-4)))

    assert discord_timestamp(value, style="unsupported").endswith(":f>")
