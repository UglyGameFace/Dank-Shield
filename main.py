from __future__ import annotations

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

from stoney_verify.app import run


if __name__ == "__main__":
    run()
