from __future__ import annotations

"""Disable legacy top-level verify admin command side effects in public mode.

``stoney_verify.events`` imports ``verify_admin_commands`` for historical side
effects. That module registers old top-level maintenance commands:
- /repair_verify_ui
- /recompute_member_risk
- /recompute_all_member_risk

The slash cleanup guard removes those before global sync, so users do not see
them. This guard goes one step earlier for public production: it preloads a tiny
module stub so the side-effect module is never imported and the commands never
enter the local global command surface.

Dev/admin profiles can still opt in with DANK_EXPOSE_VERIFY_ADMIN_COMMANDS=true
or DANK_COMMAND_PROFILE=dev/full/public-admin.
"""

import os
import sys
from types import ModuleType
from typing import Any


def _env_true(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        text = str(raw).strip()
        return text if text else default
    except Exception:
        return default


def _deployment_mode() -> str:
    raw = _env_str("DANK_DEPLOYMENT_MODE", "").lower()
    if raw:
        return raw
    if _env_true("DANK_PRODUCTION_MODE", False):
        return "production"
    if _env_true("DANK_PUBLIC_MODE", False):
        return "public"
    return "development"


def _public_like() -> bool:
    profile = _env_str("DANK_COMMAND_PROFILE", "public").lower()
    deployment = _deployment_mode()
    return profile in {"public", "minimal"} or deployment in {"public", "prod", "production"}


def _admin_commands_allowed() -> bool:
    if _env_true("DANK_EXPOSE_VERIFY_ADMIN_COMMANDS", False):
        return True
    profile = _env_str("DANK_COMMAND_PROFILE", "public").lower()
    return profile in {"public-admin", "dev", "full"}


def _noop_register(*args: Any, **kwargs: Any) -> None:
    return None


def apply() -> bool:
    module_name = "stoney_verify.verify_admin_commands"

    if not _public_like() or _admin_commands_allowed():
        return False

    if module_name in sys.modules:
        return False

    stub = ModuleType(module_name)
    stub.__dict__["_register_verify_admin_commands"] = _noop_register
    stub.__dict__["__all__"] = []
    stub.__dict__["__doc__"] = "Public-mode stub; legacy top-level verify admin commands disabled."
    sys.modules[module_name] = stub

    try:
        import stoney_verify
        setattr(stoney_verify, "verify_admin_commands", stub)
    except Exception:
        pass

    try:
        print(
            "🧹 public_verify_admin_command_skip active; legacy top-level verify admin commands "
            "disabled in public profile"
        )
    except Exception:
        pass
    return True


apply()

__all__ = ["apply"]


def _dank_legacy_add_command_blocker() -> None:
    """Block legacy top-level slash commands from being added in public mode."""
    if not _dank_public_command_surface_locked():
        return

    try:
        from discord import app_commands
    except Exception:
        return

    original = getattr(app_commands.CommandTree, "add_command", None)
    if not callable(original) or getattr(original, "_dank_legacy_top_level_blocker", False):
        return

    def patched_add_command(self, command, *args, **kwargs):
        guild = kwargs.get("guild")
        name = getattr(command, "name", None)
        if guild is None and name in _LEGACY_PUBLIC_TOP_LEVEL_COMMANDS:
            print(f"🧭 Dank Shield blocked legacy top-level command in public mode: /{name}")
            return None
        return original(self, command, *args, **kwargs)

    setattr(patched_add_command, "_dank_legacy_top_level_blocker", True)
    app_commands.CommandTree.add_command = patched_add_command
    print("✅ Dank Shield legacy top-level command blocker active")


_dank_legacy_add_command_blocker()
