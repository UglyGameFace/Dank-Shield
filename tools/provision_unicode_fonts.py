from __future__ import annotations

"""Provision deterministic long-tail Unicode fallback fonts for lifecycle cards.

Discloud's Canvas image supplies common Latin fonts but does not guarantee the
long-tail Unicode faces needed by arbitrary Discord display names.  Deployment
therefore installs a small quality fallback plus GNU Unifont Plane 0/upper
fallbacks into the app directory before startup.

The downloads are pinned and integrity checked.  A failed or changed download
fails the build instead of silently deploying a renderer that produces tofu
boxes.
"""

from dataclasses import dataclass
import argparse
import hashlib
from pathlib import Path
import sys
import tempfile
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / ".runtime_fonts"


@dataclass(frozen=True)
class FontAsset:
    filename: str
    urls: tuple[str, ...]
    size: int
    digest: str
    digest_kind: str = "sha256"


ASSETS: tuple[FontAsset, ...] = (
    FontAsset(
        filename="FreeSans.ttf",
        urls=(
            "https://raw.githubusercontent.com/opensourcedesign/fonts/"
            "96da5c8b6cdc1b91d2ee58efef3f0402f5a47217/"
            "gnu-freefont_freesans/FreeSans.ttf",
        ),
        size=714456,
        # GitHub blob object for the exact pinned file.
        digest="9db958532c12ef7f4aa22fab57a0f71e82acdd38",
        digest_kind="git-sha1",
    ),
    FontAsset(
        filename="unifont-17.0.03.otf",
        urls=(
            "https://ftpmirror.gnu.org/unifont/unifont-17.0.03/"
            "unifont-17.0.03.otf",
            "https://unifoundry.com/pub/unifont/unifont-17.0.03/font-builds/"
            "unifont-17.0.03.otf",
        ),
        size=5321400,
        digest="26071c5a97533cefdcbc6b0645e7ee279413049079f09f592b26916ca6c21bf5",
    ),
    FontAsset(
        filename="unifont_upper-17.0.03.otf",
        urls=(
            "https://ftpmirror.gnu.org/unifont/unifont-17.0.03/"
            "unifont_upper-17.0.03.otf",
            "https://unifoundry.com/pub/unifont/unifont-17.0.03/font-builds/"
            "unifont_upper-17.0.03.otf",
        ),
        size=5971416,
        digest="fa308674dacccda2e7a3fbebf2597cad07fe5a722c89ccad416d06f1dbe054d1",
    ),
)


def _git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _digest(data: bytes, kind: str) -> str:
    if kind == "sha256":
        return hashlib.sha256(data).hexdigest()
    if kind == "git-sha1":
        return _git_blob_sha1(data)
    raise ValueError(f"Unsupported digest kind: {kind}")


def _valid(data: bytes, asset: FontAsset) -> bool:
    return (
        len(data) == int(asset.size)
        and _digest(data, asset.digest_kind).lower() == asset.digest.lower()
    )


def _download(url: str, *, timeout: float = 75.0) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "Dank-Shield-Unicode-Font-Provisioner/1.0",
            "Accept": "application/octet-stream,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def _install_asset(
    asset: FontAsset,
    *,
    downloader: Callable[[str], bytes] = _download,
) -> str:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    destination = FONT_DIR / asset.filename

    if destination.is_file():
        existing = destination.read_bytes()
        if _valid(existing, asset):
            return "present"

    failures: list[str] = []
    payload: bytes | None = None
    for url in asset.urls:
        try:
            candidate = downloader(url)
        except Exception as exc:
            failures.append(f"{url}: {type(exc).__name__}: {exc}")
            continue
        if not _valid(candidate, asset):
            failures.append(
                f"{url}: integrity mismatch "
                f"(size={len(candidate)}, {asset.digest_kind}={_digest(candidate, asset.digest_kind)})"
            )
            continue
        payload = candidate
        break

    if payload is None:
        details = " | ".join(failures) or "no download source succeeded"
        raise RuntimeError(f"Could not provision {asset.filename}: {details}")

    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=FONT_DIR,
        prefix=f".{asset.filename}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)

    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    if not _valid(destination.read_bytes(), asset):
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Post-write verification failed for {asset.filename}")
    return "installed"


def provision_unicode_fonts() -> dict[str, str]:
    statuses: dict[str, str] = {}
    for asset in ASSETS:
        statuses[asset.filename] = _install_asset(asset)
    return statuses


def verify_unicode_fonts() -> dict[str, bool]:
    results: dict[str, bool] = {}
    for asset in ASSETS:
        path = FONT_DIR / asset.filename
        results[asset.filename] = path.is_file() and _valid(path.read_bytes(), asset)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify already-provisioned fonts without downloading.",
    )
    args = parser.parse_args(argv)

    try:
        if args.verify_only:
            verified = verify_unicode_fonts()
            for name, ok in verified.items():
                print(f"unicode-font {name}: {'OK' if ok else 'MISSING/INVALID'}")
            return 0 if all(verified.values()) else 1

        statuses = provision_unicode_fonts()
        for name, status in statuses.items():
            print(f"unicode-font {name}: {status}")
        return 0
    except (RuntimeError, OSError, URLError, ValueError) as exc:
        print(f"unicode-font provisioning failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
