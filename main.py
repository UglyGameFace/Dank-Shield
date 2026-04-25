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

# Force-load event helper queue guard. This keeps member sync / startup event
# maintenance helpers from running inline in Discord gateway/startup paths.
try:
    import runtime_event_safety  # noqa: F401
except Exception as e:
    try:
        print(f"⚠️ main.py failed to import runtime_event_safety guard: {e!r}")
    except Exception:
        pass

from stoney_verify.app import run


if __name__ == "__main__":
    run()
