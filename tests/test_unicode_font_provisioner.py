from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools import provision_unicode_fonts as provisioner


ROOT = Path(__file__).resolve().parents[1]
DISCLOUD = (ROOT / "discloud.config").read_text(encoding="utf-8")


def _asset(payload: bytes, *, urls: tuple[str, ...] = ("https://example.invalid/font",)):
    return provisioner.FontAsset(
        filename="test-font.otf",
        urls=urls,
        size=len(payload),
        digest=hashlib.sha256(payload).hexdigest(),
    )


def test_valid_existing_font_skips_network(tmp_path, monkeypatch) -> None:
    payload = b"deterministic-font"
    asset = _asset(payload)
    monkeypatch.setattr(provisioner, "FONT_DIR", tmp_path)
    (tmp_path / asset.filename).write_bytes(payload)

    def fail_download(_url: str) -> bytes:
        raise AssertionError("network should not be used for a verified font")

    assert provisioner._install_asset(asset, downloader=fail_download) == "present"


def test_download_uses_verified_fallback_source_and_atomic_write(
    tmp_path,
    monkeypatch,
) -> None:
    payload = b"verified-font"
    asset = _asset(
        payload,
        urls=("https://bad.invalid/font", "https://good.invalid/font"),
    )
    monkeypatch.setattr(provisioner, "FONT_DIR", tmp_path)

    def download(url: str) -> bytes:
        return b"wrong" if "bad.invalid" in url else payload

    assert provisioner._install_asset(asset, downloader=download) == "installed"
    assert (tmp_path / asset.filename).read_bytes() == payload
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_download_fails_closed_without_installing(tmp_path, monkeypatch) -> None:
    asset = _asset(b"expected")
    monkeypatch.setattr(provisioner, "FONT_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="integrity mismatch"):
        provisioner._install_asset(asset, downloader=lambda _url: b"corrupt")

    assert not (tmp_path / asset.filename).exists()


def test_pinned_production_assets_cover_quality_and_last_resort_layers() -> None:
    by_name = {asset.filename: asset for asset in provisioner.ASSETS}

    assert "FreeSans.ttf" in by_name
    assert "unifont-17.0.03.otf" in by_name
    assert "unifont_upper-17.0.03.otf" in by_name

    assert by_name["FreeSans.ttf"].digest_kind == "git-sha1"
    assert by_name["FreeSans.ttf"].digest == "9db958532c12ef7f4aa22fab57a0f71e82acdd38"
    assert by_name["unifont-17.0.03.otf"].digest == (
        "26071c5a97533cefdcbc6b0645e7ee279413049079f09f592b26916ca6c21bf5"
    )
    assert by_name["unifont_upper-17.0.03.otf"].digest == (
        "fa308674dacccda2e7a3fbebf2597cad07fe5a722c89ccad416d06f1dbe054d1"
    )


def test_discloud_deployment_provisions_fonts_before_startup() -> None:
    assert "APT=canvas" in DISCLOUD
    assert "BUILD=python tools/provision_unicode_fonts.py" in DISCLOUD
