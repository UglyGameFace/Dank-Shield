from __future__ import annotations

try:
    import stoney_verify.commands_ext.public_verify_basic_panel  # noqa: F401
except Exception:
    pass

try:
    import importlib
    m = importlib.import_module("stoney_verify.startup_guards." + "panel_menu_" + "retry_guard")
    a = getattr(m, "apply", None)
    if callable(a):
        a()
except Exception:
    pass
