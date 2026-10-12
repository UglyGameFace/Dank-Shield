"""Security and startup regression checks for the opt-in companion VLAN bridge."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from stoney_verify import voice_companion_bridge as bridge
from stoney_verify.voice_bridge_wire import (
    BridgeRejected, request_headers, response_body,
    response_signature, verify_request, verify_response,
)

KEY = "a-standalone-development-test-shared-key-long-enough-123456"
NONCE = "a" * 32
NOW = 1800000000


def test_cross_repo_signed_handshake_interop_fixture():
    headers = request_headers(KEY, now=NOW, nonce=NONCE)
    assert headers["X-WYM-Signature"] == "f8672ae48c8bd54d358cb197e9391b2600921c14f7c3adec0d884ebfa09bd26c"
    assert verify_request(headers, KEY, now=NOW) == NONCE
    body = response_body()
    assert verify_response(KEY, NONCE, body, response_signature(KEY, NONCE, body))
    assert json.loads(body)["voice_capture_authorized"] is False


@pytest.mark.asyncio
async def test_private_bridge_disabled_has_no_listener(monkeypatch):
    monkeypatch.delenv("DANK_VOICE_BRIDGE_ENABLED", raising=False)
    assert await bridge.start_private_voice_bridge() is False


@pytest.mark.asyncio
async def test_private_bridge_rejects_unsigned_and_replayed_requests(monkeypatch):
    monkeypatch.setenv("DANK_VOICE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("DANK_VOICE_BRIDGE_SECRET", KEY)
    monkeypatch.setattr(bridge.time, "time", lambda: NOW)
    bridge._USED_NONCES.clear()
    request = SimpleNamespace(headers=request_headers(KEY, now=NOW, nonce=NONCE))
    denied = await bridge.bridge_status(SimpleNamespace(headers={}))
    assert denied.status == 401
    first = await bridge.bridge_status(request)
    assert first.status == 200
    assert verify_response(KEY, NONCE, first.body, first.headers["X-WYM-Response-Signature"])
    assert json.loads(first.body)["voice_capture_authorized"] is False
    second = await bridge.bridge_status(request)
    assert second.status == 409
    assert await bridge.bridge_status(SimpleNamespace(headers=request_headers(KEY, now=NOW-31, nonce="b"*32))).status == 401
    bridge._USED_NONCES.clear()


@pytest.mark.asyncio
async def test_private_bridge_refuses_weak_secret_before_binding(monkeypatch):
    monkeypatch.setenv("DANK_VOICE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("DANK_VOICE_BRIDGE_SECRET", "bad")
    monkeypatch.setattr(bridge, "_BRIDGE_RUNNER", None)
    with pytest.raises(BridgeRejected):
        await bridge.start_private_voice_bridge()


def test_ordinary_structured_api_stays_bound_to_localhost():
    import inspect
    from stoney_verify.api_new import server
    from stoney_verify import app
    source = inspect.getsource(app._start_private_voice_bridge_once)
    assert "_start_new_api_once" not in source
    assert server._api_bind_host() == "127.0.0.1" or server._api_bind_host() not in ("", "0.0.0.0")
    assert "DANK_VOICE_BRIDGE_ENABLED" in source


def test_bridge_has_no_monitoring_or_consent_implementation():
    import inspect
    source = inspect.getsource(bridge)
    for prohibited in ("VoiceRecvClient", "listen(", "start_voice_safety(", "set_voice_safety_consent", "get_profile_guild_settings"):
        assert prohibited not in source
