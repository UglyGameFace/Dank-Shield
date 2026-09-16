from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify.ui.resource_browser import DankGuildResourceBrowserView


class FakeResponse:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    def is_done(self) -> bool:
        return False

    async def send_message(self, message: str, *, ephemeral: bool, allowed_mentions=None) -> None:
        _ = allowed_mentions
        self.sent.append((str(message), bool(ephemeral)))


class FakeInteraction:
    def __init__(self, user_id: int) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.response = FakeResponse()
        self.followup = SimpleNamespace()


class FakeGuild:
    roles = []
    channels = []

    def get_role(self, _resource_id: int):
        return None

    def get_channel(self, _resource_id: int):
        return None


def test_resource_browser_rejects_wrong_owner_with_safe_reply() -> None:
    async def scenario() -> None:
        async def picked(_interaction, _resource):
            raise AssertionError("wrong owner must never reach selection callback")

        view = DankGuildResourceBrowserView(
            guild=FakeGuild(),
            author_id=111,
            resource_kinds=("role",),
            on_pick=picked,
            custom_id="test:owner_lock",
        )
        interaction = FakeInteraction(222)
        allowed = await view.interaction_check(interaction)  # type: ignore[arg-type]

        assert allowed is False
        assert interaction.response.sent
        message, ephemeral = interaction.response.sent[0]
        assert "Only the person who opened this picker" in message
        assert ephemeral is True

    asyncio.run(scenario())
