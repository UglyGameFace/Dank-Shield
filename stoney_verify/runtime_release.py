from __future__ import annotations

"""Small runtime build proof for production support.

Discloud deployments do not guarantee that a Git commit SHA is injected into the
process environment. Prefer a host-provided SHA when available, but always expose
a deterministic source fingerprint over the interaction-critical runtime files so
support can distinguish "GitHub merged it" from "this process is actually running
it" without shell access to the host.
"""

import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROOF_VERSION = "runtime-proof-v1"
_SHA_ENV_KEYS: tuple[str, ...] = (
    "DANK_RELEASE_SHA",
    "GITHUB_SHA",
    "SOURCE_VERSION",
    "COMMIT_SHA",
    "RENDER_GIT_COMMIT",
    "RAILWAY_GIT_COMMIT_SHA",
)
_FINGERPRINT_PATHS: tuple[str, ...] = (
    "main.py",
    "stoney_verify/app.py",
    "stoney_verify/interaction_guard.py",
    "stoney_verify/commands_ext/public_owner_authority.py",
    "stoney_verify/commands_ext/public_self_roles_group.py",
)


def _clean(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def runtime_declared_sha() -> str:
    for key in _SHA_ENV_KEYS:
        value = _clean(os.getenv(key))
        if value:
            return value
    return ""


@lru_cache(maxsize=1)
def runtime_source_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    readable = 0
    for relative in _FINGERPRINT_PATHS:
        path = root / relative
        try:
            payload = path.read_bytes()
        except Exception:
            continue
        readable += 1
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    if readable <= 0:
        return "unavailable"
    return digest.hexdigest()[:16]


@lru_cache(maxsize=1)
def runtime_release_snapshot() -> dict[str, Any]:
    declared = runtime_declared_sha()
    return {
        "proof_version": _PROOF_VERSION,
        "declared_sha": declared,
        "declared_sha_short": declared[:12] if declared else "",
        "source_fingerprint": runtime_source_fingerprint(),
        "fingerprint_file_count": len(_FINGERPRINT_PATHS),
    }


@lru_cache(maxsize=1)
def runtime_release_label() -> str:
    snapshot = runtime_release_snapshot()
    sha = _clean(snapshot.get("declared_sha_short"))
    source = _clean(snapshot.get("source_fingerprint"),)
    if sha:
        return f"sha:{sha} src:{source}"
    return f"src:{source}"


__all__ = [
    "runtime_declared_sha",
    "runtime_release_label",
    "runtime_release_snapshot",
    "runtime_source_fingerprint",
]
