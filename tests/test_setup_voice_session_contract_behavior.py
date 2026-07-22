from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import setup_legacy_voice_cleanup
from stoney_verify import setup_legacy_voice_cleanup_ui
from stoney_verify.commands_ext import public_setup_defaults as defaults
from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
from stoney_verify.commands_ext import public_setup_recommend as recommend


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class _FakeGuild:
    def __init__(self) -> None:
        self.id = 4242
        self.me = SimpleNamespace(guild_permissions=SimpleNamespace())

    def get_role(self, role_id: int) -> Any:
        _ = role_id
        return None

    def get_channel(self, channel_id: int) -> Any:
        _ = channel_id
        return None


class _FakeVoice:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class _FakeText:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class _FakeRole:
    def __init__(self, name: str, *, default: bool = False) -> None:
        self.name = name
        self._default = default

    def is_default(self) -> bool:
        return self._default


class _OverwriteGuild:
    def __init__(self) -> None:
        self.id = 5252
        self.default_role = _FakeRole("@everyone", default=True)
        self.me = _FakeRole("Dank Shield")


def _voice_enabled_config() -> dict[str, Any]:
    return {
        "guild_id": "4242",
        "setup_choice": "custom_setup",
        "tickets_enabled": True,
        "ticket_service_enabled": True,
        "verification_enabled": True,
        "basic_verify_enabled": True,
        "basic_button_verify_enabled": True,
        "voice_verification_enabled": True,
        "vc_verify_enabled": True,
        "voice_verify_enabled": True,
        "verification_allows_voice": True,
        "spam_guard_enabled": False,
        "moderation_enabled": True,
        "logs_enabled": True,
    }


def test_voice_setup_requires_the_correct_channel_types() -> None:
    voice = _FakeVoice(101)
    text = _FakeText(202)

    class Guild:
        def get_channel(self, channel_id: int) -> Any:
            return {101: voice, 202: text}.get(int(channel_id))

    guild = Guild()
    cfg = {
        "vc_verify_channel_id": "202",
        "vc_verify_queue_channel_id": "101",
    }

    assert (
        recommend._has_typed_channel(
            guild,
            cfg,
            _FakeVoice,
            "vc_verify_channel_id",
        )
        is False
    )
    assert (
        recommend._has_typed_channel(
            guild,
            cfg,
            _FakeText,
            "vc_verify_queue_channel_id",
        )
        is False
    )

    cfg = {
        "vc_verify_channel_id": "101",
        "vc_verify_queue_channel_id": "202",
    }
    assert recommend._has_typed_channel(
        guild,
        cfg,
        _FakeVoice,
        "vc_verify_channel_id",
    )
    assert recommend._has_typed_channel(
        guild,
        cfg,
        _FakeText,
        "vc_verify_queue_channel_id",
    )


