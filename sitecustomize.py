from __future__ import annotations

"""
Python host fallback for Dank Shield startup safety.

Some hosts auto-import ``sitecustomize`` before the normal app entrypoint. Keep
this file tiny: the real runtime safety logic lives in
``stoney_verify.startup_guards.runtime_safety``.
"""

try:
    import stoney_verify.startup_guards as startup_guards

    if not hasattr(startup_guards, "load_all_startup_guards") and hasattr(startup_guards, "load_startup_guards"):
        startup_guards.load_all_startup_guards = startup_guards.load_startup_guards
except Exception as e:
    try:
        print(f"⚠️ sitecustomize failed to install startup guard loader compatibility: {e!r}")
    except Exception:
        pass

try:
    from stoney_verify.startup_guards.runtime_safety import load_runtime_safety

    load_runtime_safety()
except Exception as e:
    try:
        print(f"⚠️ sitecustomize failed to load startup_guards.runtime_safety: {e!r}")
    except Exception:
        pass
