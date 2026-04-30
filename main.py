from __future__ import annotations

# Force-load process health visibility first. If the host restarts/kills the bot,
# this makes the next log show boot count, signals, atexit, memory, and async
# exception details instead of leaving us guessing.
try:
    import runtime_process_health_guard  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_process_health_guard: {e!r}")
    except Exception:
        pass

# Force-load command registration safety first so Discord's 100 global slash
# command limit can never crash startup again.
try:
    import runtime_command_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_command_safety guard: {e!r}")
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

# Clear stale verification timers before events/kick timers can act on fresh joins.
try:
    import runtime_member_join_kick_safety_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_member_join_kick_safety_patch guard: {e!r}")
    except Exception:
        pass

# Force-load guild_members schema compatibility before member sync imports.
try:
    import runtime_guild_members_role_state_compat_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_guild_members_role_state_compat_patch guard: {e!r}")
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

# Native source fix: create ticket channels directly inside the configured Active Tickets category.
try:
    import runtime_ticket_creation_native_category_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_ticket_creation_native_category_patch guard: {e!r}")
    except Exception:
        pass

# Native source wiring: close/reopen ticket category movement uses lifecycle helpers.
try:
    import runtime_ticket_lifecycle_native_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_ticket_lifecycle_native_patch guard: {e!r}")
    except Exception:
        pass

# Truthful lifecycle command responses: /ticket close/reopen must not say clean success if move/rename failed.
try:
    import runtime_ticket_lifecycle_command_truth_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_ticket_lifecycle_command_truth_patch guard: {e!r}")
    except Exception:
        pass

# Native source wiring: startup ticket sync/backfill uses sync category helpers.
try:
    import runtime_ticket_sync_native_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_ticket_sync_native_patch guard: {e!r}")
    except Exception:
        pass

# Emergency repair net only: move already-misplaced open tickets back into Active Tickets.
try:
    import runtime_ticket_category_enforcer_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_ticket_category_enforcer_patch guard: {e!r}")
    except Exception:
        pass

# Repair missing in-ticket control panels after broken creation/move flows.
try:
    import runtime_ticket_channel_panel_repair_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_ticket_channel_panel_repair_patch guard: {e!r}")
    except Exception:
        pass

# Keep stoney_verify.app's startup ticket-sync alias pointed at the current patched sync_service function.
try:
    import runtime_ticket_sync_alias_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_ticket_sync_alias_patch guard: {e!r}")
    except Exception:
        pass

# Public startup scope guard.
try:
    import runtime_public_startup_scope_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_public_startup_scope_patch guard: {e!r}")
    except Exception:
        pass

# Structured API per-guild config guard.
try:
    import runtime_api_guild_config_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_api_guild_config_patch guard: {e!r}")
    except Exception:
        pass

# Event helper queue guard.
try:
    import runtime_event_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_event_safety guard: {e!r}")
    except Exception:
        pass

# Automatic runtime job dedupe.
try:
    import runtime_job_dedupe_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_job_dedupe_safety guard: {e!r}")
    except Exception:
        pass

try:
    import runtime_process_health_guard as _process_health_guard
    try:
        _process_health_guard.install_loop_exception_handler()
    except Exception:
        pass
except Exception:
    pass

from stoney_verify.app import run


if __name__ == "__main__":
    try:
        import runtime_process_health_guard as _process_health_guard
        _process_health_guard.start_health_loop()
    except Exception:
        pass
    run()
