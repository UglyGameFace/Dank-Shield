from __future__ import annotations

"""Expose /dank embed without touching other setup modules."""

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
        allowed.add("embed")
        commands_ext._ALLOWED_STONEY_CHILDREN = allowed

        from stoney_verify.commands_ext import public_embed_group

        register = getattr(public_embed_group, "register_public_embed_group_commands", None)
        if callable(register):
            register(None, None)
        _PATCHED = True
        print("✅ embed_builder_command_guard active; /dank embed is allowed in public setup surface")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ embed_builder_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
