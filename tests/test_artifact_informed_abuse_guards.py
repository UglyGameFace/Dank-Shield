from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import abuse_burst_detector as detector
from stoney_verify import spam_guard_channel_flood_runtime as channel_guard
from stoney_verify import spam_guard_webhook_runtime as webhook_guard


class FakeMessage:
    def __init__(self, *, guild_id: int, channel_id: int, author_id: int, content: str = "x", webhook_id: int = 0):
        self.guild = SimpleNamespace(id=guild_id)
        self.channel = SimpleNamespace(id=channel_id)
        self.author = SimpleNamespace(id=author_id, bot=False, roles=[])
        self.content = content
        self.webhook_id = webhook_id or None
        self.deleted = 0

    async def delete(self) -> None:
        self.deleted += 1


def setup_function() -> None:
    detector.reset_for_tests()
    webhook_guard._BLOCK_UNTIL.clear()  # noqa: SLF001
    channel_guard._BLOCK_UNTIL.clear()  # noqa: SLF001


def test_webhook_detector_requires_extreme_burst_and_expires_old_events() -> None:
    messages = [FakeMessage(guild_id=1, channel_id=2, author_id=3, webhook_id=99) for _ in range(detector.WEBHOOK_MESSAGE_THRESHOLD)]
    for index, message in enumerate(messages[:-1]):
        fired, _rows = detector.record_webhook(message, now=float(index) * 0.1)
        assert fired is False

    fired, rows = detector.record_webhook(messages[-1], now=1.7)
    assert fired is True
    assert len(rows) == detector.WEBHOOK_MESSAGE_THRESHOLD

    detector.reset_for_tests()
    first = FakeMessage(guild_id=1, channel_id=2, author_id=3, webhook_id=99)
    fired, _rows = detector.record_webhook(first, now=0.0)
    assert fired is False
    late = FakeMessage(guild_id=1, channel_id=2, author_id=3, webhook_id=99)
    fired, rows = detector.record_webhook(late, now=detector.WEBHOOK_WINDOW_SECONDS + 1.0)
    assert fired is False
    assert rows == [late]


def test_channel_detector_catches_coordinated_duplicate_accounts() -> None:
    fired = False
    rows = []
    for index in range(detector.CHANNEL_DUPLICATE_THRESHOLD):
        message = FakeMessage(
            guild_id=1,
            channel_id=5,
            author_id=100 + (index % detector.CHANNEL_DUPLICATE_ACTORS),
            content="same raid line",
        )
        fired, rows = detector.record_channel(message, fingerprint="same raid line", now=float(index) * 0.1)
    assert fired is True
    assert len(rows) == detector.CHANNEL_DUPLICATE_THRESHOLD
    assert len({row[1] for row in rows}) == detector.CHANNEL_DUPLICATE_ACTORS


def test_channel_detector_catches_high_volume_even_with_unique_content() -> None:
    fired = False
    for index in range(detector.CHANNEL_VOLUME_THRESHOLD):
        message = FakeMessage(
            guild_id=1,
            channel_id=6,
            author_id=200 + (index % detector.CHANNEL_VOLUME_ACTORS),
            content=f"unique-{index}",
        )
        fired, _rows = detector.record_channel(message, fingerprint=f"unique-{index}", now=float(index) * 0.1)
    assert fired is True


def test_reaction_and_voice_detectors_require_sustained_churn() -> None:
    for index in range(detector.REACTION_THRESHOLD - 1):
        assert detector.record_reaction(1, 10, now=float(index) * 0.1) is False
    assert detector.record_reaction(1, 10, now=1.8) is True

    for index in range(detector.VOICE_TRANSITION_THRESHOLD - 1):
        assert detector.record_voice(1, 11, now=float(index)) is False
    assert detector.record_voice(1, 11, now=7.0) is True


def test_webhook_runtime_suppresses_confirmed_burst(monkeypatch) -> None:
    async def enabled(_guild_id: int):
        return {"enabled": True}

    monkeypatch.setattr(webhook_guard.spam_guard, "get_spam_settings", enabled)
    messages = [FakeMessage(guild_id=8, channel_id=9, author_id=10, webhook_id=777) for _ in range(detector.WEBHOOK_MESSAGE_THRESHOLD)]

    for message in messages:
        asyncio.run(webhook_guard._on_message(message))  # noqa: SLF001

    assert sum(message.deleted for message in messages) == detector.WEBHOOK_MESSAGE_THRESHOLD


def test_channel_runtime_uses_existing_spamguard_policy_for_participants(monkeypatch) -> None:
    async def settings(_guild_id: int):
        return {
            "enabled": True,
            "mode": "timeout",
            "exempt_user_ids": [],
            "exempt_role_ids": [],
        }

    actions: list[int] = []

    async def apply_mode_action(*, guild, member, settings, reason):
        assert guild.id == 15
        assert "coordinated" in reason
        actions.append(int(member.id))
        return "timeout:30m", None

    monkeypatch.setattr(channel_guard.spam_guard, "get_spam_settings", settings)
    monkeypatch.setattr(channel_guard.spam_guard, "_is_staffish", lambda _member: False)
    monkeypatch.setattr(channel_guard.spam_guard, "_member_has_any_role", lambda _member, _roles: False)
    monkeypatch.setattr(channel_guard.spam_guard, "_apply_mode_action", apply_mode_action)

    messages = [
        FakeMessage(
            guild_id=15,
            channel_id=16,
            author_id=300 + (index % detector.CHANNEL_DUPLICATE_ACTORS),
            content="same raid line",
        )
        for index in range(detector.CHANNEL_DUPLICATE_THRESHOLD)
    ]
    shared_guild = SimpleNamespace(id=15)
    for message in messages:
        message.guild = shared_guild
        asyncio.run(channel_guard._on_message(message))  # noqa: SLF001

    assert sum(message.deleted for message in messages) == detector.CHANNEL_DUPLICATE_THRESHOLD
    assert set(actions) == {300, 301, 302, 303}


def test_main_installs_artifact_informed_guards_after_antinuke_post_app() -> None:
    source = open("main.py", "r", encoding="utf-8").read()
    post_app = source.index("    install_anti_nuke_post_app(bot)")
    abuse = source.index("    _install_spam_guard_abuse_runtimes()", post_app)
    run = source.index("    _run_dank_shield()", abuse)
    assert post_app < abuse < run
