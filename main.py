from __future__ import annotations

# Process health now lives inside stoney_verify/startup_guards instead of root runtime patches.
try:
    from stoney_verify.startup_guards import process_health  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import startup_guards.process_health: {e!r}")
    except Exception:
        pass

# Command safety now lives inside stoney_verify/startup_guards instead of root runtime patches.
# It also loads the package-local auto_shard and global_command_sync guards first.
try:
    from stoney_verify.startup_guards import command_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import startup_guards.command_safety: {e!r}")
    except Exception:
        pass

# Force-load runtime safety guards before the bot imports modules that may register
# blocking sync Supabase/PostgREST lookups. Some hosts do not auto-import
# sitecustomize even when it exists in the project root.
try:
    import sitecustomize  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import sitecustomize runtime guard: {e!r}")
    except Exception:
        pass

# Force-load a hard raidguard DB stop before app.py imports events/modlog.
try:
    import runtime_raidguard_hard_stop  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_raidguard_hard_stop guard: {e!r}")
    except Exception:
        pass

# Force-load disposable/bot-pattern raidguard heuristic hardening.
try:
    import runtime_raidguard_bot_heuristics_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_raidguard_bot_heuristics_patch guard: {e!r}")
    except Exception:
        pass

# Force-load Risk Engine v2 after heuristic hardening.
try:
    import runtime_raidguard_risk_engine_v2_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_raidguard_risk_engine_v2_patch guard: {e!r}")
    except Exception:
        pass

# Force-load alt-link safety after Risk Engine v2.
try:
    import runtime_alt_identity_link_safety_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_alt_identity_link_safety_patch guard: {e!r}")
    except Exception:
        pass

# Fresh-join removal safety now lives inside startup_guards instead of root runtime patches.
try:
    from stoney_verify.startup_guards import member_join_removal_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import startup_guards.member_join_removal_safety: {e!r}")
    except Exception:
        pass

# Guild member role-state compatibility now lives inside members_new instead of root runtime patches.
try:
    from stoney_verify.members_new import role_state_compat_guard  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import members_new.role_state_compat_guard: {e!r}")
    except Exception:
        pass

# Force-load Discord setup role preset safety before setup modules import.
try:
    import runtime_setup_role_safety_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_setup_role_safety_patch guard: {e!r}")
    except Exception:
        pass

# Force-load public ticket panel command before grouped commands import.
try:
    import runtime_public_ticket_panel_command_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_public_ticket_panel_command_patch guard: {e!r}")
    except Exception:
        pass

# Force-load clearer Ban/Unban command replacement before commands_ext imports.
try:
    import runtime_public_mod_ban_toggle_startup_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_public_mod_ban_toggle_startup_patch guard: {e!r}")
    except Exception:
        pass

# Force-load per-guild ticket config guard before tickets_new.service is imported.
try:
    import runtime_guild_config_ticket_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_guild_config_ticket_patch guard: {e!r}")
    except Exception:
        pass

# Ticket creation category guard now lives inside stoney_verify/tickets_new instead of root runtime patches.
try:
    from stoney_verify.tickets_new import creation_category_guard  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import tickets_new.creation_category_guard: {e!r}")
    except Exception:
        pass

# Ticket channel panel repair now lives inside stoney_verify/tickets_new instead of root runtime patches.
try:
    from stoney_verify.tickets_new import channel_panel_repair  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import tickets_new.channel_panel_repair: {e!r}")
    except Exception:
        pass

# Active ticket category enforcer now lives inside stoney_verify/tickets_new instead of root runtime patches.
try:
    from stoney_verify.tickets_new import category_enforcer  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import tickets_new.category_enforcer: {e!r}")
    except Exception:
        pass

# Ticket sync/backfill category guard now lives inside stoney_verify/tickets_new instead of root runtime patches.
try:
    from stoney_verify.tickets_new import sync_native_guard  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import tickets_new.sync_native_guard: {e!r}")
    except Exception:
        pass

# Ticket sync alias guard now lives inside stoney_verify/tickets_new instead of root runtime patches.
try:
    from stoney_verify.tickets_new import sync_alias_guard  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import tickets_new.sync_alias_guard: {e!r}")
    except Exception:
        pass

# Structured API guild-config guard now lives inside stoney_verify/api_new instead of root runtime patches.
try:
    from stoney_verify.api_new import guild_config_guard  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import api_new.guild_config_guard: {e!r}")
    except Exception:
        pass

# Public startup scope guard now lives inside stoney_verify/startup_guards instead of root runtime patches.
try:
    from stoney_verify.startup_guards import public_startup_scope  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import startup_guards.public_startup_scope: {e!r}")
    except Exception:
        pass

# Event helper queue guard now lives inside stoney_verify/startup_guards instead of root runtime patches.
try:
    from stoney_verify.startup_guards import event_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import startup_guards.event_safety: {e!r}")
    except Exception:
        pass

# Shard/scale readiness guard now lives inside stoney_verify/startup_guards instead of root runtime patches.
try:
    from stoney_verify.startup_guards import shard_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import startup_guards.shard_safety: {e!r}")
    except Exception:
        pass

# Automatic runtime job dedupe now lives inside stoney_verify/startup_guards instead of root runtime patches.
try:
    from stoney_verify.startup_guards import job_dedupe  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import startup_guards.job_dedupe: {e!r}")
    except Exception:
        pass

from stoney_verify.app import run


if __name__ == "__main__":
    try:
        process_health.start_health_loop()
    except Exception:
        pass
    run()
