from __future__ import annotations

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
# This prevents sync Supabase/PostgREST identity lookups from blocking Discord
# heartbeat inside voice-state modlog paths.
try:
    import runtime_raidguard_hard_stop  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_raidguard_hard_stop guard: {e!r}")
    except Exception:
        pass

# Force-load disposable/bot-pattern raidguard heuristic hardening.
# This keeps obvious human-name + long numeric suffix accounts from staying
# misleadingly LOW/CLEAR when the account looks bot-farm/disposable.
try:
    import runtime_raidguard_bot_heuristics_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_raidguard_bot_heuristics_patch guard: {e!r}")
    except Exception:
        pass

# Force-load Risk Engine v2 after heuristic hardening.
# Public listing sites like Disboard/Discodus/Discordfy/Discadia are treated as
# expected growth sources, not as suspicious by themselves.
try:
    import runtime_raidguard_risk_engine_v2_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_raidguard_risk_engine_v2_patch guard: {e!r}")
    except Exception:
        pass

# Force-load alt-link safety after Risk Engine v2.
# This removes self-matches, dedupes cluster members, and separates known alt ties
# from weak possible-related-account matches.
try:
    import runtime_alt_identity_link_safety_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_alt_identity_link_safety_patch guard: {e!r}")
    except Exception:
        pass

# Force-load guild_members schema compatibility before member sync imports.
# Older DB constraints do not allow role_state='cosmetic_only', so the guard
# stores it compatibly while preserving has_cosmetic_only=true.
try:
    import runtime_guild_members_role_state_compat_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_guild_members_role_state_compat_patch guard: {e!r}")
    except Exception:
        pass

# Force-load per-guild ticket config guard before tickets_new.service is imported.
# Public/beta bots must resolve ticket category/staff/transcript settings from
# guild_configs instead of one env-only guild.
try:
    import runtime_guild_config_ticket_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_guild_config_ticket_patch guard: {e!r}")
    except Exception:
        pass

# Keep stoney_verify.app's startup ticket-sync alias pointed at the current
# patched sync_service function. Without this, app.py can keep a stale function
# reference captured before runtime_guild_config_ticket_patch wraps sync_service.
try:
    import runtime_ticket_sync_alias_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_ticket_sync_alias_patch guard: {e!r}")
    except Exception:
        pass

# Public startup scope guard: sync public commands globally and run startup
# maintenance only for guilds with saved guild_configs rows.
try:
    import runtime_public_startup_scope_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_public_startup_scope_patch guard: {e!r}")
    except Exception:
        pass

# Force-load structured API per-guild config guard before api_new.server is imported.
# This makes dashboard/API lifecycle actions use each guild's configured ticket
# categories instead of one env-only category.
try:
    import runtime_api_guild_config_patch  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_api_guild_config_patch guard: {e!r}")
    except Exception:
        pass

# Force-load event helper queue guard. This keeps member sync / startup event
# maintenance helpers from running inline in Discord gateway/startup paths.
try:
    import runtime_event_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_event_safety guard: {e!r}")
    except Exception:
        pass

# Force-load automatic runtime job dedupe. This makes queued work coalesce by
# default, even if a producer forgets to provide a dedupe key.
try:
    import runtime_job_dedupe_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_job_dedupe_safety guard: {e!r}")
    except Exception:
        pass

from stoney_verify.app import run


if __name__ == "__main__":
    run()
