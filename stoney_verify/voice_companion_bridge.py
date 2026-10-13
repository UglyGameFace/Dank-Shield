"""Private, authenticated Watch Yo Mouf transport smoke test.

An independently scoped VLAN listener, not the public site or general Bot API.
Never exposes consent records or allows voice monitoring. Disabled by default.
"""
from __future__ import annotations

import os
import time

from aiohttp import web

from .voice_bridge_wire import BridgeRejected, PATH, response_body, response_signature, secret_bytes, verify_request
from .voice_safety_coverage import channel_in_scope
from .voice_member_wire import (
    CHECK_PATH, MAX_CHECK_BYTES, MemberCheckRejected, check_response_body,
    sign_check_response, verify_check_request,
)

_BRIDGE_RUNNER: web.AppRunner | None = None
_USED_NONCES: dict[str, int] = {}
_NONCE_CAP = 4096
_PORT = 8097


def _enabled() -> bool:
    return os.environ.get("DANK_VOICE_BRIDGE_ENABLED", "").strip().lower() in {"1", "yes", "true", "on"}


def _bridge_secret() -> str:
    return os.environ.get("DANK_VOICE_BRIDGE_SECRET", "")


def _claim_nonce(nonce: str, now: int) -> bool:
    """One shared replay window for both bridge methods."""
    for old, expiry in tuple(_USED_NONCES.items()):
        if expiry < now:
            _USED_NONCES.pop(old, None)
    if nonce in _USED_NONCES or len(_USED_NONCES) >= _NONCE_CAP:
        return False
    _USED_NONCES[nonce] = now + 30
    return True


async def _member_policy_and_consent(guild_id: int, member_id: int, channel_id: int) -> tuple[bool, bool]:
    """Consult ONLY the authoritative stores; do not activate any receiver."""
    from .globals import bot
    from .guild_config import get_guild_config
    from .voice_safety_consent import read_voice_safety_agreement

    guild = bot.get_guild(guild_id)
    if guild is None or guild.get_channel(channel_id) is None:
        return False, False
    cfg = await get_guild_config(guild_id, refresh=True)
    policy_matches = bool(
        cfg.get("voice_safety_enabled") is True
        and channel_in_scope(guild, cfg, guild.get_channel(channel_id))
        and int(cfg.get("voice_safety_staff_channel_id") or 0) > 0
        and int(cfg.get("voice_safety_notice_channel_id") or 0) > 0
    )
    if not policy_matches:
        return False, False
    return bool(await read_voice_safety_agreement(guild_id, member_id)), True


async def member_eligibility(request: web.Request) -> web.Response:
    """Signed, per-request consent/policy check; NEVER authorizes audio."""
    if not _enabled():
        return web.Response(status=503, text="Unavailable")
    if request.content_length is not None and request.content_length > MAX_CHECK_BYTES:
        return web.Response(status=413)
    try:
        # Request.read() consumes the complete body with aiohttp's 1 KiB cap,
        # unlike StreamReader.read(n), which may return a partial TCP fragment.
        body = await request.read()
    except web.HTTPRequestEntityTooLarge:
        return web.Response(status=413)
    if len(body) > MAX_CHECK_BYTES:
        return web.Response(status=413)
    now = int(time.time())
    key = _bridge_secret()
    try:
        guild_id, member_id, channel_id, nonce = verify_check_request(
            request.headers, key, body, now=now,
        )
    except MemberCheckRejected:
        return web.Response(status=401, text="Unauthorized")
    if not _claim_nonce(nonce, now):
        return web.Response(status=409, text="Request rejected")
    # A store failure is a conservative deny. This read is never permission to capture.
    consent_current = policy_matches = False
    try:
        consent_current, policy_matches = await _member_policy_and_consent(
            guild_id, member_id, channel_id,
        )
    except Exception:
        pass
    payload = check_response_body(
        consent_current=consent_current, policy_matches=policy_matches,
    )
    return web.Response(
        body=payload, content_type="application/json",
        headers={
            "X-WYM-Response-Signature": sign_check_response(key, nonce, body, payload),
            "Cache-Control": "no-store",
        },
    )


async def bridge_status(request: web.Request) -> web.Response:
    """The only route; no guild/member IDs or policy actions are accepted."""
    if not _enabled():
        return web.Response(status=503, text="Unavailable")
    now = int(time.time())
    try:
        nonce = verify_request(request.headers, _bridge_secret(), now=now)
    except BridgeRejected:
        return web.Response(status=401, text="Unauthorized")
    # Exact nonce replay is rejected even if a captured request is still fresh.
    if not _claim_nonce(nonce, now):
        return web.Response(status=409, text="Request rejected")
    body = response_body()
    return web.Response(
        body=body, content_type="application/json",
        headers={"X-WYM-Response-Signature": response_signature(_bridge_secret(), nonce, body)},
    )


async def start_private_voice_bridge() -> bool:
    """Bind a distinct, signed VLAN-only diagnostics port. Never change Bot API."""
    global _BRIDGE_RUNNER
    if not _enabled():
        return False
    if _BRIDGE_RUNNER is not None:
        return True
    secret_bytes(_bridge_secret())  # Refuse to open a listener with a weak key.
    app = web.Application(client_max_size=1024)
    app.router.add_get(PATH, bridge_status)
    app.router.add_post(CHECK_PATH, member_eligibility)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        # Discloud exposes TYPE=site only through port 8080; this is VLAN:8097.
        site = web.TCPSite(runner, host="0.0.0.0", port=_PORT)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    _BRIDGE_RUNNER = runner
    return True


async def stop_private_voice_bridge() -> None:
    """Stop the test listener and release its replay state."""
    global _BRIDGE_RUNNER
    runner = _BRIDGE_RUNNER
    _BRIDGE_RUNNER = None
    _USED_NONCES.clear()
    if runner is not None:
        await runner.cleanup()
