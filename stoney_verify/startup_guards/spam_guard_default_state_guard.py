from __future__ import annotations

"""Once-per-startup SpamGuard default/persistence state reporting.

This guard never changes an existing owner choice. It asks the authoritative
SpamGuard loader for each connected guild, which may bootstrap a missing row
using the product default, then reports the result in plain state labels.
"""

from typing import Any, Mapping

from stoney_verify.globals import bot
from stoney_verify.spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED

_LOGGED_GUILDS: set[int] = set()


def classify_spam_guard_startup_state(settings: Mapping[str, Any], diag: Mapping[str, Any]) -> str:
    status = str(diag.get("status") or "").strip().lower()
    if status in {"unavailable", "exception", "missing_table"}:
        return "DATABASE LOAD ERROR"

    original_row_found = diag.get("original_row_found")
    enabled = bool(settings.get("enabled", SPAM_GUARD_DEFAULT_ENABLED))

    if original_row_found is False:
        return "DEFAULT ENABLED"
    if original_row_found is True:
        return "PERSISTED ENABLED" if enabled else "PERSISTED DISABLED"

    # A fresh process normally has a diagnostic event after loading. Keep this
    # conservative if another code path populated a cache before on_ready.
    if bool(diag.get("persisted")):
        return "PERSISTED ENABLED" if enabled else "PERSISTED DISABLED"
    return "DEFAULT ENABLED" if enabled else "DATABASE LOAD ERROR"


async def _report_spam_guard_states_once() -> None:
    try:
        from stoney_verify import spam_guard
    except Exception as exc:
        print(f"⚠️ SpamGuard startup state report unavailable: {type(exc).__name__}: {exc}")
        return

    for guild in list(getattr(bot, "guilds", []) or []):
        try:
            guild_id = int(getattr(guild, "id", 0) or 0)
        except Exception:
            guild_id = 0
        if guild_id <= 0 or guild_id in _LOGGED_GUILDS:
            continue

        try:
            settings = await spam_guard.get_spam_settings(guild_id)
            diag = dict(spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.get(guild_id) or {})
            label = classify_spam_guard_startup_state(settings, diag)
            enabled = bool(settings.get("enabled", SPAM_GUARD_DEFAULT_ENABLED))
            persisted = bool(diag.get("persisted"))
            reason = str(diag.get("reason") or "").strip() or "none"
            print(
                "🛡️ SpamGuard startup "
                f"guild={guild_id} state={label} enabled={enabled} persisted={persisted} reason={reason}"
            )
        except Exception as exc:
            print(
                "🛡️ SpamGuard startup "
                f"guild={guild_id} state=DATABASE LOAD ERROR enabled={SPAM_GUARD_DEFAULT_ENABLED} "
                f"persisted=False reason={type(exc).__name__}"
            )
        finally:
            _LOGGED_GUILDS.add(guild_id)


@bot.listen("on_ready")
async def spam_guard_default_state_on_ready() -> None:
    await _report_spam_guard_states_once()


__all__ = [
    "classify_spam_guard_startup_state",
]
