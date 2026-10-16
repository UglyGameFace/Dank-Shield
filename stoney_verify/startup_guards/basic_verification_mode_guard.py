from __future__ import annotations

# The canonical DankCommandTree in command_runtime.py owns synchronization.
# This guard may still install legacy ID verification compatibility, but it must
# never replace discord.app_commands.CommandTree.sync process-wide.
_READY = False


def _log(message: str) -> None:
    try:
        print(f"basic_verification_mode_guard: {message}")
    except Exception:
        pass


def _install_panel_command() -> bool:
    try:
        from stoney_verify.commands_ext import public_verify_basic_panel
        install = getattr(public_verify_basic_panel, "apply", None)
        return bool(install()) if callable(install) else False
    except Exception:
        return False


def _install_legacy_allowlist() -> bool:
    try:
        from stoney_verify.startup_guards import id_verify_allowlist_guard
        install = getattr(id_verify_allowlist_guard, "apply", None)
        return bool(install()) if callable(install) else False
    except Exception:
        return False


def apply() -> bool:
    global _READY
    if _READY:
        _install_panel_command()
        return True
    allowlist_ready = _install_legacy_allowlist()
    panel_ready = _install_panel_command()
    _READY = bool(allowlist_ready or panel_ready)
    if _READY:
        _log(
            f"active allowlist={allowlist_ready} panel={panel_ready} "
            "basic_button_owner=native_runtime"
        )
    return _READY


apply()

__all__ = ["apply"]
