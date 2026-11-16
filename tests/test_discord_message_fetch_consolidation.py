"""Live message owners use the shared Discord API safety helper.

Keep every fetch in the native feature owner. The existing helper provides
per-channel serialization, concurrent duplicate coalescing, and the native
Discord.py HTTP errors without another retry or monkeypatch layer.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest


OWNERS = (
    "embed_studio_live_runtime",
    "embed_studio_live_ui",
    "profile_card_runtime",
    "profile_card_runtime_core",
    "trusted_advert_cleanup",
    "community_lfg_public_panel",
    "share_router_download_queue",
    "toke_live_ui",
    "welcome_message",
    "spam_guard",
    "interaction_handlers",
)


@pytest.mark.parametrize("module", OWNERS)
def test_live_message_owner_fetch_is_coordinated(module: str) -> None:
    path = Path("stoney_verify") / f"{module}.py"
    source = path.read_text(encoding="utf-8")
    parsed = ast.parse(source, filename=str(path))
    helpers = [
        node for node in ast.walk(parsed)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "fetch_message_with_api_safety"
    ]
    direct = [
        node for node in ast.walk(parsed)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fetch_message"
    ]
    assert helpers, f"{module} is missing its shared protected fetch"
    assert not direct, f"{module} bypasses shared message-fetch safety"
    assert "from .startup_guards.discord_api_safety import fetch_message_with_api_safety" in source


def test_original_safety_helper_remains_the_single_fetch_owner() -> None:
    safety = Path("stoney_verify/startup_guards/discord_api_safety.py").read_text(encoding="utf-8")
    assert "async def fetch_message_with_api_safety(" in safety
    assert "await asyncio.shield(existing)" in safety
    assert "async with lock:" in safety
    assert "return await channel.fetch_message(resolved_message_id)" in safety
