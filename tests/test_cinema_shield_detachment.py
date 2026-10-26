from __future__ import annotations

"""Extraction regression: Dank Shield must not start or package Cinema."""

import ast
from pathlib import Path

from stoney_verify.command_surface_contract import PUBLIC_GLOBAL_COMMAND_NAMES
from stoney_verify.navigation_registry import feature_by_key

ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    value = (ROOT / path).read_text(encoding="utf-8")
    if path.endswith(".py"):
        ast.parse(value, filename=path)
    return value


def test_shield_bootstrap_has_no_cinema_or_torrent_listener() -> None:
    app = source("stoney_verify/app.py")
    runtime = source("stoney_verify/command_runtime.py")
    api = source("stoney_verify/api_new/server.py")
    for code in (app, runtime):
        assert "start_torrent_media_server" not in code
        assert "from .torrent_media_server" not in code
        assert "start_share_router_public_server" in code
    assert "register_torrent_admin_routes" not in api
    assert "torrent_stream_routes" not in api


def test_shield_share_router_preserves_public_video_but_not_torrents() -> None:
    server = source("stoney_verify/share_router_public_server.py")
    video = source("stoney_verify/share_router_external_links.py")
    router = source("stoney_verify/share_router_runtime.py")
    assert "register_share_video_routes(app)" in server
    assert "add_get(\"/health\"" in server
    assert "register_cinema_site_routes" not in server
    assert "register_torrent_public_routes" not in server
    assert "register_movie_night_public_routes" not in server
    assert "torrent_streaming" not in server
    assert "share_router_public_server import media_public_base_url" in video
    assert "_route_torrent_media" not in router
    assert "get_torrent_manager" not in router
    assert "torrent_streaming" not in router


def test_cinema_discord_commands_and_menu_are_retired_from_shield() -> None:
    assert "cinema" not in PUBLIC_GLOBAL_COMMAND_NAMES
    assert "movie" not in PUBLIC_GLOBAL_COMMAND_NAMES
    assert feature_by_key("movie_night") is None
    surface = source("stoney_verify/commands_ext/public_command_surface_v2.py")
    assert "from .public_movie_night" not in surface
    assert "_remove_tree_command(tree, \"cinema\")" in surface
    assert "_remove_tree_command(tree, \"movie\")" in surface
    assert "build_cinema_command_group" not in surface
    assert "open_movie_night" not in surface
    ext = source("stoney_verify/commands_ext/__init__.py")
    assert '("public_movie_night",' not in ext


def test_cinema_is_not_shipped_in_discloud_package() -> None:
    ignored = set(source(".discloudignore").splitlines())
    assert {
        "stoney_verify/torrent_streaming.py",
        "stoney_verify/torrent_media_server.py",
        "stoney_verify/cinema_site.py",
        "stoney_verify/movie_night_web.py",
        "stoney_verify/commands_ext/public_movie_night.py",
        "stoney_verify/api_new/torrent_stream_routes.py",
        "stoney_verify/assets/cinema_site.js",
        "stoney_verify/assets/cinema_site.css",
    }.issubset(ignored)
    # Discloud's exact-name matching has varied by client; check basenames too.
    for required_name in (
        "torrent_streaming.py", "torrent_media_server.py", "cinema_site.py",
        "movie_night_web.py", "public_movie_night.py",
        "torrent_stream_routes.py", "cinema_site.js", "cinema_site.css",
    ):
        assert required_name in ignored
    assert "libtorrent==" not in source("requirements.txt")
    example = source(".env.example")
    assert "DANK_TORRENT_" not in example
    assert "DANK_CINEMA_" not in example


def test_lifecycle_hooks_do_not_call_cinema() -> None:
    assert "from .commands_ext.public_movie_night" not in source("stoney_verify/interaction_guard.py")
    assert "cinema_site" not in source("stoney_verify/events.py")
    assert "cinema_site" not in source("stoney_verify/commands_ext/public_onboarding.py")


def test_share_video_listener_uses_new_or_legacy_configuration_without_cinema(monkeypatch) -> None:
    from stoney_verify import share_router_public_server as site

    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://stoneyverify.discloud.dev/")
    monkeypatch.setenv("DANK_SHARE_VIDEO_PUBLIC_BASE_URL", "")
    assert site.media_public_base_url() == "https://stoneyverify.discloud.dev"
    monkeypatch.setenv("DANK_SHARE_VIDEO_PUBLIC_BASE_URL", "https://video.example.net/")
    assert site.media_public_base_url() == "https://video.example.net"
    monkeypatch.setenv("DANK_SHARE_VIDEO_SERVER_ENABLED", "false")
    assert not site.media_server_enabled()
    monkeypatch.setenv("DANK_SHARE_VIDEO_SERVER_ENABLED", "true")
    assert site.media_server_enabled()
    monkeypatch.setenv("DANK_SHARE_VIDEO_PUBLIC_BASE_URL", "http://public.example.net")
    import pytest
    with pytest.raises(RuntimeError, match="HTTPS"):
        site._validate_public_base_url()


def test_archived_cinema_runtime_and_legacy_tests_are_not_in_shield() -> None:
    # Cinema's unchanged source was SHA-verified against Dank-Cinema/main before
    # this removal; regression protects against reintroducing the old workload.
    legacy = (
        "stoney_verify/cinema_site.py",
        "stoney_verify/cinema_storage.py",
        "stoney_verify/movie_night_web.py",
        "stoney_verify/torrent_streaming.py",
        "stoney_verify/torrent_media_server.py",
        "stoney_verify/commands_ext/public_movie_night.py",
        "stoney_verify/api_new/torrent_stream_routes.py",
        "stoney_verify/assets/cinema_site.js",
        "stoney_verify/assets/cinema_site.css",
        "tests/test_movie_night_web.py",
        "tests/test_torrent_streaming.py",
        "tools/qualify_cinema_rolling_engine.py",
    )
    assert all(not (ROOT / filename).exists() for filename in legacy)
