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


def main() -> None:
    _sleep_before_import_if_discord_login_backoff_active()
    from stoney_verify.app import run as _run_dank_shield

    _run_dank_shield()


if __name__ == "__main__":
    main()
