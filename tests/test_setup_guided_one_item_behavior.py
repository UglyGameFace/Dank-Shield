from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify.commands_ext import (
    public_setup_defaults as defaults,
)
from stoney_verify.commands_ext import (
    public_setup_recommend as recommend,
)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class FakeGuild:
    def __init__(self) -> None:
        self.id = 4242
        self.me = None

    def get_role(self, role_id: int) -> None:
        return None

    def get_channel(self, channel_id: int) -> None:
        return None


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = FakeGuild()
        self.user = SimpleNamespace(id=99)


@pytest.mark.parametrize(
    ("requirement_key", "expected_keys"),
    (
        (
            "ticket_staff_role",
            {"staff_role_id", "vc_staff_role_id"},
        ),
        (
            "ticket_folder",
            {"ticket_category_id"},
        ),
        (
            "verification_channel",
            {
                "verify_channel_id",
                "verification_channel_id",
            },
        ),
        (
            "verified_role",
            {"verified_role_id"},
        ),
        (
            "voice_verify_channel",
            {"vc_verify_channel_id"},
        ),
        (
            "voice_verify_staff_channel",
            {
                "vc_verify_queue_channel_id",
                "vc_queue_channel_id",
                "vc_request_channel_id",
                "vc_verify_requests_channel_id",
            },
        ),
        (
            "modlog_channel",
            {
                "modlog_channel_id",
                "raidlog_channel_id",
                "force_verify_log_channel_id",
            },
        ),
    ),
)
def test_payload_saves_every_canonical_alias(
    requirement_key: str,
    expected_keys: set[str],
) -> None:
    payload = recommend._guided_item_payload(
        requirement_key,
        123456,
    )

    assert set(payload) == expected_keys
    assert set(payload.values()) == {"123456"}


@pytest.mark.parametrize(
    "requirement_key",
    (
        "ticket_staff_role",
        "ticket_folder",
        "verification_channel",
        "verified_role",
        "voice_verify_channel",
        "voice_verify_staff_channel",
        "modlog_channel",
    ),
)
def test_each_requirement_builds_one_combined_screen(
    requirement_key: str,
) -> None:
    view = recommend.GuidedOneItemView(
        requirement_key=requirement_key,
    )

    labels = {
        str(getattr(child, "label", "") or "")
        for child in view.children
    }
    placeholders = {
        str(getattr(child, "placeholder", "") or "")
        for child in view.children
    }

    assert "Create this for me" in labels
    assert "Back to Guided Setup" in labels
    assert "Choose one I already have" in placeholders
    assert len(view.children) == 3


def test_existing_selection_saves_then_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[tuple[str, Any]] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def defer(*args: Any, **kwargs: Any) -> None:
        events.append(("defer", None))

    async def current(*args: Any, **kwargs: Any) -> bool:
        return True

    async def save(
        interaction_arg: Any,
        payload: dict[str, str],
    ) -> None:
        events.append(("save", payload))

    async def guided(
        interaction_arg: Any,
        **kwargs: Any,
    ) -> None:
        events.append(("guided", kwargs))

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_safe_defer_update",
        defer,
    )
    monkeypatch.setattr(
        recommend,
        "_guided_step_is_current",
        current,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_save_config",
        save,
    )
    monkeypatch.setattr(
        recommend,
        "_open_guided_setup",
        guided,
    )

    run(
        recommend._guided_save_existing_item(
            interaction,
            "verification_channel",
            SimpleNamespace(id=555),
        )
    )

    assert events[0][0] == "defer"
    assert events[1] == (
        "save",
        {
            "verify_channel_id": "555",
            "verification_channel_id": "555",
        },
    )
    assert events[2][0] == "guided"
    assert "next setup step" in events[2][1]["saved_message"]


def test_stale_existing_selection_cannot_overwrite_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[str] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def defer(*args: Any, **kwargs: Any) -> None:
        events.append("defer")

    async def stale(*args: Any, **kwargs: Any) -> bool:
        return False

    async def forbidden_save(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("stale selection attempted to save")

    async def guided(*args: Any, **kwargs: Any) -> None:
        events.append("guided")

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_safe_defer_update",
        defer,
    )
    monkeypatch.setattr(
        recommend,
        "_guided_step_is_current",
        stale,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_save_config",
        forbidden_save,
    )
    monkeypatch.setattr(
        recommend,
        "_open_guided_setup",
        guided,
    )

    run(
        recommend._guided_save_existing_item(
            interaction,
            "verified_role",
            SimpleNamespace(id=777),
        )
    )

    assert events == ["defer", "guided"]


