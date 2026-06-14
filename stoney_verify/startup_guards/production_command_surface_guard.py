from __future__ import annotations

"""Final public command surface pruning.

This guard runs after feature guards have attached their commands. It keeps the
normal public /dank surface product-like without hiding real owner/staff
workflows. Debug, legacy, duplicate, raw-ID, and migration commands stay hidden.
"""

import os
from typing import Any

_PATCHED = False

# Real production product areas. These should stay visible because they map to
# clear owner/staff jobs, not debug internals.
_DEFAULT_KEEP = {
    "setup",
    "overview",
    "protection",
    "welcome",
    "roles",
    "modlog",
    "embed",
    "cleanup",
    "members",
    "help",
    "commands",
}

# Legacy/replaced/noisy /dank children. Automod + Spam Guard are now inside
# /dank protection; the other names here are setup/debug/repair aliases that
# should not crowd the normal production surface.
_DEFAULT_HIDE = {
    "automod",
    "spam",
    "config-cache",
    "current",
    "archive-backfill",
    "cache",
    "config",
    "db-check",
    "health",
    "launch-check",
    "modlog-check",
    "permission-check",
    "production-audit",
    "refresh-config",
    "setup-access",
    "setup-assistant",
    "setup-defaults",
    "setup-find",
    "setup-logs",
    "setup-picker",
    "setup-review",
    "setup-status",
    "setup-tickets",
    "setup-verify",
    "setup-verify-ids",
    "tickettool-check",
}


def _csv_set(value: str) -> set[str]:
    return {part.strip().lower() for part in str(value or "").split(",") if part.strip()}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "")
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _enabled() -> bool:
    raw = str(os.getenv("STONEY_PUBLIC_COMMAND_SURFACE", "clean") or "clean").strip().lower()
    return raw in {"clean", "production", "prod", "minimal"} and not _env_bool("STONEY_DISABLE_PRODUCTION_COMMAND_SURFACE_GUARD", False)


def _configured_keep() -> set[str]:
    keep = set(_DEFAULT_KEEP)
    keep |= _csv_set(os.getenv("STONEY_PUBLIC_EXTRA_DANK_CHILDREN", "") or "")
    keep -= _csv_set(os.getenv("STONEY_PUBLIC_HIDE_DANK_CHILDREN", "") or "")
    return {item for item in keep if item}


def _child_names(group: Any) -> list[str]:
    try:
        return sorted(str(getattr(cmd, "name", "")) for cmd in list(getattr(group, "commands", []) or []) if str(getattr(cmd, "name", "")).strip())
    except Exception:
        return []


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        if not _enabled():
            _PATCHED = True
            print("✅ production_command_surface_guard skipped; expanded /dank surface enabled")
            return True

        keep = _configured_keep()
        hide = (set(_DEFAULT_HIDE) | _csv_set(os.getenv("STONEY_PUBLIC_FORCE_HIDE_DANK_CHILDREN", "") or "")) - keep

        import stoney_verify.commands_ext as commands_ext

        commands_ext._ALLOWED_STONEY_CHILDREN = set(keep)
        confusing = set(getattr(commands_ext, "_CONFUSING_STONEY_CHILDREN", tuple()) or tuple())
        confusing.update(hide)
        commands_ext._CONFUSING_STONEY_CHILDREN = tuple(sorted(confusing))

        from stoney_verify.commands_ext.public_setup_group import stoney_group

        before = _child_names(stoney_group)
        removed: list[str] = []
        for child in sorted(hide):
            try:
                if stoney_group.get_command(child) is not None:
                    stoney_group.remove_command(child)
                    removed.append(child)
            except Exception:
                pass
        after = _child_names(stoney_group)
        unexpected = [child for child in after if child not in keep]
        _PATCHED = True
        print(
            "✅ production_command_surface_guard active; "
            f"keep={sorted(keep)} removed={removed} unexpected_remaining={unexpected} before={before} after={after}"
        )
        return True
    except Exception as exc:
        try:
            print(f"⚠️ production_command_surface_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
