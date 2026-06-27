from __future__ import annotations

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.services import server_design_studio as studio

        protected = set(getattr(studio, "DEFAULT_PROTECTED_NAMES", set()) or set())
        protected.update({
            "log",
            "logs",
            "mod-log",
            "mod-logs",
            "staff-log",
            "staff-logs",
            "audit-log",
            "audit-logs",
            "ticket-log",
            "ticket-logs",
        })
        studio.DEFAULT_PROTECTED_NAMES = protected
        try:
            studio.ThemePreset.__dataclass_fields__["protected_defaults"].default = tuple(sorted(protected))
        except Exception:
            pass
        _PATCHED = True
        print("✅ server_design_protected_defaults_guard active; log/modlog/ticket log channels are protected by default")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_protected_defaults_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
