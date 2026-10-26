from __future__ import annotations

"""Static Discloud packaging audit for Cinema extraction, with no network calls.

This validates repository-side exclusions and runtime registration only.
It cannot inspect Discloud's final ZIP or running production process.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = (
    "stoney_verify/cinema_site.py",
    "stoney_verify/movie_night_web.py",
    "stoney_verify/torrent_media_server.py",
    "stoney_verify/torrent_streaming.py",
    "stoney_verify/commands_ext/public_movie_night.py",
    "stoney_verify/api_new/torrent_stream_routes.py",
    "stoney_verify/assets/cinema_site.js",
    "stoney_verify/assets/cinema_site.css",
)
PRODUCTION_FILES = (
    "stoney_verify/app.py",
    "stoney_verify/command_runtime.py",
    "stoney_verify/share_router_runtime.py",
    "stoney_verify/share_router_external_links.py",
    "stoney_verify/share_router_public_server.py",
    "stoney_verify/api_new/server.py",
)
FORBIDDEN_PREFIXES = (
    "stoney_verify.cinema_",
    "stoney_verify.movie_night",
    "stoney_verify.movie_catalog",
    "stoney_verify.media_source_registry",
    "stoney_verify.media_source_resolver",
    "stoney_verify.torrent_",
    "stoney_verify.commands_ext.public_movie_night",
    "stoney_verify.api_new.torrent_stream_routes",
)


def assert_separation() -> None:
    ignores = {
        part.strip()
        for part in (ROOT / ".discloudignore").read_text(encoding="utf-8").splitlines()
        if part.strip() and not part.lstrip().startswith("#")
    }
    for path in LEGACY:
        assert path in ignores and Path(path).name in ignores, (
            f"Missing Discloud exact-name Cinema exclusion: {path}"
        )
        assert not (ROOT / path).exists(), (
            f"Legacy Cinema file was reintroduced into Shield: {path}"
        )
    requirement = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert not any(
        line.strip().lower().startswith("libtorrent")
        for line in requirement.splitlines()
    ), "libtorrent may not be installed by Dank Shield"

    for path in PRODUCTION_FILES:
        source = (ROOT / path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = (node.module or "")
                if node.level:
                    # Verify local-relative Cinema modules by path segment, even
                    # if they are imported dynamically in non-top-level blocks.
                    if any(
                        module == tail or module.startswith(tail + ".")
                        for tail in (
                            "cinema_site", "movie_night", "movie_catalog",
                            "torrent_media_server", "torrent_streaming",
                            "torrent_stream_routes", "public_movie_night",
                            "media_source_registry", "media_source_resolver",
                        )
                    ) or module.startswith("cinema_") or module.startswith("torrent_"):
                        raise AssertionError(f"{path}: legacy Cinema relative import {module}")
                names = [module]
            else:
                continue
            for name in names:
                if any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
                    raise AssertionError(f"{path}: legacy Cinema import {name}")
    app = (ROOT / "stoney_verify/app.py").read_text(encoding="utf-8")
    runtime = (ROOT / "stoney_verify/command_runtime.py").read_text(encoding="utf-8")
    assert "start_share_router_public_server()" in app
    assert "start_share_router_public_server()" in runtime
    assert "start_torrent_media_server(" not in app + runtime
    web = (ROOT / "stoney_verify/share_router_public_server.py").read_text(encoding="utf-8")
    assert "register_share_video_routes(app)" in web
    assert "register_torrent_public_routes" not in web
    assert "register_cinema_site_routes" not in web


if __name__ == "__main__":
    assert_separation()
    print("Cinema packaging audit passed: Shield is source-gated from Cinema runtime.")
