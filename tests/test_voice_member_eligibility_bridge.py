"""Read-only, signed member eligibility bridge regression tests for Voice Safety #517."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stoney_verify import voice_companion_bridge as bridge
from stoney_verify.voice_member_wire import (
    CHECK_PATH, MemberCheckRejected, check_request_body, check_response_body,
    sign_check_request, sign_check_response, verify_check_request, verify_check_response,
)

KEY = "a-standalone-development-test-shared-key-long-enough-123456"
NOW = 1800000000
NONCE = "c" * 32
GUILD, MEMBER, CHANNEL = 123456789123456789, 222222222222222222, 333333333333333333


def _request(body: bytes, headers: dict[str, str]):
    return SimpleNamespace(
        content_length=len(body), headers=headers,
        content=SimpleNamespace(read=AsyncMock(return_value=body)),
    )


def test_member_check_roundtrip_and_signed_authority_is_never_audio():
    body = check_request_body(GUILD, MEMBER, CHANNEL)
    hdrs = sign_check_request(KEY, body, now=NOW, nonce=NONCE)
    assert verify_check_request(hdrs, KEY, body, now=NOW) == (GUILD, MEMBER, CHANNEL, NONCE)
    reply = check_response_body(consent_current=True, policy_matches=True)
    decoded = verify_check_response(KEY, NONCE, body, reply, sign_check_response(KEY, NONCE, body, reply))
    assert decoded == {"consent_current": True, "policy_matches": True}
    assert json.loads(reply)["voice_capture_authorized"] is False
    assert b"member_id" not in reply and b"guild_id" not in reply
    assert CHECK_PATH != "/v1/status"


def test_member_check_signature_binds_request_method_route_nonce_and_all_ids():
    body = check_request_body(GUILD, MEMBER, CHANNEL)
    headers = sign_check_request(KEY, body, now=NOW, nonce=NONCE)
    for altered in (
        check_request_body(GUILD, MEMBER + 1, CHANNEL),
        check_request_body(GUILD + 1, MEMBER, CHANNEL),
        check_request_body(GUILD, MEMBER, CHANNEL + 1),
        body + b" ",
        body.replace(b'"member_id":', b'"member_id":1,"member_id":'),
    ):
        try:
            verify_check_request(headers, KEY, altered, now=NOW)
            assert False, "request was not bound to exact bytes and schema"
        except MemberCheckRejected:
            pass
    try:
        verify_check_request(headers, KEY, body, now=NOW + 31)
        assert False
    except MemberCheckRejected:
        pass
    try:
        verify_check_request(headers, "another-development-key-which-should-not-authenticate", body, now=NOW)
        assert False
    except MemberCheckRejected:
        pass
    assert verify_check_request(headers, KEY, body, now=NOW) == (GUILD, MEMBER, CHANNEL, NONCE)


def test_member_route_replay_optout_and_storage_failures_fail_closed(monkeypatch):
    monkeypatch.setenv("DANK_VOICE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("DANK_VOICE_BRIDGE_SECRET", KEY)
    monkeypatch.setattr(bridge.time, "time", lambda: NOW)
    bridge._USED_NONCES.clear()
    body = check_request_body(GUILD, MEMBER, CHANNEL)
    good = sign_check_request(KEY, body, now=NOW, nonce=NONCE)
    calls = []

    async def state(gid, uid, cid):
        calls.append((gid, uid, cid))
        return True, True

    monkeypatch.setattr(bridge, "_member_policy_and_consent", state)

    async def check():
        denied = await bridge.member_eligibility(_request(body, {}))
        assert denied.status == 401
        first = await bridge.member_eligibility(_request(body, good))
        assert first.status == 200
        reply = verify_check_response(KEY, NONCE, body, first.body, first.headers["X-WYM-Response-Signature"])
        assert reply["consent_current"] and reply["policy_matches"]
        assert calls == [(GUILD, MEMBER, CHANNEL)]
        assert (await bridge.member_eligibility(_request(body, good))).status == 409

        async def withdrew(*_):
            return False, True
        monkeypatch.setattr(bridge, "_member_policy_and_consent", withdrew)
        fresh = sign_check_request(KEY, body, now=NOW, nonce="d" * 32)
        withdrawn = await bridge.member_eligibility(_request(body, fresh))
        assert verify_check_response(KEY, "d" * 32, body, withdrawn.body,
                                     withdrawn.headers["X-WYM-Response-Signature"])["consent_current"] is False

        async def unavailable(*_):
            raise RuntimeError("simulated storage unavailable, never display")
        monkeypatch.setattr(bridge, "_member_policy_and_consent", unavailable)
        fresh = sign_check_request(KEY, body, now=NOW, nonce="e" * 32)
        denied_store = await bridge.member_eligibility(_request(body, fresh))
        assert denied_store.status == 200
        assert verify_check_response(KEY, "e" * 32, body, denied_store.body,
                                     denied_store.headers["X-WYM-Response-Signature"]) == {
                                         "consent_current": False, "policy_matches": False}
        assert b"simulated" not in denied_store.body
    asyncio.run(check())
    bridge._USED_NONCES.clear()


def test_authoritative_policy_consent_query_is_fail_closed(monkeypatch):
    import stoney_verify.globals as global_state
    import stoney_verify.guild_config as guild_config
    import stoney_verify.voice_safety_consent as consent
    monkeypatch.setattr(global_state, "bot", SimpleNamespace(
        get_guild=lambda gid: SimpleNamespace(get_channel=lambda cid: object()) if gid == GUILD else None,
    ))
    config = {"voice_safety_enabled": True, "voice_safety_voice_channel_id": str(CHANNEL),
              "voice_safety_staff_channel_id": "111", "voice_safety_notice_channel_id": "222"}
    async def fetch(gid, *, refresh=False):
        assert refresh is True
        return config
    async def consent_read(gid, uid):
        return uid == MEMBER
    monkeypatch.setattr(guild_config, "get_guild_config", fetch)
    monkeypatch.setattr(consent, "read_voice_safety_agreement", consent_read)

    async def check():
        assert await bridge._member_policy_and_consent(GUILD, MEMBER, CHANNEL) == (True, True)
        assert await bridge._member_policy_and_consent(GUILD, MEMBER + 1, CHANNEL) == (False, True)
        assert await bridge._member_policy_and_consent(GUILD + 1, MEMBER, CHANNEL) == (False, False)
        config["voice_safety_enabled"] = False
        assert await bridge._member_policy_and_consent(GUILD, MEMBER, CHANNEL) == (False, False)
        config["voice_safety_enabled"] = True
        assert await bridge._member_policy_and_consent(GUILD, MEMBER, CHANNEL + 1) == (False, False)
    asyncio.run(check())


def test_member_reply_cannot_escalate_voice_capture_even_with_valid_signature():
    body = check_request_body(GUILD, MEMBER, CHANNEL)
    reply = check_response_body(consent_current=True, policy_matches=True)
    forged = dict(json.loads(reply), voice_capture_authorized=True)
    raw = json.dumps(forged).encode("utf-8")
    try:
        verify_check_response(KEY, NONCE, body, raw, sign_check_response(KEY, NONCE, body, raw))
        assert False, "privileged signed result must be rejected"
    except MemberCheckRejected:
        pass
