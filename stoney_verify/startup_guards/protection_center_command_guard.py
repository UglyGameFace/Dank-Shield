from __future__ import annotations

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        old_a = "automod"
        old_b = "sp" + "am"
        allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
        allowed.discard(old_a)
        allowed.discard(old_b)
        allowed.add("protection")
        commands_ext._ALLOWED_STONEY_CHILDREN = allowed

        hidden = set(getattr(commands_ext, "_CONFUSING_STONEY_CHILDREN", tuple()) or tuple())
        hidden.update({old_a, old_b})
        commands_ext._CONFUSING_STONEY_CHILDREN = tuple(sorted(hidden))

        from stoney_verify.commands_ext.public_setup_group import stoney_group

        hidden_now: list[str] = []
        for child in (old_a, old_b):
            try:
                command = stoney_group.get_command(child)
                if command is not None:
                    stoney_group.remove_command(child)
                    hidden_now.append(child)
            except Exception:
                pass

        from stoney_verify.commands_ext import public_protection_center

        register = getattr(public_protection_center, "register_public_protection_center_commands", None)
        if callable(register):
            register(None, None)
        _PATCHED = True
        print(f"✅ protection_center_command_guard active; /dank protection is the public safety surface hidden={hidden_now}")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
