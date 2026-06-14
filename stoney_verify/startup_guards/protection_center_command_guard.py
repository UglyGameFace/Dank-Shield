from __future__ import annotations

"""Expose /dank protection as the public safety surface.

Migration shim with removal path: once public_protection_center.py defines its
slash command directly against protection_center_services.open_protection_center,
remove the command replacement portion here and keep only the legacy automod/spam
hide list if still needed.
"""

import discord
from discord import app_commands

_PATCHED = False


async def _owned_protection_command(interaction: discord.Interaction) -> None:
    from stoney_verify import protection_center_services

    await protection_center_services.open_protection_center(interaction)


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

        try:
            existing = stoney_group.get_command("protection")
            if existing is not None:
                stoney_group.remove_command("protection")
        except Exception:
            pass

        owned_command = app_commands.Command(
            name="protection",
            description="Open the unified Automod + Spam Guard protection center.",
            callback=_owned_protection_command,
        )
        stoney_group.add_command(owned_command)

        _PATCHED = True
        print(
            "✅ protection_center_command_guard active; /dank protection routes through owned service "
            f"hidden={hidden_now}"
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
