from __future__ import annotations

"""Keep bot_command_worker role decisions per-guild in public mode.

The dashboard command worker had legacy helpers that read global role IDs from
stoney_verify.globals. The public env guard zeros those IDs, but this guard makes
the intent explicit and lets worker code resolve roles from the current guild's
validated runtime config when the active command has a guild context.
"""

from typing import Any

_PATCHED = False
_ACTIVE_GUILD_ID: int = 0


def _log(message: str) -> None:
    try:
        print(f"🧭 bot_command_worker_public_config_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ bot_command_worker_public_config_guard {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_id_for_active_guild(*keys: str) -> int:
    gid = int(_ACTIVE_GUILD_ID or 0)
    if gid <= 0:
        return 0
    try:
        from stoney_verify import guild_config as gc

        cache = getattr(gc, "_CONFIG_CACHE", None)
        row = cache.get(str(gid)) if isinstance(cache, dict) else None
        if not isinstance(row, dict):
            row = gc._db_get_guild_config_sync(gid)  # type: ignore[attr-defined]
        if not isinstance(row, dict):
            return 0
        for key in keys:
            value = _safe_int(row.get(key), 0)
            if value > 0:
                return value
    except Exception:
        return 0
    return 0


def _wrap_execute_command(worker: Any) -> bool:
    original = getattr(worker, "execute_command", None)
    if not callable(original) or getattr(original, "_public_config_guard_wrapped", False):
        return False

    async def wrapped_execute_command(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        global _ACTIVE_GUILD_ID
        previous = _ACTIVE_GUILD_ID
        try:
            if isinstance(cmd, dict):
                _ACTIVE_GUILD_ID = _safe_int(cmd.get("guild_id"), 0)
            return await original(cmd, *args, **kwargs)
        finally:
            _ACTIVE_GUILD_ID = previous

    setattr(wrapped_execute_command, "_public_config_guard_wrapped", True)
    setattr(worker, "execute_command", wrapped_execute_command)
    return True


def _replace_role_helpers(worker: Any) -> int:
    replacements = {
        "_get_verified_role_id": ("verified_role_id",),
        "_get_unverified_role_id": ("unverified_role_id",),
        "_get_resident_role_id": ("resident_role_id",),
        "_get_stoner_role_id": ("stoner_role_id",),
        "_get_drunken_role_id": ("drunken_role_id",),
    }
    count = 0
    for name, keys in replacements.items():
        original = getattr(worker, name, None)
        if not callable(original) or getattr(original, "_public_config_guard_wrapped", False):
            continue

        def make_helper(original_fn: Any, keys: tuple[str, ...]):
            def helper() -> int:
                value = _cfg_id_for_active_guild(*keys)
                if value > 0:
                    return value
                return 0
            setattr(helper, "_public_config_guard_wrapped", True)
            return helper

        setattr(worker, name, make_helper(original, keys))
        count += 1
    return count


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.workers import bot_command_worker as worker

        wrapped_execute = _wrap_execute_command(worker)
        helper_count = _replace_role_helpers(worker)
        _PATCHED = True
        _log(f"active execute_wrapped={wrapped_execute} role_helpers={helper_count}")
        return True
    except Exception as e:
        _warn(f"failed to patch bot command worker config helpers: {e!r}")
        return False


apply()

__all__ = ["apply"]
