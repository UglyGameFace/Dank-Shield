from __future__ import annotations

"""Preserve the Voice Verify session-access contract in setup health screens.

Presentation layers may replace the detailed setup-health embed, but they must
never imply that a broad role receives standing access to the private room. The
room is unlocked only for the active requester and assigned staff during an
active verification session.
"""

from typing import Any

import discord

_PATCH_MARKER = "_dank_voice_session_health_contract"


def _rendered_text(embed: discord.Embed) -> str:
    return "\n".join(
        [str(embed.description or "")]
        + [str(field.value or "") for field in embed.fields]
    )


async def _apply_contract(
    guild: discord.Guild,
    embed: discord.Embed,
    *,
    setup: Any,
) -> discord.Embed:
    try:
        cfg = await setup.get_guild_config(guild.id, refresh=True)
        services: dict[str, Any] = dict(setup._selected_setup_services(cfg))
    except Exception:
        services = {}

    if bool(services.get("voice", False)):
        rendered = _rendered_text(embed).lower()
        if "active requester" not in rendered or "assigned staff" not in rendered:
            embed.add_field(
                name="🔒 Voice Verify Room Access",
                value=(
                    "The private room stays hidden and locked by default. Dank "
                    "Shield grants voice, video, and screen-share access only to "
                    "the **active requester** and **assigned staff** for the "
                    "current session, then removes those member-specific "
                    "permissions when it ends."
                ),
                inline=False,
            )
    return embed


def _wrap_health(owner: Any, attribute: str, *, setup: Any) -> bool:
    original = getattr(owner, attribute, None)
    if not callable(original):
        return False
    if bool(getattr(original, _PATCH_MARKER, False)):
        return True

    async def wrapped(guild: discord.Guild) -> discord.Embed:
        embed = await original(guild)
        return await _apply_contract(guild, embed, setup=setup)

    setattr(wrapped, _PATCH_MARKER, True)
    setattr(wrapped, "__wrapped__", original)
    setattr(owner, attribute, wrapped)
    return True


def install_voice_health_contract() -> bool:
    from stoney_verify.commands_ext import public_setup_recommend as setup

    installed = _wrap_health(
        setup,
        "_build_plain_setup_health_embed",
        setup=setup,
    )

    # Compact setup owns a replacement renderer and may install it after the
    # canonical setup function was already wrapped. Wrap the renderer itself so
    # later apply_compact_setup_patch() calls cannot erase this contract.
    try:
        from stoney_verify.setup_ui import public_setup_compact as compact

        installed = bool(
            _wrap_health(compact, "_health_embed", setup=setup)
            or installed
        )
    except Exception:
        pass

    return installed


__all__ = ["install_voice_health_contract"]
