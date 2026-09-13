from __future__ import annotations


def _install_anti_nuke_guardian_once() -> None:
    """Install the broad AntiNuke audit guardian before the legacy gateway hook.

    The legacy installer remains import-compatible, but its own duplicate flag is
    set after the guardian is attached so production has exactly one audit gateway
    listener and one canonical AntiNuke policy engine.
    """

    try:
        from .globals import bot
        from .anti_nuke_guardian_runtime import install_anti_nuke_guardian_runtime

        if install_anti_nuke_guardian_runtime(bot):
            setattr(bot, "_dank_antinuke_gateway_runtime_installed", True)
    except Exception as exc:
        try:
            print(
                "⚠️ AntiNuke guardian package install failed; "
                f"native protection remains available: {type(exc).__name__}: {exc}"
            )
        except Exception:
            pass


_install_anti_nuke_guardian_once()

__all__ = []