def test_guided_voice_setup_does_not_require_verified_role_room_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _FakeGuild()
    cfg = _voice_enabled_config()

    async def load_config(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return cfg

    async def category_load(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(error="", rows=[SimpleNamespace(name="Support")])

    monkeypatch.setattr(recommend, "get_guild_config", load_config)
    monkeypatch.setattr(recommend, "_missing_setup_permissions", lambda *_args: [])
    monkeypatch.setattr(recommend, "_has_role", lambda *_args: True)
    monkeypatch.setattr(recommend, "_has_channel", lambda *_args: True)
    monkeypatch.setattr(recommend, "_has_typed_channel", lambda *_args: True)
    monkeypatch.setattr(recommend.solid, "_category_load", category_load)
    monkeypatch.setattr(
        recommend,
        "_verified_role_voice_access",
        lambda *_args: (
            False,
            "WRONG: grant every verified member Connect and Speak.",
            ("Connect", "Speak"),
        ),
        raising=False,
    )

    target = run(recommend._guided_setup_target(guild))

    assert target[0] == "ready"
    assert target[3] == "ready"


def test_health_check_describes_session_access_not_verified_role_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _FakeGuild()
    cfg = _voice_enabled_config()

    async def load_config(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return cfg

    async def category_load(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(error="", rows=[SimpleNamespace(name="Support")])

    monkeypatch.setattr(recommend, "get_guild_config", load_config)
    monkeypatch.setattr(recommend, "_missing_setup_permissions", lambda *_args: [])
    monkeypatch.setattr(recommend, "_has_role", lambda *_args: True)
    monkeypatch.setattr(recommend, "_has_channel", lambda *_args: True)
    monkeypatch.setattr(recommend, "_has_typed_channel", lambda *_args: True)
    monkeypatch.setattr(recommend.solid, "_category_load", category_load)
    monkeypatch.setattr(
        recommend,
        "_verified_role_voice_access",
        lambda *_args: (
            False,
            "WRONG VERIFIED ROLE ACCESS",
            ("Connect", "Speak"),
        ),
        raising=False,
    )

    embed = run(recommend._build_plain_setup_health_embed(guild))
    text = "\n".join(
        [str(embed.description or "")]
        + [str(field.value or "") for field in embed.fields]
    )

    assert "WRONG VERIFIED ROLE ACCESS" not in text
    assert "Allow Approved Members Into Voice Verify" not in text
    assert "active requester" in text
    assert "assigned staff" in text


def test_guided_voice_creation_builds_room_and_staff_queue_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def create_exact(
        _guild: Any,
        _cfg: Any,
        requirement_key: str,
    ) -> tuple[Any, list[str], list[str], list[str]]:
        if requirement_key == "voice_verify_channel":
            return (
                SimpleNamespace(id=301),
                [],
                ["Voice: <#301>"],
                [],
            )
        assert requirement_key == "voice_verify_staff_channel"
        return (
            SimpleNamespace(id=302),
            [],
            [],
            ["Channel: <#302>"],
        )

    monkeypatch.setattr(recommend, "_guided_create_exact_item", create_exact)

    payload, notes, created, reused = run(
        recommend._guided_create_voice_bundle(
            SimpleNamespace(id=4242),
            {},
        )
    )

    assert notes == []
    assert created == ["Voice: <#301>"]
    assert reused == ["Channel: <#302>"]
    assert payload["vc_verify_channel_id"] == "301"
    assert payload["vc_verify_channel_managed_id"] == "301"
    assert payload["vc_verify_queue_channel_id"] == "302"
    assert payload["vc_queue_channel_id"] == "302"
    assert payload["vc_request_channel_id"] == "302"
    assert payload["vc_verify_requests_channel_id"] == "302"
    assert "vc_verify_queue_channel_managed_id" not in payload


def test_default_voice_room_is_private_until_a_session_grants_member_access() -> None:
    guild = _OverwriteGuild()
    staff = _FakeRole("Support Team")
    control = _FakeRole("Bot Manager")
    unverified = _FakeRole("Unverified")

    overwrites = defaults._voice_overwrites(
        guild,
        staff,
        control,
        unverified,
    )

    everyone = overwrites[guild.default_role]
    assert everyone.view_channel is False
    assert everyone.connect is False
    assert everyone.speak is False

    waiting = overwrites[unverified]
    assert waiting.view_channel is False
    assert waiting.connect is False
    assert waiting.speak is False

    for broad_staff_role in (staff, control):
        permission = overwrites[broad_staff_role]
        assert permission.view_channel is False
        assert permission.connect is False
        assert permission.speak is False

    bot_permission = overwrites[guild.me]
    assert bot_permission.view_channel is True
    assert bot_permission.connect is True
    assert bot_permission.speak is True
    assert bot_permission.move_members is True
    assert bot_permission.manage_channels is True


def test_voice_off_routes_unproven_legacy_items_to_explicit_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, Any]] = []
    interaction = SimpleNamespace()
    guild = SimpleNamespace(id=4242)

    async def find_candidates(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            has_candidates=True,
            blocked_reason="",
            voice_id=901,
            queue_id=0,
        )

    async def open_review(
        interaction_arg: Any,
        *,
        result_message: str = "",
        already_deferred: bool = False,
    ) -> None:
        assert interaction_arg is interaction
        events.append((result_message, already_deferred))

    monkeypatch.setattr(
        setup_legacy_voice_cleanup,
        "find_legacy_voice_cleanup_candidates",
        find_candidates,
    )
    monkeypatch.setattr(
        setup_legacy_voice_cleanup_ui,
        "open_legacy_voice_cleanup_review",
        open_review,
    )

    opened = run(
        fresh._open_legacy_voice_cleanup_if_needed(
            interaction,
            guild,
            "Voice Verify is OFF. The old room was preserved because ownership could not be proven.",
            already_deferred=True,
        )
    )

    assert opened is True
    assert events == [
        (
            "Voice Verify is OFF. The old room was preserved because ownership could not be proven.",
            True,
        )
    ]
