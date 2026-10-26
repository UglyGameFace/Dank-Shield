from __future__ import annotations

"""Static repo-side enforcement of Dank Shield's retired Cinema runtime.

Scans every Shield Python module (including new worker/helper modules), not
just today's entrypoints. This is still *not* an inspection of a running
Discloud process, existing files, downloaded media or database state.
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
# These roots were Cinema-only. Shared media metadata and Share Router
# HTTP/video code must stay available; likewise Community & Pings settings.
RETIRED_MODULE_ROOTS = frozenset((
    "cinema_site", "cinema_catalog", "cinema_storage", "cinema_playback_service",
    "cinema_feed_service", "cinema_feed_personalization", "cinema_discovery_service",
    "movie_night", "movie_catalog", "media_source_registry", "media_source_resolver",
    "torrent_media_server", "torrent_streaming", "torrent_stream_routes",
    "public_movie_night",
))


def _retired_module(module: str) -> bool:
    if any(module.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return True
    # Match module path components, not arbitrary imported attribute names.
    # Shared movie_night_role_id and CAP_MOVIE_NIGHT_NOTIFY remain valid.
    parts = str(module or "").strip(".").split(".")
    return any(
        part in RETIRED_MODULE_ROOTS
        or part.startswith(("cinema_", "torrent_"))
        or part == "movie_night"
        for part in parts
    )


def assert_no_retired_imports(source: str, *, path: str = "<source>") -> None:
    """Reject static and literal dynamic imports of retired Cinema modules."""
    tree = ast.parse(source, filename=path)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [module]
            # Alias names may be submodules only when imported directly from
            # a package. A service attribute named movie_night_role_id is not
            # a module and must remain importable.
            if module in {
                "", "stoney_verify", "stoney_verify.commands_ext",
                "stoney_verify.api_new", "commands_ext", "api_new",
            }:
                names.extend(
                    f"{module}.{item.name}" if module else item.name
                    for item in node.names
                )
        elif isinstance(node, ast.Call):
            func = node.func
            call_name = (
                func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else ""
            )
            if call_name in {"import_module", "__import__", "load_extension"}:
                if node.args and isinstance(node.args[0], ast.Constant):
                    if isinstance(node.args[0].value, str):
                        names = [node.args[0].value]
        for name in names:
            if _retired_module(name):
                raise AssertionError(
                    f"{path}: retired Cinema module reference {name}"
                )


def assert_separation() -> None:
    ignores = {
        line.strip()
        for line in (ROOT / ".discloudignore").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    for path in LEGACY:
        assert path in ignores and Path(path).name in ignores, (
            f"Missing Discloud exact-name Cinema exclusion: {path}"
        )
        assert not (ROOT / path).exists(), (
            f"Legacy Cinema file was reintroduced into Shield: {path}"
        )

    # Reject a new Cinema-named module anywhere in the production package,
    # including unrelated-looking startup helpers or newly added subpackages.
    prod_root = ROOT / "stoney_verify"
    modules = tuple(prod_root.rglob("*.py"))
    for file in modules:
        relative = file.relative_to(ROOT).as_posix()
        if _retired_module(file.stem):
            raise AssertionError(
                f"Cinema module was reintroduced into Shield: {relative}"
            )
        assert_no_retired_imports(file.read_text(encoding="utf-8"), path=relative)

    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert not any(
        line.strip().lower().startswith("libtorrent")
        for line in requirements.splitlines()
    ), "libtorrent may not be installed by Dank Shield"

    app = (ROOT / "stoney_verify/app.py").read_text(encoding="utf-8")
    runtime = (ROOT / "stoney_verify/command_runtime.py").read_text(
        encoding="utf-8"
    )
    assert "start_share_router_public_server()" in app
    assert "start_share_router_public_server()" in runtime
    assert "start_torrent_media_server(" not in app + runtime
    web = (ROOT / "stoney_verify/share_router_public_server.py").read_text(
        encoding="utf-8"
    )
    assert "register_share_video_routes(app)" in web
    assert "register_torrent_public_routes" not in web
    assert "register_cinema_site_routes" not in web


if __name__ == "__main__":
    assert_separation()
    print("Cinema separation audit passed across all Shield Python runtime modules.")
