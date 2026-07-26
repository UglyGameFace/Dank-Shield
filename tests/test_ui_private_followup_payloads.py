from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from stoney_verify import profile_signature_studio
from stoney_verify.commands_ext import public_command_hub, public_profile_cards_core
from stoney_verify import welcome_setup_ui


class _Response:
    def __init__(self, *, done: bool) -> None:
        self._done = done
        self.sent: dict[str, Any] | None = None

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, **payload: Any) -> None:
        self.sent = payload


class _Followup:
    def __init__(self) -> None:
        self.sent: dict[str, Any] | None = None

    async def send(self, **payload: Any) -> None:
        self.sent = payload


class _Interaction:
    def __init__(self, *, done: bool) -> None:
        self.response = _Response(done=done)
        self.followup = _Followup()


PrivateSender = Callable[..., Awaitable[None]]


def _run(sender: PrivateSender, **kwargs: Any) -> dict[str, Any]:
    interaction = _Interaction(done=True)
    asyncio.run(sender(interaction, **kwargs))
    assert interaction.followup.sent is not None
    return interaction.followup.sent


def test_profile_preview_followup_omits_none_view() -> None:
    embed = object()
    file = object()
    payload = _run(profile_signature_studio._private, embed=embed, file=file)

    assert payload["embed"] is embed
    assert payload["file"] is file
    assert "view" not in payload
    assert "content" not in payload


def test_content_only_followups_omit_none_optional_fields() -> None:
    for sender in (
        public_command_hub._private,
        public_profile_cards_core._send_private,
        welcome_setup_ui._send_private,
    ):
        payload = _run(sender, content="ok")
        assert payload["content"] == "ok"
        assert "embed" not in payload
        assert "view" not in payload
