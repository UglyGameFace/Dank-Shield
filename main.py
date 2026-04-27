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
