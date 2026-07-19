from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

REPO = "UglyGameFace/Dank-Shield"
BRANCH = "fix/finish-dank-shield-stability-mission"


def api(method: str, url: str, *, payload: dict | None = None) -> dict:
    token = os.environ["GH_TOKEN"]
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def update_file(path: str, cleaned: str, original: str, message: str) -> None:
    if cleaned == original:
        print(path, "already clean")
        return
    compile(cleaned, path, "exec")
    query = urllib.parse.urlencode({"ref": BRANCH})
    endpoint = f"https://api.github.com/repos/{REPO}/contents/{path}"
    current = api("GET", f"{endpoint}?{query}")
    result = api(
        "PUT",
        endpoint,
        payload={
            "message": message,
            "content": base64.b64encode(cleaned.encode("utf-8")).decode("ascii"),
            "sha": current["sha"],
            "branch": BRANCH,
        },
    )
    print(path, result.get("commit", {}).get("sha"))


def clean_spam_guard() -> None:
    path = "stoney_verify/spam_guard.py"
    original = Path(path).read_text(encoding="utf-8")
    text = original
    import_line = "from .spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED\n"
    anchor = "from .globals import *  # noqa: F401,F403\n"
    if import_line not in text:
        if anchor not in text:
            raise RuntimeError("spam_guard import anchor missing")
        text = text.replace(anchor, anchor + import_line, 1)
    old = 'def _default_settings(guild_id: int) -> Dict[str, Any]:\n    return {\n        "guild_id": str(guild_id),\n        "enabled": True,'
    new = 'def _default_settings(guild_id: int) -> Dict[str, Any]:\n    return {\n        "guild_id": str(guild_id),\n        "enabled": SPAM_GUARD_DEFAULT_ENABLED,'
    if old in text:
        text = text.replace(old, new, 1)
    if '"enabled": SPAM_GUARD_DEFAULT_ENABLED' not in text:
        raise RuntimeError("spam_guard authoritative enabled default was not applied")
    update_file(path, text, original, "refactor: use authoritative SpamGuard enabled default")


def clean_setup_service_modes() -> None:
    path = "stoney_verify/startup_guards/setup_service_modes.py"
    original = Path(path).read_text(encoding="utf-8")
    text = original
    import_line = "from stoney_verify.spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED\n"
    anchor = "import discord\n"
    if import_line not in text:
        if anchor not in text:
            raise RuntimeError("setup_service_modes import anchor missing")
        text = text.replace(anchor, anchor + "\n" + import_line, 1)
    replacements = {
        'return ServiceState(True, False, False, True, True, "defaults")':
            'return ServiceState(True, False, False, SPAM_GUARD_DEFAULT_ENABLED, SPAM_GUARD_DEFAULT_ENABLED, "defaults")',
        'spamguard = _safe_bool(_cfg_value(cfg, "spam_guard_enabled", True), True)':
            'spamguard = _safe_bool(_cfg_value(cfg, "spam_guard_enabled", SPAM_GUARD_DEFAULT_ENABLED), SPAM_GUARD_DEFAULT_ENABLED)',
        '"enabled": True,\n        "mode": "timeout",':
            '"enabled": SPAM_GUARD_DEFAULT_ENABLED,\n        "mode": "timeout",',
        'data["enabled"] = _safe_bool(data.get("enabled", data.get("spam_blocker_enabled")), False)':
            'data["enabled"] = _safe_bool(data.get("enabled", data.get("spam_blocker_enabled")), SPAM_GUARD_DEFAULT_ENABLED)',
    }
    for old, new in replacements.items():
        if old in text:
            text = text.replace(old, new, 1)
    required = (
        "from stoney_verify.spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED",
        "ServiceState(True, False, False, SPAM_GUARD_DEFAULT_ENABLED, SPAM_GUARD_DEFAULT_ENABLED, \"defaults\")",
        '_cfg_value(cfg, "spam_guard_enabled", SPAM_GUARD_DEFAULT_ENABLED)',
        '"enabled": SPAM_GUARD_DEFAULT_ENABLED',
    )
    for marker in required:
        if marker not in text:
            raise RuntimeError(f"setup_service_modes missing authoritative marker: {marker}")
    update_file(path, text, original, "refactor: centralize SpamGuard setup default truth")


def clean_slash_command_cleanup() -> None:
    path = "stoney_verify/startup_guards/slash_command_cleanup.py"
    original = Path(path).read_text(encoding="utf-8")
    text = original
    import_line = "from stoney_verify.command_surface_contract import PUBLIC_DANK_CHILDREN\n"
    anchor = "from discord import app_commands\n"
    if import_line not in text:
        if anchor not in text:
            raise RuntimeError("slash cleanup import anchor missing")
        text = text.replace(anchor, anchor + "\n" + import_line, 1)
    text = text.replace(
        'COMMAND_CLEANUP_EPOCH = "2026-06-14-verify-panel-command-v2"',
        'COMMAND_CLEANUP_EPOCH = "2026-07-19-public-command-contract-v1"',
        1,
    )
    old_allowed = '''ALLOWED_DANK_CHILDREN = {
    "setup",
    "help",
    "commands",
    "spam",
    "cleanup",
    "members",
}

CONFUSING_DANK_CHILDREN = CONFUSING_DANK_CHILDREN
ALLOWED_DANK_CHILDREN = ALLOWED_DANK_CHILDREN
'''
    new_allowed = '''ALLOWED_DANK_CHILDREN = set(PUBLIC_DANK_CHILDREN)
'''
    if old_allowed in text:
        text = text.replace(old_allowed, new_allowed, 1)
    if "ALLOWED_DANK_CHILDREN = set(PUBLIC_DANK_CHILDREN)" not in text:
        raise RuntimeError("slash cleanup canonical /dank allowlist was not applied")
    update_file(path, text, original, "refactor: use canonical public command cleanup contract")


def main() -> None:
    clean_spam_guard()
    clean_setup_service_modes()
    clean_slash_command_cleanup()


if __name__ == "__main__":
    main()
