"""Native owner-only Safety Reports are part of Dank Shield, not an external bot."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stoney_verify.commands_ext import member_command_center as center
from stoney_verify import network_safety as network

GUILD_ID = 123456789012345678
OWNER_ID = 223456789012345678
OTHER_ID = 323456789012345678
TARGET_ID = 423456789012345678


def _interaction(actor_id: int):
    return SimpleNamespace(
        user=SimpleNamespace(id=actor_id),
        guild=SimpleNamespace(id=GUILD_ID, owner_id=OWNER_ID),
        client=object(),
        response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock()),
    )


def test_non_owner_cannot_read_shared_incident_reports(monkeypatch):
    reader = AsyncMock(side_effect=AssertionError("Unauthorized cross-guild read"))
    reply = AsyncMock()
    monkeypatch.setattr(network, "reports_for_owner", reader)
    monkeypatch.setattr(center, "reply_ephemeral", reply)
    interaction = _interaction(OTHER_ID)

    asyncio.run(center._show_native_safety_reports(interaction, TARGET_ID))

    reader.assert_not_awaited()
    reply.assert_awaited_once()
    interaction.response.defer.assert_not_awaited()


def test_owner_uses_existing_native_network_and_review_view(monkeypatch):
    reports = [{
        "id": "incident-1",
        "source_label": "Opted-in Server",
        "category": "scam",
        "reason": "Owner documented a specific suspicious incident",
        "occurred_at": "2026-11-01T00:00:00Z",
    }]
    reader = AsyncMock(return_value=reports)
    replace = AsyncMock()
    monkeypatch.setattr(network, "reports_for_owner", reader)
    monkeypatch.setattr(center, "_replace_panel", replace)
    interaction = _interaction(OWNER_ID)

    asyncio.run(center._show_native_safety_reports(interaction, TARGET_ID))

    interaction.response.defer.assert_awaited_once()
    reader.assert_awaited_once_with(interaction.guild, TARGET_ID, interaction.client)
    replace.assert_awaited_once()
    embed = replace.await_args.kwargs["embed"]
    assert "Owner-attested" in embed.description
    assert "Opted-in Server" in embed.fields[0].name
    assert "specific suspicious incident" in embed.fields[0].value
    assert "No automatic action" in embed.footer.text


def test_network_lookup_failure_does_not_become_clean_history(monkeypatch):
    reader = AsyncMock(side_effect=RuntimeError("storage unavailable"))
    reply = AsyncMock()
    replace = AsyncMock()
    monkeypatch.setattr(network, "reports_for_owner", reader)
    monkeypatch.setattr(center, "reply_ephemeral", reply)
    monkeypatch.setattr(center, "_replace_panel", replace)
    interaction = _interaction(OWNER_ID)

    asyncio.run(center._show_native_safety_reports(interaction, TARGET_ID))

    reply.assert_awaited_once()
    assert "unavailable" in str(reply.await_args.args).lower()
    replace.assert_not_awaited()


def test_member_intelligence_reuses_one_network_and_no_third_party_dependency():
    source = (Path(__file__).resolve().parents[1] /
              "stoney_verify/commands_ext/member_command_center.py").read_text()
    assert source.count('label="Safety Reports"') == 2
    assert "from stoney_verify.network_safety import reports_for_owner" in source
    assert "from .public_network_safety import NetworkReportPages" in source
    assert "Only the physical server owner" in source
    assert "discord.AllowedMentions.none()" in source
