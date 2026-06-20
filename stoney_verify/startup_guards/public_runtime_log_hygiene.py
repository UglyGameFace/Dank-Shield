from __future__ import annotations

"""Production runtime log hygiene for public installs.

This does not hide real warnings/errors. It only normalizes noisy known-good
startup output so public production logs stay useful:

- Startup ticket sync summaries keep counts but do not dump every scanned row.
- Spam Guard row-missing defaults are logged as expected first-run state, not as
  scary "startup settings issue" text.
"""

import builtins
import os
from typing import Any

_PATCHED = False
_ORIGINAL_PRINT = None


def _env_str(name: str, default: str = "") -> str:
    try:
        raw = os.getenv(name)
        if raw is None:
            return default
        text = str(raw).strip()
        return text if text else default
    except Exception:
        return default


def _compact_logs_enabled() -> bool:
    style = _env_str("DANK_STARTUP_LOG_STYLE", "compact").lower()
    return style not in {"verbose", "debug", "trace", "full"}


def _log(message: str) -> None:
    try:
        printer = _ORIGINAL_PRINT or builtins.print
        printer(f"🧼 public_runtime_log_hygiene {message}")
    except Exception:
        pass


def _compact_ticket_sync_summary(summary: dict[str, Any]) -> dict[str, Any]:
    out = dict(summary)
    rows = out.pop("rows", None)
    if isinstance(rows, list):
        out["rows_count"] = len(rows)
        actions: dict[str, int] = {}
        reasons: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            action = str(row.get("action") or "unknown")
            reason = str(row.get("reason") or "")
            actions[action] = actions.get(action, 0) + 1
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
        if actions:
            out["row_actions"] = actions
        if reasons:
            out["skip_reasons"] = reasons
    return out


def _rewrite_text_line(text: str) -> str | None:
    line = str(text or "")

    if (
        line.startswith("🛡️ spam_guard startup settings issue ")
        and "row_found=False" in line
        and "reason=row_missing" in line
    ):
        return line.replace(
            "🛡️ spam_guard startup settings issue ",
            "ℹ️ spam_guard settings defaulted ",
            1,
        )

    return None


def _filtered_print(*args: Any, **kwargs: Any) -> Any:
    printer = _ORIGINAL_PRINT or builtins.print

    try:
        if not _compact_logs_enabled():
            return printer(*args, **kwargs)

        if args:
            first = str(args[0] or "")
            if first == "✅ Startup ticket sync complete:" and len(args) >= 2 and isinstance(args[1], dict):
                new_args = (args[0], _compact_ticket_sync_summary(args[1]), *args[2:])
                return printer(*new_args, **kwargs)

            if len(args) == 1:
                rewritten = _rewrite_text_line(first)
                if rewritten is not None:
                    return printer(rewritten, **kwargs)
    except Exception:
        pass

    return printer(*args, **kwargs)


def apply() -> bool:
    global _PATCHED, _ORIGINAL_PRINT
    if _PATCHED:
        return True

    try:
        if getattr(builtins.print, "_public_runtime_log_hygiene_wrapped", False):
            _PATCHED = True
            return True
        _ORIGINAL_PRINT = builtins.print
        setattr(_filtered_print, "_public_runtime_log_hygiene_wrapped", True)
        builtins.print = _filtered_print  # type: ignore[assignment]
        _PATCHED = True
        _log("active; compacting known noisy public startup summaries")
        return True
    except Exception:
        return False


apply()

__all__ = ["apply"]
