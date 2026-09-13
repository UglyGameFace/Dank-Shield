from __future__ import annotations

# Load Discord API throttling/retry safety before the app imports anything that
# can call audit logs, send modlogs, or edit ticket channels.
import stoney_verify.startup_guards.discord_api_safety  # noqa: F401

# Keep production/public slash commands on one surface. This runs before app.py
# so the app does not create beta guild command copies unless explicitly enabled.
import stoney_verify.startup_guards.command_safety  # noqa: F401
import stoney_verify.startup_guards.command_scope_dedupe  # noqa: F401

# Public production must never read deployment-level Discord role/channel/
# category/home-guild IDs. This runs before app.py imports globals consumers.
import stoney_verify.startup_guards.public_server_env_id_guard  # noqa: F401

# Core runtime safety only. Product command registration belongs to
# stoney_verify.commands and commands_ext, never startup_guards.
from stoney_verify.startup_guards import (  # noqa: F401
    discord_api_safety,
    command_safety,
    command_scope_dedupe,
    public_server_env_id_guard,
    guild_config_runtime_validator,
    interaction_action_lock_guard,
)


# =====================================================
# DISCORD BOT ENTRYPOINT
# Discloud starts main.py, so main.py must hand off to
# stoney_verify.app where bot.run(DISCORD_TOKEN) lives.
# =====================================================


def _sleep_before_import_if_discord_login_backoff_active() -> None:
    """Sleep before importing the bot app if Discord login is cooling down.

    This prevents restart loops from repeatedly loading all command modules
    before the bot is even allowed to try Discord login again.
    """
    import os
    import time

    path = os.getenv("DANK_LOGIN_BACKOFF_STATE_FILE", ".dank_login_backoff_until")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            until = float((fh.read() or "0").strip())
    except Exception:
        return

    remaining = int(until - time.time())
    if remaining > 0:
        print(
            "🧯 Dank Shield early login backoff active; "
            f"sleeping {remaining}s before bot import"
        )
        time.sleep(remaining)


def _install_invite_reconciliation_runtime() -> None:
    """Attach missed-invite recovery to the real bot before Discord login."""

    try:
        from stoney_verify.globals import bot
        from stoney_verify.invite_reconciliation_runtime import (
            install_invite_reconciliation,
        )

        if not install_invite_reconciliation(bot):
            print(
                "⚠️ invite_reconcile could not install; "
                "live invite enforcement remains active but history recovery is unavailable"
            )
    except Exception as exc:
        print(
            "⚠️ invite_reconcile install failed before app import: "
            f"{type(exc).__name__}: {exc}"
        )


def _install_anti_nuke_gateway_runtime() -> None:
    """Attach the AntiNuke audit gateway fast path before Discord login."""

    try:
        from stoney_verify.globals import bot
        from stoney_verify.anti_nuke_gateway_runtime import (
            install_anti_nuke_gateway_runtime,
        )

        if not install_anti_nuke_gateway_runtime(bot):
            print("ℹ️ AntiNuke gateway runtime was already installed; duplicate skipped")
    except Exception as exc:
        print(
            "⚠️ AntiNuke gateway runtime install failed; "
            "canonical Discord-event/REST protection remains active: "
            f"{type(exc).__name__}: {exc}"
        )


def _install_anti_nuke_finalizer_runtime() -> None:
    """Finalize AntiNuke readiness truth and gateway listener ownership."""

    try:
        from stoney_verify.globals import bot
        from stoney_verify.anti_nuke_finalizer_runtime import (
            install_anti_nuke_finalizer_runtime,
        )

        if not install_anti_nuke_finalizer_runtime(bot):
            print("ℹ️ AntiNuke finalizer runtime was already installed; duplicate skipped")
    except Exception as exc:
        print(
            "⚠️ AntiNuke finalizer runtime install failed; "
            "existing AntiNuke protection remains active: "
            f"{type(exc).__name__}: {exc}"
        )


def _install_anti_nuke_incident_runtime() -> None:
    """Attach AntiNuke live-incident recovery after readiness finalization."""

    try:
        from stoney_verify.globals import bot
        from stoney_verify.anti_nuke_incident_runtime import (
            install_anti_nuke_incident_runtime,
        )

        if not install_anti_nuke_incident_runtime(bot):
            print("⚠️ AntiNuke incident runtime could not replace the audit listener")
    except Exception as exc:
        print(
            "🚨 AntiNuke incident runtime install failed: "
            f"{type(exc).__name__}: {exc}"
        )


def _install_hostile_actor_runtime() -> None:
    """Attach durable hostile-identity enforcement after AntiNuke policy is final."""

    try:
        from stoney_verify.globals import bot
        from stoney_verify.anti_nuke_hostile_actor_runtime import (
            install_hostile_actor_runtime,
        )

        if not install_hostile_actor_runtime(bot):
            print("ℹ️ Hostile actor reputation runtime was already installed; duplicate skipped")
    except Exception as exc:
        print(
            "🚨 Hostile actor reputation runtime install failed: "
            f"{type(exc).__name__}: {exc}"
        )


def main() -> None:
    _sleep_before_import_if_discord_login_backoff_active()
    _install_invite_reconciliation_runtime()
    _install_anti_nuke_gateway_runtime()
    _install_anti_nuke_finalizer_runtime()
    _install_anti_nuke_incident_runtime()
    _install_hostile_actor_runtime()
    from stoney_verify.app import run as _run_dank_shield

    _run_dank_shield()


if __name__ == "__main__":
    main()
