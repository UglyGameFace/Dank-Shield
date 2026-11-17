from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path

import pytest

from stoney_verify.spam_content_signals import (
    classify_unsolicited_promotion,
    recently_joined_or_created,
    single_message_enforcement_mode,
)


@pytest.mark.parametrize(
    ("text", "mention", "category"),
    [
        (
            "Heyy I'm not here for games lol I'm literally the hottest girl on discord "
            "u tryna play with a 10/10 hmu if or wanna get some hot treats "
            "We can smoke together and have fun too",
            False,
            "unsolicited_personal_promotion",
        ),
        (
            "@everyone Looking to give out my Pc Nothing is wrong with it, "
            "I recently upgraded and no longer need this one. DM if you are interested",
            True,
            "mass_mention_giveaway_dm",
        ),
        (
            "@everyone Giving away my old gaming rig; DM me",
            False,
            "mass_mention_giveaway_dm",
        ),
        (
            "Giving away my old PC! DM me to claim it",
            True,
            "mass_mention_giveaway_dm",
        ),
    ],
)
def test_screenshot_like_spam_is_detected(text: str, mention: bool, category: str) -> None:
    result = classify_unsolicited_promotion(text, mentions_everyone=mention)
    assert result is not None
    assert result.category == category
    assert len(result.signals) >= 3


@pytest.mark.parametrize(
    "text",
    [
        "",
        "I've upgraded my PC and donated the old one to a friend.",
        "@everyone Movie night tonight, DM me to help organize!",
        "I'm a 10/10 Fortnite player, hmu to play!",
        "Somebody in chat said they're the hottest player, lol.",
        "Giving away some extra gear after the tournament.",
        "Join us for the community PC building workshop.",
    ],
)
def test_benign_content_not_flagged(text: str) -> None:
    assert classify_unsolicited_promotion(text) is None


def test_low_signal_giveaway_without_mass_ping_not_blocked() -> None:
    assert classify_unsolicited_promotion("Giving away a PC; DM me") is None


def test_recent_account_or_join_is_reversible_risk_modifier() -> None:
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    recent_join = SimpleNamespace(
        joined_at=now - timedelta(hours=9),
        created_at=now - timedelta(days=300),
    )
    established = SimpleNamespace(
        joined_at=now - timedelta(days=90),
        created_at=now - timedelta(days=300),
    )
    recent_creation = SimpleNamespace(
        joined_at=now - timedelta(days=5),
        created_at=now - timedelta(days=6),
    )
    assert recently_joined_or_created(recent_join, now=now)
    assert recently_joined_or_created(recent_creation, now=now)
    assert not recently_joined_or_created(established, now=now)


@pytest.mark.parametrize("configured", ["kick", "ban", "timeout"])
def test_single_content_finding_never_kicks_or_bans(configured: str) -> None:
    assert single_message_enforcement_mode(configured, recent_member=True) == "timeout"
    assert single_message_enforcement_mode(configured, recent_member=False) == "delete_only"


def test_single_content_finding_preserves_lower_modes_and_quarantine() -> None:
    assert single_message_enforcement_mode("log_only", recent_member=True) == "log_only"
    assert single_message_enforcement_mode("delete_only", recent_member=True) == "delete_only"
    assert single_message_enforcement_mode("quarantine", recent_member=True) == "quarantine"
    assert single_message_enforcement_mode("quarantine", recent_member=False) == "delete_only"


def test_content_rule_integrated_with_canonical_spam_guard_and_edit_path() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "stoney_verify" / "spam_guard.py").read_text()
    assert "classify_unsolicited_promotion(" in source
    assert "fired_content_rule = True" in source
    assert "EVIDENCE_CONTENT_PROMO" in source
    assert "single_message_enforcement_mode(" in source
    assert '@bot.listen("on_message_edit")' in source
    assert "fired_content_rule=fired_content_rule" in source
