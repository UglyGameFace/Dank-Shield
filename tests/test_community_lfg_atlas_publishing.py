"""LFG Atlas outbound publishing: consent, response security, and revocation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify import community_lfg_atlas_publishing as atlas
from stoney_verify import community_lfg_atlas_publisher as worker
from stoney_verify.commands_ext import public_community_hub as ui


GID, UID = 111111111111111111, 222222222222222222
SID = "123e4567-e89b-12d3-a456-426614174000"


def _row(**change):
    data = {
        "session_id": SID, "guild_id": str(GID), "actor_id": str(UID),
        "game": "Minecraft", "title": "Looking for players: Minecraft",
        "region": "NA", "contact": "https://discord.gg/validcode",
        "state": "publishing", "claim_version": 2, "delete_attempts": 0,
        "revoke_requested": False,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    }
    data.update(change)
    return data


def test_official_games_slug_matching_and_unknowns():
    assert atlas.atlas_game_slug("Minecraft") == "minecraft"
    assert atlas.atlas_game_slug("Destiny 2") == "destiny2"
    assert atlas.atlas_game_slug("Call of Duty: MW3") == ""
    assert atlas.atlas_game_slug("ARC Raiders") == "arc-raiders"
    assert atlas.atlas_game_slug("Valorant") == "valorant"


def test_receipt_required_for_provider_owned_removal():
    payload = {
        "post": {"id": "post-unique-123", "url": "https://lfgatlas.pages.dev/p/post-unique-123"},
        "manage": {"edit_token": "private-edit-secret-987654321",
                   "delete_url": "https://lfgatlas-bot.jonsimm36.workers.dev/api/posts/post-unique-123"},
    }
    assert atlas._extract_post_receipt(payload) == (
        "post-unique-123", "https://lfgatlas.pages.dev/p/post-unique-123",
        "private-edit-secret-987654321",
    )
    clone = {**payload, "post": {**payload["post"], "url": "https://evil.example/collect"}}
    with pytest.raises(atlas.AtlasPublishError, match="trusted public"):
        atlas._extract_post_receipt(clone)
    without_key = {**payload, "manage": {}}
    with pytest.raises(atlas.AtlasPublishError, match="private deletion"):
        atlas._extract_post_receipt(without_key)
    from_delete_url = {
        "url": payload["post"]["url"],
        "manage": payload["manage"],
    }
    assert atlas._extract_post_receipt(from_delete_url)[0] == "post-unique-123"


def test_status_never_exposes_private_token_contact_or_actor():
    public = atlas.public_status(_row(
        state="posted", remote_url="https://lfgatlas.pages.dev/p/confirmed",
        edit_token="VERY_SECRET_PRIVATE_EDIT_TOKEN", last_error="",
    ))
    assert public == {
        "state": "posted",
        "url": "https://lfgatlas.pages.dev/p/confirmed",
        "region": "NA",
        "expires_at": public["expires_at"],
        "last_error": "",
        "revoke_requested": False,
    }
    assert "VERY_SECRET" not in repr(public)
    assert "validcode" not in repr(public)
    assert str(UID) not in repr(public)


def test_enqueue_rejects_bad_region_before_db():
    for region in ("", "https://evil.example/", "a", "@everyone", "x"*41):
        with pytest.raises(atlas.AtlasPublishError):
            asyncio.run(atlas.enqueue_host_publication(SID, GID, UID, region=region, hours=2))
    with pytest.raises(atlas.AtlasPublishError):
        asyncio.run(atlas.enqueue_host_publication(SID, GID, UID, region="NA", hours=999))


def test_post_payload_is_readonly_projection_not_session_notes(monkeypatch):
    payloads = []
    class Client:
        def __init__(self, *, timeout):
            assert timeout.total == 9
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
    async def respond(client, *, method, url, payload):
        payloads.append((method, url, payload))
        return 201, {
            "url": "https://lfgatlas.pages.dev/p/abc123",
            "id": "abc123",
            "manage": {"edit_token": "secret-token-123456789"},
        }
    monkeypatch.setattr(atlas.aiohttp, "ClientSession", Client)
    monkeypatch.setattr(atlas, "_read_json", respond)
    row = _row(notes="DO_NOT_PUBLISH", user_id="PERSONAL_ID")
    remote = asyncio.run(atlas.send_post(row))
    assert remote[0] == "abc123"
    method, url, payload = payloads[0]
    assert method == "POST" and url == atlas.API_POSTS
    assert payload["source"] == "dankshield" and payload["consent"] is True
    assert payload["type"] == "lfm" and payload["game"] == "minecraft"
    assert payload["contact"] == "https://discord.gg/validcode"
    assert "notes" not in payload and "actor_id" not in payload
    assert "DO_NOT_PUBLISH" not in repr(payload)


def test_only_explicit_host_job_can_be_sent_and_receipt_saved(monkeypatch):
    monkeypatch.setattr(worker, "_still_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "_post_with_pacing", AsyncMock(return_value=(
        "remote-123", "https://lfgatlas.pages.dev/p/remote-123", "PRIVATE-REVOKE-TOKEN",
    )))
    change = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "_update_claim", change)
    state = asyncio.run(worker.process_claim(_row()))
    assert state == "posted"
    assert change.await_args.args[1] == "posted"
    assert change.await_args.kwargs["changes"]["edit_token"] == "PRIVATE-REVOKE-TOKEN"


def test_invalid_or_revoked_host_never_calls_remote_post(monkeypatch):
    monkeypatch.setattr(worker, "_still_authorized", AsyncMock(return_value=False))
    send = AsyncMock()
    change = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "_post_with_pacing", send)
    monkeypatch.setattr(worker, "_update_claim", change)
    assert asyncio.run(worker.process_claim(_row())) == "revoked-before-publish"
    send.assert_not_awaited()
    assert change.await_args.args[1] == "failed"


def test_network_timeout_does_not_repost_uncertain_write(monkeypatch):
    monkeypatch.setattr(worker, "_still_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "_post_with_pacing", AsyncMock(side_effect=asyncio.TimeoutError()))
    change = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "_update_claim", change)
    assert asyncio.run(worker.process_claim(_row())) == "unknown"
    assert change.await_args.args[1] == "unknown"


def test_removal_purges_secret_only_after_provider_confirmation(monkeypatch):
    remove = AsyncMock(return_value=True)
    change = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "delete_post", remove)
    monkeypatch.setattr(worker, "_update_claim", change)
    row = _row(state="removing",remote_id="remote-123", edit_token="PRIVATE-EDIT-TOKEN")
    assert asyncio.run(worker.process_claim(row)) == "removed"
    remove.assert_awaited_once_with("remote-123", "PRIVATE-EDIT-TOKEN")
    assert change.await_args.args[1] == "removed"
    assert change.await_args.kwargs["changes"]["edit_token"] is None


def test_failure_to_store_remote_token_attempts_compensating_delete(monkeypatch):
    monkeypatch.setattr(worker, "_still_authorized", AsyncMock(return_value=True))
    monkeypatch.setattr(worker, "_post_with_pacing", AsyncMock(return_value=(
        "remote-123", "https://lfgatlas.pages.dev/p/remote-123", "PRIVATE-EDIT-TOKEN",
    )))
    monkeypatch.setattr(worker, "_update_claim", AsyncMock(return_value=False))
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "delete_post", delete)
    assert asyncio.run(worker.process_claim(_row())) == "unconfirmed"
    delete.assert_awaited_once_with("remote-123", "PRIVATE-EDIT-TOKEN")


def test_live_eligibility_requires_both_consents_and_host(monkeypatch):
    member = {"user_id": str(UID), "role": "host", "left_at": None}
    settings = {
        "enabled": True, "maintenance_mode": False, "public_lfg_enabled": True,
        "public_lfg_invite_url": "https://discord.gg/validcode",
        "public_lfg_display_name": "Sample Gaming Guild",
        "max_session_lifetime_seconds": 43200, "idle_timeout_seconds": 1800,
    }
    session = {
        "privacy": "public", "state": "open", "public_lfg_opt_in": True,
        "created_at": (datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat(),
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
    }
    from stoney_verify import community_hub_service as hub
    monkeypatch.setattr(hub, "get_session", AsyncMock(return_value=session))
    monkeypatch.setattr(hub, "get_settings", AsyncMock(return_value=settings))
    monkeypatch.setattr(hub, "list_session_members", AsyncMock(return_value=[member]))
    assert asyncio.run(worker._still_authorized(_row()))
    monkeypatch.setattr(hub, "list_session_members", AsyncMock(return_value=[
        {**member, "role": "member"},
    ]))
    assert not asyncio.run(worker._still_authorized(_row()))
    monkeypatch.setattr(hub, "list_session_members", AsyncMock(return_value=[member]))
    monkeypatch.setattr(hub, "get_settings", AsyncMock(return_value={
        **settings, "public_lfg_enabled": False,
    }))
    assert not asyncio.run(worker._still_authorized(_row()))


def test_control_is_one_host_consent_form_and_live_public_buttons():
    buttons = ui.SessionControlView(UID, session_id=SID).children
    labels = {getattr(x, "label", None) for x in buttons}
    assert {"Share Outside Server", "Advertise Externally",
            "Remove External Ad", "External Ad Status"} <= labels
    assert len(buttons) <= 25
    assert max(getattr(x, "row", 0) or 0 for x in buttons) <= 4
    modal = ui.ExternalAtlasPublishModal(SID, GID, UID)
    assert len(modal.children) == 3
    assert modal.confirm.label.startswith("Type POST")


def test_no_auto_outreach_and_sql_retains_only_private_token():
    root = Path(__file__).resolve().parents[1]
    src = (root / "stoney_verify/community_lfg_atlas_publisher.py").read_text()
    ui_code = (root / "stoney_verify/commands_ext/public_community_hub.py").read_text()
    migration = (root / "supabase/migrations/20261026194500_lfg_atlas_optin_publishing.sql").read_text()
    assert "start_atlas_publisher" in (root / "stoney_verify/app.py").read_text()
    assert "for update of p skip locked" in migration
    assert "m.role in ('host','cohost')" in migration
    assert "s.public_lfg_opt_in is true" in migration
    assert "public_lfg_enabled is true" in migration
    assert "revoke all on public.dank_community_atlas_publications" in migration
    assert ".delete_messages(" not in src and ".purge(" not in src
    assert "send_message(" not in src
    assert 'custom_id="dank:hub:manage:atlas:publish:v1"' in ui_code
