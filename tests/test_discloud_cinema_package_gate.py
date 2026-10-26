from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_discloud_package_cinema_separation_gate() -> None:
    spec = importlib.util.spec_from_file_location(
        "audit_cinema_discloud_package",
        ROOT / "tools" / "audit_cinema_discloud_package.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.assert_separation()


def test_existing_app_deployment_requires_package_audit_and_share_router() -> None:
    content = (ROOT / ".github/workflows/discloud-existing-app-manual-commit.yml").read_text(
        encoding="utf-8"
    )
    assert "test -s stoney_verify/share_router_public_server.py" in content
    assert "python tools/audit_cinema_discloud_package.py" in content
    assert "test -s stoney_verify/torrent_streaming.py" not in content


def _retirement_auditor():
    spec = importlib.util.spec_from_file_location(
        "cinema_runtime_retirement_audit",
        ROOT / "tools" / "audit_cinema_discloud_package.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cinema_modules_cannot_be_reintroduced_by_nested_or_dynamic_imports() -> None:
    import pytest

    auditor = _retirement_auditor()
    forbidden = (
        "import stoney_verify.cinema_site",
        "from stoney_verify import torrent_streaming",
        "from . import cinema_playback_service",
        "from stoney_verify.commands_ext import public_movie_night",
        "from .api_new import torrent_stream_routes",
        "importlib.import_module('stoney_verify.torrent_media_server')",
        "bot.load_extension('stoney_verify.commands_ext.public_movie_night')",
        "__import__('stoney_verify.movie_night')",
        "import movie_catalog",
        "from stoney_verify import media_metadata",
        "from .media_metadata import parse_release_name",
    )
    for snippet in forbidden:
        with pytest.raises(AssertionError, match="retired Cinema"):
            auditor.assert_no_retired_imports(snippet, path="test-injection.py")


def test_share_router_and_community_notification_imports_are_not_blocked() -> None:
    auditor = _retirement_auditor()
    safe = (
        "from stoney_verify.community_pings_service import CAP_MOVIE_NIGHT_NOTIFY",
        "from stoney_verify.share_router_public_server import media_public_base_url",
        "from .share_router_external_links import register_share_video_routes",
    )
    for snippet in safe:
        auditor.assert_no_retired_imports(snippet, path="test-shared-import.py")


def test_share_router_ci_and_setup_docs_do_not_depend_on_torrent_listener() -> None:
    workflow = (ROOT / ".github/workflows/share-router-production-reconcile.yml").read_text(
        encoding="utf-8"
    )
    assert '"stoney_verify/share_router_public_server.py"' in workflow
    assert '"stoney_verify/torrent_media_server.py"' not in workflow
    doc = (ROOT / "docs/share-router-private-downloads.md").read_text(
        encoding="utf-8"
    )
    assert "stoney_verify/share_router_public_server.py" in doc
    assert "DANK_SHARE_VIDEO_PUBLIC_BASE_URL" in doc
    assert "`stoney_verify/torrent_media_server.py`" not in doc
