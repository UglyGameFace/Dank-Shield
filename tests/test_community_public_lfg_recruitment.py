"""Recruitment never leaks private server/user data or bypasses consent."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from stoney_verify import community_public_lfg as public
from stoney_verify import community_hub_service as hub


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "supabase/migrations/20261026083000_community_hub_public_lfg_recruitment.sql"
).read_text(encoding="utf-8")
UI = (ROOT / "stoney_verify/commands_ext/public_community_hub.py").read_text(encoding="utf-8")
SERVER = (ROOT / "stoney_verify/share_router_public_server.py").read_text(encoding="utf-8")


def _row(*, game="Minecraft", invite="https://discord.gg/the420lobby", **other):
    return {
        "game_name": game, "capacity": 6, "invite_url": invite,
        "community_name": "The 420 Lobby",
        "host_id": "123", "guild_id": "456", "notes": "private notes",
        "session_id": "opaque-uuid", "panel_channel_id": "999",
        **other,
    }


def test_only_whitelisted_fields_are_published():
    rows = public.sanitize_directory([_row()])
    assert rows == [{
        "game": "Minecraft", "community": "The 420 Lobby", "capacity": 6,
        "join_url": "https://discord.gg/the420lobby",
    }]
    page = public._page(rows, "")
    for forbidden in ("private notes", "opaque-uuid", "999", "host_id", "guild_id"):
        assert forbidden not in page
    assert "Minecraft" in page
    assert "Hosted by The 420 Lobby" in page
    assert "https://discord.gg/the420lobby" in page


def test_invalid_invites_and_html_are_rejected_or_escaped():
    assert not public.verified_invite_url("https://evil.example/invite/fake")
    assert not public.verified_invite_url("http://discord.gg/the420lobby")
    assert not public.verified_invite_url("https://discord.com/channels/123/456")
    assert not public.verified_invite_url("https://discord.gg/abc?redirect=https://evil.example")
    assert not public.verified_invite_url("https://discord.gg/ab\nHeader: injected")
    assert public.verified_invite_url("https://discord.gg/the420lobby")
    escaped = public._page(public.sanitize_directory([_row(game="<img src=x onerror=alert(1)>")]), "")
    assert "<img src=x" not in escaped
    assert "&lt;img" in escaped


def test_filters_use_only_structured_game_name():
    rows = [_row(game="Minecraft"), _row(game="Destiny 2"), _row(game="Minecraft", invite="http://bad")]
    assert [x["game"] for x in public.sanitize_directory(rows, game="mc")] == ["Minecraft"]
    assert public.sanitize_directory([_row(game="Minecraft", capacity=1)]) == []
    assert public.sanitize_directory([_row(game="Minecraft", capacity=999)]) == []


def test_public_url_uses_configured_https_origin(monkeypatch):
    monkeypatch.setenv("DANK_LFG_PUBLIC_BASE_URL", "https://lfg.example.org")
    assert public.public_lfg_base_url() == "https://lfg.example.org"
    monkeypatch.setenv("DANK_LFG_PUBLIC_BASE_URL", "http://attacker.example.org")
    assert public.public_lfg_base_url() == ""
    monkeypatch.setenv("DANK_LFG_PUBLIC_BASE_URL", "https://lfg.example.org@evil.example")
    # Host is untrusted as a configured deployment value, but URL must never
    # allow embedded credentials that could confuse users.
    assert public.public_lfg_base_url() == ""


def test_rate_limit_rejects_bursts_and_bounds_tracking(monkeypatch):
    monkeypatch.setattr(public, "_per_ip", {})
    monkeypatch.setattr(public, "_global_calls", public.deque())
    assert all(public._rate_allowed("peer1", now=100.0) for _ in range(20))
    assert not public._rate_allowed("peer1", now=100.0)
    assert public._rate_allowed("peer1", now=161.0)


def test_directory_success_is_privacy_safe_and_errors_are_unavailable(monkeypatch):
    monkeypatch.setattr(public, "_rate_allowed", lambda remote, now: True)
    mocked = AsyncMock(return_value=[_row(), _row(game="Halo Infinite")])
    monkeypatch.setattr(hub, "list_public_lfg_directory", mocked)
    request = SimpleNamespace(remote="127.0.0.1", query={"game": "Minecraft"}, path="/lfg.json")
    response = asyncio.run(public.public_lfg_directory(request))
    assert response.status == 200
    assert "Minecraft" in response.text
    assert "Halo Infinite" not in response.text
    assert "host_id" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    mocked.assert_awaited_once_with(limit=100)
    mocked.side_effect = hub.CommunityHubError("Missing migration")
    response = asyncio.run(public.public_lfg_directory(request))
    assert response.status == 503
    assert "Missing migration" not in response.text


def test_routes_use_existing_share_router_listener():
    app = web.Application()
    public.register_public_lfg_routes(app)
    resources = [resource.canonical for resource in app.router.resources()]
    assert resources == ["/lfg", "/lfg.json"]
    assert "register_public_lfg_routes(app)" in SERVER
    assert "register_share_video_routes(app)" in SERVER
    assert 'app.router.add_get("/health", _health)' in SERVER


def test_opt_in_and_guild_gate_default_off_and_no_member_data_exposed():
    assert "public_lfg_enabled boolean not null default false" in MIGRATION
    assert "public_lfg_opt_in boolean not null default false" in MIGRATION
    assert "h.enabled is true" in MIGRATION
    assert "h.maintenance_mode is false" in MIGRATION
    assert "h.public_lfg_enabled is true" in MIGRATION
    assert "s.public_lfg_opt_in is true" in MIGRATION
    assert "s.privacy = 'public'" in MIGRATION
    assert "s.state in ('open', 'forming', 'ready', 'active')" in MIGRATION
    assert "max_session_lifetime_seconds" in MIGRATION
    assert "idle_timeout_seconds" in MIGRATION
    assert "revoke all on function public.community_hub_public_lfg_directory" in MIGRATION
    assert "from public, anon, authenticated" in MIGRATION
    assert "grant execute on function public.community_hub_public_lfg_directory" in MIGRATION
    assert "to service_role" in MIGRATION
    assert "public_lfg_display_name" in MIGRATION
    assert "host_id" not in MIGRATION
    assert "panel_message_id" not in MIGRATION


def test_discord_controls_require_staff_and_host_consent():
    assert 'label="Public Recruiting"' in UI
    assert 'label="Share Outside Server"' in UI
    assert 'self.confirmation.value' in UI
    assert '_staff_authorized(interaction)' in UI
    assert 'interaction.client.fetch_invite(url)' in UI
    assert 'int(destination.id) != int(interaction.guild_id or 0)' in UI
    assert 'mine or {}).get("role")' in UI
    assert 'hub.set_session_public_lfg(' in UI
    assert '"public_lfg_enabled": False' in UI
    assert '"public_lfg_invite_url": url, "public_lfg_display_name": name, "public_lfg_enabled": True' in UI
