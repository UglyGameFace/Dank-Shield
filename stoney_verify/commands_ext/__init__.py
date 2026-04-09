from __future__ import annotations

from typing import Any


_COMMANDS_EXT_REGISTERED = False


def register_all_commands(bot: Any, tree: Any) -> None:
    """
    Central loader for split command modules.

    This lets commands.py stay thin while all real command groups
    live in commands_ext/*.py.

    Safe to call multiple times; it only registers once per process.
    """
    global _COMMANDS_EXT_REGISTERED

    if _COMMANDS_EXT_REGISTERED:
        try:
            print("ℹ️ commands_ext.register_all_commands already ran; skipping duplicate registration.")
        except Exception:
            pass
        return

    errors: list[str] = []

    # ------------------------------------------------------------
    # Kick timers
    # ------------------------------------------------------------
    try:
        from .kick_timers import register_kick_timer_commands
        register_kick_timer_commands(bot, tree)
        try:
            print("✅ commands_ext: registered kick timer commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"kick_timers: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering kick timer commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # VC flow
    # ------------------------------------------------------------
    try:
        from .vc_flow import register_vc_flow_commands
        register_vc_flow_commands(bot, tree)
        try:
            print("✅ commands_ext: registered VC flow commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"vc_flow: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering VC flow commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket / verification admin
    # ------------------------------------------------------------
    try:
        from .ticket_admin import register_ticket_admin_commands
        register_ticket_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Moderation
    # ------------------------------------------------------------
    try:
        from .moderation import register_moderation_commands
        register_moderation_commands(bot, tree)
        try:
            print("✅ commands_ext: registered moderation commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"moderation: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering moderation commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Role / verification role admin
    # ------------------------------------------------------------
    try:
        from .role_admin import register_role_admin_commands
        register_role_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered role admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"role_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering role admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Channel cleanup admin
    # ------------------------------------------------------------
    try:
        from .channel_cleanup_admin import register_channel_cleanup_admin_commands
        register_channel_cleanup_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered channel cleanup admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"channel_cleanup_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering channel cleanup admin commands: {repr(e)}")
        except Exception:
            pass

    _COMMANDS_EXT_REGISTERED = True

    try:
        if errors:
            print("⚠️ commands_ext registration completed with errors:")
            for item in errors:
                print(f"   - {item}")
        else:
            print("✅ commands_ext registration complete.")
    except Exception:
        pass