@pytest.mark.parametrize(
    (
        "requirement_key",
        "expected_helper",
        "expected_name",
        "expected_overwrites",
    ),
    (
        (
            "ticket_staff_role",
            "_ensure_role",
            "DEFAULT_STAFF_ROLE_NAME",
            None,
        ),
        (
            "ticket_folder",
            "_ensure_category",
            "TICKET_CATEGORY_NAME",
            "staff",
        ),
        (
            "verification_channel",
            "_ensure_text",
            "VERIFY_CHANNEL_NAME",
            "public",
        ),
        (
            "verified_role",
            "_ensure_role",
            "DEFAULT_VERIFIED_ROLE_NAME",
            None,
        ),
        (
            "voice_verify_channel",
            "_ensure_voice",
            "VC_VERIFY_CHANNEL_NAME",
            "voice",
        ),
        (
            "voice_verify_staff_channel",
            "_ensure_text",
            "VC_QUEUE_CHANNEL_NAME",
            "staff",
        ),
        (
            "modlog_channel",
            "_ensure_text",
            "MODLOG_CHANNEL_NAME",
            "staff",
        ),
    ),
)
def test_creation_uses_canonical_helper_name_and_permissions(
    monkeypatch: pytest.MonkeyPatch,
    requirement_key: str,
    expected_helper: str,
    expected_name: str,
    expected_overwrites: str | None,
) -> None:
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
    guild = FakeGuild()
    created_item = SimpleNamespace(id=9001)

    monkeypatch.setattr(
        recommend,
        "_guided_configured_role",
        lambda *args, **kwargs: SimpleNamespace(id=20),
    )
    monkeypatch.setattr(
        recommend,
        "_guided_configured_channel",
        lambda *args, **kwargs: SimpleNamespace(id=30),
    )
    monkeypatch.setattr(
        defaults,
        "_staff_overwrites",
        lambda *args, **kwargs: "staff",
    )
    monkeypatch.setattr(
        defaults,
        "_public_overwrites",
        lambda *args, **kwargs: "public",
    )
    monkeypatch.setattr(
        defaults,
        "_voice_overwrites",
        lambda *args, **kwargs: "voice",
    )

    async def record(
        helper_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        calls.append((helper_name, args, kwargs))
        return created_item

    async def ensure_role(*args: Any, **kwargs: Any) -> Any:
        return await record("_ensure_role", *args, **kwargs)

    async def ensure_category(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        return await record(
            "_ensure_category",
            *args,
            **kwargs,
        )

    async def ensure_text(*args: Any, **kwargs: Any) -> Any:
        return await record("_ensure_text", *args, **kwargs)

    async def ensure_voice(*args: Any, **kwargs: Any) -> Any:
        return await record("_ensure_voice", *args, **kwargs)

    monkeypatch.setattr(defaults, "_ensure_role", ensure_role)
    monkeypatch.setattr(
        defaults,
        "_ensure_category",
        ensure_category,
    )
    monkeypatch.setattr(defaults, "_ensure_text", ensure_text)
    monkeypatch.setattr(defaults, "_ensure_voice", ensure_voice)

    item, notes, created, reused = run(
        recommend._guided_create_exact_item(
            guild,
            {},
            requirement_key,
        )
    )

    assert item is created_item
    assert notes == []
    assert len(calls) == 1

    helper_name, args, kwargs = calls[0]

    assert helper_name == expected_helper
    assert args[0] is guild
    assert args[1] == getattr(defaults, expected_name)

    if expected_overwrites is not None:
        assert kwargs["overwrites"] == expected_overwrites

    if expected_helper == "_ensure_role":
        assert kwargs["create_missing_roles"] is True

    if requirement_key == "verification_channel":
        assert kwargs["topic"] == (
            "Press Verify here to receive server access."
        )

    if requirement_key == "voice_verify_staff_channel":
        assert kwargs["topic"] == (
            "Staff requests and updates for Voice Verify."
        )

    if requirement_key == "modlog_channel":
        assert kwargs["topic"] == (
            "Moderation and security logs are posted here."
        )


def test_created_voice_staff_channel_saves_all_aliases_and_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[tuple[str, Any]] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def defer(*args: Any, **kwargs: Any) -> None:
        events.append(("defer", None))

    async def current(*args: Any, **kwargs: Any) -> bool:
        return True

    async def get_config(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {}

    async def create_exact(
        *args: Any,
        **kwargs: Any,
    ) -> tuple[Any, list[str], list[str], list[str]]:
        return (
            SimpleNamespace(id=8080),
            [],
            ["Channel: created"],
            [],
        )

    async def save(
        interaction_arg: Any,
        payload: dict[str, str],
    ) -> None:
        events.append(("save", payload))

    async def guided(
        interaction_arg: Any,
        **kwargs: Any,
    ) -> None:
        events.append(("guided", kwargs))

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_safe_defer_update",
        defer,
    )
    monkeypatch.setattr(
        recommend,
        "_guided_step_is_current",
        current,
    )
    monkeypatch.setattr(
        recommend,
        "get_guild_config",
        get_config,
    )
    monkeypatch.setattr(
        recommend,
        "_guided_create_exact_item",
        create_exact,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_save_config",
        save,
    )
    monkeypatch.setattr(
        recommend,
        "_open_guided_setup",
        guided,
    )

    run(
        recommend._guided_create_item(
            interaction,
            "voice_verify_staff_channel",
        )
    )

    assert events[0][0] == "defer"
    assert events[1] == (
        "save",
        {
            "vc_verify_queue_channel_id": "8080",
            "vc_queue_channel_id": "8080",
            "vc_request_channel_id": "8080",
            "vc_verify_requests_channel_id": "8080",
            "vc_verify_queue_channel_managed_id": "8080",
        },
    )
    assert events[2][0] == "guided"
    assert "Created this item" in events[2][1]["saved_message"]
