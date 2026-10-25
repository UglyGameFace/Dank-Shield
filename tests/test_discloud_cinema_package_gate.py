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
