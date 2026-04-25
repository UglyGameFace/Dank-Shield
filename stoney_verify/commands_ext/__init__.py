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
    # Ticket channel admin
    # ------------------------------------------------------------
    try:
        from .ticket_channel_admin import register_ticket_channel_admin_commands
        register_ticket_channel_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket channel admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_channel_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket channel admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket intake / routing admin
    # ------------------------------------------------------------
    try:
        from .ticket_intake_admin import register_ticket_intake_admin_commands
        register_ticket_intake_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket intake admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_intake_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket intake admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket queue / history admin
    # ------------------------------------------------------------
    try:
        from .ticket_queue_admin import register_ticket_queue_admin_commands
        register_ticket_queue_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket queue admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_queue_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket queue admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket category admin
    # ------------------------------------------------------------
    try:
        from .ticket_category_admin import register_ticket_category_admin_commands
        register_ticket_category_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket category admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_category_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket category admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket governance / guardrails admin
    # ------------------------------------------------------------
    try:
        from .ticket_governance_admin import register_ticket_governance_admin_commands
        register_ticket_governance_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket governance admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_governance_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket governance admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket SLA admin
    # ------------------------------------------------------------
    try:
        from .ticket_sla_admin import register_ticket_sla_admin_commands
        register_ticket_sla_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket SLA admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_sla_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket SLA admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket resolution admin
    # ------------------------------------------------------------
    try:
        from .ticket_resolution_admin import register_ticket_resolution_admin_commands
        register_ticket_resolution_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket resolution admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_resolution_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket resolution admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket macro admin
    # ------------------------------------------------------------
    try:
        from .ticket_macro_admin import register_ticket_macro_admin_commands
        register_ticket_macro_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket macro admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_macro_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket macro admin commands: {repr(e)}")
        except Exception:
            pass

    # ------------------------------------------------------------
    # Ticket automation admin
    # ------------------------------------------------------------
    try:
        from .ticket_automation_admin import register_ticket_automation_admin_commands
        register_ticket_automation_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered ticket automation admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"ticket_automation_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering ticket automation admin commands: {repr(e)}")
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
    # Identity truth admin
    # ------------------------------------------------------------
    try:
        from .identity_admin import register_identity_admin_commands
        register_identity_admin_commands(bot, tree)
        try:
            print("✅ commands_ext: registered identity truth admin commands")
        except Exception:
            pass
    except Exception as e:
        errors.append(f"identity_admin: {repr(e)}")
        try:
            print(f"⚠️ commands_ext: failed registering identity truth admin commands: {repr(e)}")
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

    # NOTE:
    # Do not register runtime_jobs_admin here yet.
    # The bot is already at Discord's 100 global slash-command limit, and adding
    # runtime_jobs_status globally crashes startup with CommandLimitReached.
    # We will expose runtime queue stats through an existing command group later.

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
