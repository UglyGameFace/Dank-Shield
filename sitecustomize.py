from __future__ import annotations

"""
Python host fallback for Stoney Verify startup safety.

Some hosts auto-import ``sitecustomize`` before the normal app entrypoint. Keep
this file tiny: the real runtime safety logic lives in
``stoney_verify.startup_guards.runtime_safety``.
"""

try:
    from stoney_verify.startup_guards.runtime_safety import load_runtime_safety

    load_runtime_safety()
except Exception as e:
    try:
        print(f"⚠️ sitecustomize failed to load startup_guards.runtime_safety: {e!r}")
    except Exception:
        pass
