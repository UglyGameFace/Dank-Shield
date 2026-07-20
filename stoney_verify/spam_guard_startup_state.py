from __future__ import annotations

"""Native startup-state reporting for SpamGuard.

This module is loaded through SpamGuard's authoritative defaults import path, so
its on_ready listener is part of the real runtime without enabling the dormant
startup-guard loader. It is diagnostic only: it never changes an existing owner
choice. Missing settings rows are handled by SpamGuard's authoritative loader,
which may bootstrap the product default before this module reports the result.
"""

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .globals import bot
from .spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED

_LOGGED_GUILDS: set[int] = set()


def classify_spam_guard_startup_state(settings: Mapping[str, Any], diag: Mapping[str, Any]) -> str:
    """Return the plain startup label for one guild's effective SpamGuard state."""

    status = str(diag.get("status") or "").strip().lower()
    if status in {"unavailable", "exception", "missing_table"}:
        return "DATABASE LOAD ERROR"

    original_row_found = diag.get("original_row_found")
    enabled = bool(settings.get("enabled", SPAM_GUARD_DEFAULT_ENABLED))

    if original_row_found is False:
        return "DEFAULT ENABLED"
    if original_row_found is True:
        return "PERSISTED ENABLED" if enabled else "PERSISTED DISABLED"

    if bool(diag.get("persisted")):
        return "PERSISTED ENABLED" if enabled else "PERSISTED DISABLED"
    return "DEFAULT ENABLED" if enabled else "DATABASE LOAD ERROR"


def _augment_diag_from_runtime_cache(spam_guard: Any, guild_id: int, diag: dict[str, Any]) -> dict[str, Any]:
    """Fill provenance gaps when an already-warm cache bypassed a fresh DB read."""

    if diag:
        return diag

    try:
        cached = dict(getattr(spam_guard, "_RUNTIME_SETTINGS", {}).get(int(guild_id)) or {})
    except Exception:
        cached = {}
    if not cached:
        return diag

    persisted = bool(cached.get("__meta_persisted"))
    source = str(cached.get("__meta_source") or "").strip().lower()
    if persisted:
        diag["persisted"] = True
        if source == "db-bootstrap":
            diag["original_row_found"] = False
        elif source in {"db", "/dank setup"}:
            diag["original_row_found"] = True
    return diag


async def report_spam_guard_states_once(
    guilds: Iterable[Any] | None = None,
    *,
    emit: Callable[[str], None] | None = None,
) -> None:
    """Load and report each guild once for this process lifetime."""

    writer = emit or print
    try:
        from . import spam_guard
    except Exception as exc:
        writer(f"⚠️ SpamGuard startup state report unavailable: {type(exc).__name__}: {exc}")
        return

    source_guilds = list(guilds if guilds is not None else (getattr(bot, "guilds", []) or []))
    for guild in source_guilds:
        try:
            guild_id = int(getattr(guild, "id", 0) or 0)
        except Exception:
            guild_id = 0
        if guild_id <= 0 or guild_id in _LOGGED_GUILDS:
            continue

        try:
            settings = await spam_guard.get_spam_settings(guild_id)
            diag = dict(spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.get(guild_id) or {})
            diag = _augment_diag_from_runtime_cache(spam_guard, guild_id, diag)
            label = classify_spam_guard_startup_state(settings, diag)
            enabled = bool(settings.get("enabled", SPAM_GUARD_DEFAULT_ENABLED))
            persisted = bool(diag.get("persisted"))
            reason = str(diag.get("reason") or "").strip() or "none"
            writer(
                "🛡️ SpamGuard startup "
                f"guild={guild_id} state={label} enabled={enabled} persisted={persisted} reason={reason}"
            )
        except Exception as exc:
            writer(
                "🛡️ SpamGuard startup "
                f"guild={guild_id} state=DATABASE LOAD ERROR enabled={SPAM_GUARD_DEFAULT_ENABLED} "
                f"persisted=False reason={type(exc).__name__}"
            )
        finally:
            _LOGGED_GUILDS.add(guild_id)


@bot.listen("on_ready")
async def spam_guard_default_state_on_ready() -> None:
    await report_spam_guard_states_once()


__all__ = [
    "classify_spam_guard_startup_state",
    "report_spam_guard_states_once",
    "spam_guard_default_state_on_ready",
]
