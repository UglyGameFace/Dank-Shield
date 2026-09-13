from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

from stoney_verify.commands_ext import public_protection_center as protection


def test_antinuke_owner_guard_rejects_delegated_admin(monkeypatch) -> None:
    messages: list[str] = []

    async def fake_send(_interaction, content: str = "", **_kwargs) -> None:
        messages.append(content)

    monkeypatch.setattr(protection, "_send_ephemeral", fake_send)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=123, owner_id=999),
        user=SimpleNamespace(id=555),
    )

    allowed = asyncio.run(protection._require_antinuke_owner(interaction))

    assert allowed is False
    assert messages
    assert "server owner" in messages[0].lower()
    assert "compromised delegated admin" in messages[0].lower()


def test_antinuke_owner_guard_accepts_actual_guild_owner(monkeypatch) -> None:
    messages: list[str] = []

    async def fake_send(_interaction, content: str = "", **_kwargs) -> None:
        messages.append(content)

    monkeypatch.setattr(protection, "_send_ephemeral", fake_send)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=123, owner_id=999),
        user=SimpleNamespace(id=999),
    )

    allowed = asyncio.run(protection._require_antinuke_owner(interaction))

    assert allowed is True
    assert messages == []


def test_every_antinuke_mutation_entrypoint_uses_owner_guard() -> None:
    callables = [
        protection._toggle_antinuke,
        protection._toggle_antinuke_mode,
        protection.AntiNukeTrustedIdsModal.on_submit,
        protection.AntiNukeThresholdsModal.on_submit,
        protection._open_antinuke_trusted_modal,
        protection._open_antinuke_thresholds_modal,
    ]

    for target in callables:
        source = inspect.getsource(target)
        assert "_require_antinuke_owner" in source, target.__qualname__
