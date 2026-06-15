from __future__ import annotations

from stoney_verify.interaction_guard import (
    run_guarded_interaction,
    safe_send_interaction,
)


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.deferred = False
        self.sent: list[dict] = []

    def is_done(self) -> bool:
        return self.done

    async def defer(self, *, ephemeral: bool = True) -> None:
        self.done = True
        self.deferred = True
        self.defer_ephemeral = ephemeral

    async def send_message(self, **payload) -> None:
        self.done = True
        self.sent.append(payload)


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **payload) -> None:
        self.sent.append(payload)


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponse()
        self.followup = FakeFollowup()


async def test_safe_send_interaction_uses_initial_response_when_not_done():
    interaction = FakeInteraction()

    sent = await safe_send_interaction(interaction, content="hello", ephemeral=True)

    assert sent is True
    assert interaction.response.sent[0]["content"] == "hello"
    assert interaction.response.sent[0]["ephemeral"] is True
    assert interaction.followup.sent == []


async def test_safe_send_interaction_uses_followup_after_defer():
    interaction = FakeInteraction()
    await interaction.response.defer(ephemeral=True)

    sent = await safe_send_interaction(interaction, content="after defer", ephemeral=True)

    assert sent is True
    assert interaction.response.sent == []
    assert interaction.followup.sent[0]["content"] == "after defer"


async def test_run_guarded_interaction_defers_and_reports_success():
    interaction = FakeInteraction()
    called: list[str] = []

    async def action() -> None:
        called.append("ran")

    result = await run_guarded_interaction(interaction, action, defer=True, ephemeral=True)

    assert result.ok is True
    assert called == ["ran"]
    assert interaction.response.deferred is True
    assert interaction.followup.sent == []


async def test_run_guarded_interaction_sends_safe_error_on_exception():
    interaction = FakeInteraction()

    async def action() -> None:
        raise ValueError("bad thing")

    result = await run_guarded_interaction(interaction, action, defer=True, ephemeral=True)

    assert result.ok is False
    assert result.error_type == "ValueError"
    assert result.error_message == "bad thing"
    assert interaction.response.deferred is True
    assert len(interaction.followup.sent) == 1
    payload = interaction.followup.sent[0]
    assert payload["ephemeral"] is True
    assert payload["embed"].title == "❌ Command failed safely"
    assert "bad thing" in payload["embed"].description
