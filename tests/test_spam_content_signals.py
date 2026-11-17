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


@pytest.mark.parametrize(
    ("text", "recent", "mode", "expected_mode"),
    [
        (
            "Heyy I'm not here for games lol I'm literally the hottest girl on discord "
            "u tryna play with a 10/10 hmu if or wanna get some hot treats "
            "We can smoke together and have fun too",
            True, "ban", "timeout",
        ),
        (
            "@everyone Looking to give out my Pc, I upgraded and no longer need this one. "
            "DM if you are interested",
            False, "ban", "delete_only",
        ),
    ],
)
def test_live_spam_guard_processes_single_verified_post(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    recent: bool,
    mode: str,
    expected_mode: str,
) -> None:
    """Exercise the actual async handler, not just classifier/source strings."""
    import asyncio
    from stoney_verify import spam_guard

    class FakeMember:
        bot = False
        id = 9001
        roles = []

        def __init__(self):
            self.joined_at = datetime.now(timezone.utc) - timedelta(
                hours=1 if recent else 24 * 90
            )
            self.created_at = datetime.now(timezone.utc) - timedelta(
                days=1 if recent else 300
            )

    class FakeTextChannel:
        id = 9002

    member = FakeMember()
    guild = SimpleNamespace(id=9003)
    message = SimpleNamespace(
        guild=guild,
        author=member,
        channel=FakeTextChannel(),
        content=text,
        id=9004,
        mention_everyone=text.startswith("@everyone"),
    )
    settings = spam_guard._default_settings(guild.id)
    settings["mode"] = mode
    settings["apply_to_verified_users"] = True

    actions: list[str] = []
    deleted: list[list[dict]] = []
    logged: list[dict] = []

    async def fake_get_settings(_guild_id):
        return settings

    async def fake_delete(*, guild, refs, reason):
        deleted.append(refs)
        return len(refs)

    async def fake_action(*, guild, member, settings, reason):
        actions.append(settings["mode"])
        return "deleted-or-restricted", None

    async def fake_log(**kwargs):
        logged.append(kwargs)

    async def fake_risk(*args, **kwargs):
        return None

    monkeypatch.setattr(spam_guard.discord, "Member", FakeMember)
    monkeypatch.setattr(spam_guard.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(spam_guard, "_is_staffish", lambda _m: False)
    monkeypatch.setattr(spam_guard, "_is_verifiedish", lambda _m: True)
    monkeypatch.setattr(spam_guard, "get_spam_settings", fake_get_settings)
    monkeypatch.setattr(spam_guard, "_delete_recent_messages", fake_delete)
    monkeypatch.setattr(spam_guard, "_apply_mode_action", fake_action)
    monkeypatch.setattr(spam_guard, "_log_trigger", fake_log)
    monkeypatch.setattr(spam_guard, "_record_security_behavior_for_incident", fake_risk)
    spam_guard._MESSAGE_WINDOWS.clear()
    spam_guard._LOCKS.clear()

    try:
        assert asyncio.run(spam_guard.handle_incoming_spam_message(message)) is True
        assert actions == [expected_mode]
        assert deleted and deleted[0][0]["message_id"] == message.id
        assert logged and "single-message promotional" in " ".join(logged[0]["reasons"])
    finally:
        spam_guard._MESSAGE_WINDOWS.clear()
        spam_guard._LOCKS.clear()


def test_verified_member_opt_out_and_allowed_promo_channel(monkeypatch: pytest.MonkeyPatch):
    """Saved guild exceptions must not be silently discarded."""
    import asyncio
    from stoney_verify import spam_guard

    class FakeMember:
        bot = False
        id = 9101
        roles = []
    class FakeTextChannel:
        id = 9102

    member = FakeMember()
    guild = SimpleNamespace(id=9103)
    msg = SimpleNamespace(
        guild=guild, author=member, channel=FakeTextChannel(),
        id=9104, mention_everyone=True,
        content="@everyone Giving away my spare PC, DM me if interested",
    )
    settings = spam_guard._default_settings(guild.id)
    actions: list[str] = []

    async def get_settings(_):
        return settings

    async def fake_action(**kwargs):
        actions.append("called")
        return "none", None

    monkeypatch.setattr(spam_guard.discord, "Member", FakeMember)
    monkeypatch.setattr(spam_guard.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(spam_guard, "_is_staffish", lambda _: False)
    monkeypatch.setattr(spam_guard, "_is_verifiedish", lambda _: True)
    monkeypatch.setattr(spam_guard, "get_spam_settings", get_settings)
    monkeypatch.setattr(spam_guard, "_apply_mode_action", fake_action)
    spam_guard._MESSAGE_WINDOWS.clear()
    spam_guard._LOCKS.clear()
    try:
        settings["apply_to_verified_users"] = False
        assert asyncio.run(spam_guard.handle_incoming_spam_message(msg)) is False
        settings["apply_to_verified_users"] = True
        settings["allowed_channel_ids"] = [str(msg.channel.id)]
        assert asyncio.run(spam_guard.handle_incoming_spam_message(msg)) is False
        assert not actions
    finally:
        spam_guard._MESSAGE_WINDOWS.clear()
        spam_guard._LOCKS.clear()
