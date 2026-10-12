"""Private, authenticated Watch Yo Mouf transport smoke test.

An independently scoped VLAN listener, not the public site or general Bot API.
Never exposes consent records or allows voice monitoring. Disabled by default.
"""
from __future__ import annotations

import os
import time

from aiohttp import web

from .voice_bridge_wire import BridgeRejected, PATH, response_body, response_signature, secret_bytes, verify_request

_BRIDGE_RUNNER: web.AppRunner | None = None
_USED_NONCES: dict[str, int] = {}
_NONCE_CAP = 4096
_PORT = 8097


def _enabled() -> bool:
    return os.environ.get("DANK_VOICE_BRIDGE_ENABLED", "").strip().lower() in {"1", "yes", "true", "on"}


def _bridge_secret() -> str:
    return os.environ.get("DANK_VOICE_BRIDGE_SECRET", "")


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
    for old, expiry in tuple(_USED_NONCES.items()):
        if expiry < now:
            _USED_NONCES.pop(old, None)
    if nonce in _USED_NONCES or len(_USED_NONCES) >= _NONCE_CAP:
        return web.Response(status=409, text="Request rejected")
    _USED_NONCES[nonce] = now + 30
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
