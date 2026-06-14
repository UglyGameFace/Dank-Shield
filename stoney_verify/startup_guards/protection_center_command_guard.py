from __future__ import annotations

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
        allowed.add("protection")
        commands_ext._ALLOWED_STONEY_CHILDREN = allowed

        # Legacy surfaces are intentionally removed from public autocomplete.
        confusing = set(getattr(commands_ext, "_CONFUSING_STONEY_CHILDREN", tuple()) or tuple())
        confusing.update({"automod", "spam"})
        commands_ext._CONFUSING_STONEY_CHILDREN = tuple(sorted(confusing))

        from stoney_verify.commands_ext import public_protection_center

        register = getattr(public_protection_center, "register_public_protection_center_commands", None)
        if callable(register):
            register(None, None)
        _PATCHED = True
        print("✅ protection_center_command_guard active; /dank protection is the public safety surface")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
