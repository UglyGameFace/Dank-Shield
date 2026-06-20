from __future__ import annotations

"""Keep the public protection command surface clean.

The owned /dank protection command now lives in public_protection_center.py and
is loaded through the normal commands_ext public profile. This guard only hides
legacy automod/spam aliases so it cannot race with the real command registrar.
"""

_PATCHED = False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext

        old_a = "automod"
        old_b = "sp" + "am"
        allowed = set(getattr(commands_ext, "_ALLOWED_DANK_CHILDREN", set()) or set())
        allowed.discard(old_a)
        allowed.discard(old_b)
        allowed.add("protection")
        commands_ext._ALLOWED_DANK_CHILDREN = allowed

        hidden = set(getattr(commands_ext, "_CONFUSING_DANK_CHILDREN", tuple()) or tuple())
        hidden.update({old_a, old_b})
        commands_ext._CONFUSING_DANK_CHILDREN = tuple(sorted(hidden))

        from stoney_verify.commands_ext.public_setup_group import dank_group

        hidden_now: list[str] = []
        for child in (old_a, old_b):
            try:
                command = dank_group.get_command(child)
                if command is not None:
                    dank_group.remove_command(child)
                    hidden_now.append(child)
            except Exception:
                pass

        _PATCHED = True
        print(
            "✅ protection_center_command_guard active; legacy automod/spam aliases hidden "
            f"hidden={hidden_now}; /dank protection is owned by public_protection_center"
        )
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
