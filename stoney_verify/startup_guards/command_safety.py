from __future__ import annotations

"""Retired command-tree monkey-patch compatibility surface.

Command ownership now lives in ``stoney_verify.command_runtime`` plus the
canonical bot/app owners. Importing this module must not mutate discord.py,
replace ``CommandTree.add_command``/``sync``, or activate other startup guards.

The small compatibility functions remain temporarily because diagnostics and
older focused tests may still import them while the ownership migration lands.
"""

from typing import Any

from ..command_runtime import command_budget_snapshot as _native_budget_snapshot

GLOBAL_COMMAND_LIMIT = 100
WARN_AT = 90


def skipped_command_registrations() -> list[dict[str, Any]]:
    """Legacy compatibility result.

    The old guard swallowed ``CommandLimitReached`` and tracked skipped
    registrations. Native ownership no longer silently skips commands, so this
    list is always empty.
    """

    return []


def command_budget_snapshot(tree: Any) -> dict[str, Any]:
    """Return the native command budget using the historical result shape."""

    snapshot = dict(_native_budget_snapshot(tree))
    snapshot.setdefault("global_limit", GLOBAL_COMMAND_LIMIT)
    snapshot.setdefault("global_remaining", max(0, GLOBAL_COMMAND_LIMIT - int(snapshot.get("global_count", 0) or 0)))
    snapshot["skipped_count"] = 0
    snapshot["skipped"] = []
    return snapshot


def install_command_limit_guard() -> bool:
    """Compatibility no-op.

    The old implementation replaced global discord.py methods. Keeping this
    function callable avoids a needless import break while making it impossible
    to reactivate the retired patch accidentally.
    """

    try:
        print(
            "🧭 command_safety compatibility shim loaded; "
            "CommandTree monkey patches are retired and native command ownership is active"
        )
    except Exception:
        pass
    return False


__all__ = [
    "GLOBAL_COMMAND_LIMIT",
    "WARN_AT",
    "command_budget_snapshot",
    "install_command_limit_guard",
    "skipped_command_registrations",
]
