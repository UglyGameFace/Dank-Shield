from __future__ import annotations

import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
import pytest

from stoney_verify import cinema_site
from tools.probe_cinema_public_routes import (
    Probe,
    classify,
    parse_guild_id,
    validated_origin,
)


GUILD_ID = "1514374173517152418"


def _probe(name: str, status: int, content_type: str, data: object = "") -> Probe:
    body = json.dumps(data).encode() if not isinstance(data, str) else data.encode()
    return Probe(name, "/cinema/123/api/home", status, content_type, body)


def test_live_media_health_and_auth_debug_expect_real_json() -> None:
    health = _probe("health", 200, "application/json; charset=utf-8", {
        "ok": True,
        "service": "dank_torrent_media",
    })
    assert classify(health)[0]
    auth = _probe("auth-debug", 200, "application/json", {
        "contract": "signed-session-v8-snowflake-safe",
    })
    assert classify(auth)[0]
    assert not classify(_probe("health", 200, "text/html", "<html>fallback</html>"))[0]
    assert not classify(_probe("auth-debug", 200, "text/html", "<html>fallback</html>"))[0]
    assert not classify(_probe("health", 200, "application/json", {"service": "other"}))[0]
    assert not classify(_probe("auth-debug", 200, "application/json", {"contract": "old"}))[0]
    assert not classify(_probe("health", 200, "application/json", "not json"))[0]


def test_anonymous_home_must_never_be_html_200_or_unprotected_json() -> None:
    assert classify(_probe("home-anonymous", 401, "text/plain", "Sign in to Dank Cinema again."))[0]
    assert not classify(_probe("home-anonymous", 401, "text/html", "<html>Unauthorized</html>"))[0]
    assert not classify(_probe("home-anonymous", 401, "text/plain", "Gateway unauthorized"))[0]
    for status, content_type, data in (
        (200, "text/html", "<html>rewritten</html>"),
        (200, "application/json", {"ok": True}),
        (302, "text/html", "Redirect"),
        (404, "text/plain", "Missing API"),
        (503, "text/html", "Gateway down"),
    ):
        accepted, _reason = classify(_probe("home-anonymous", status, content_type, data))
        assert not accepted


def test_probe_blocks_accidental_credentials_and_internal_targets() -> None:
    assert validated_origin("https://stoneyverify.discloud.dev/") == "https://stoneyverify.discloud.dev"
    assert validated_origin("https://cinema.example.com") == "https://cinema.example.com"
    for origin in (
        "http://cinema.example.com",
        "https://user:pass@cinema.example.com",
        "https://127.0.0.1",
        "https://192.168.1.12",
        "https://localhost",
        "https://cinema.example.com/health",
        "https://cinema.example.com?sig=not-a-real-secret",
        "https://cinema.example.com:8080",
    ):
        with pytest.raises(ValueError):
            validated_origin(origin)


def test_guild_snowflake_required_no_tokens_or_placeholders() -> None:
    assert parse_guild_id(GUILD_ID) == GUILD_ID
    for bad in ("", "guild", "123", "15;rm -rf /", "123456789012345678?uid=5"):
        with pytest.raises(ValueError):
            parse_guild_id(bad)


def test_actual_aiohttp_router_resolves_cinema_api_not_html_shell() -> None:
    async def check() -> None:
        app = web.Application()
        cinema_site.register_cinema_site_routes(app)
        request_home = make_mocked_request(
            "GET", f"/cinema/{GUILD_ID}/api/home", app=app,
        )
        match_home = await app.router.resolve(request_home)
        assert match_home.handler is cinema_site.cinema_home_api

        request_debug = make_mocked_request(
            "GET", f"/cinema/{GUILD_ID}/api/auth-debug", app=app,
        )
        match_debug = await app.router.resolve(request_debug)
        assert match_debug.handler is cinema_site.cinema_auth_debug_api

        request_shell = make_mocked_request(
            "GET", f"/cinema/{GUILD_ID}", app=app,
        )
        match_shell = await app.router.resolve(request_shell)
        assert match_shell.handler is cinema_site.cinema_site_page

    asyncio.run(check())
