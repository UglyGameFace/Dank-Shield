from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from ..guild_config import get_guild_config
from ..verification_new.basic_verify import (
    maybe_handle_basic_verify_interaction,
    post_basic_verify_panel,
    register_basic_verify_runtime,
)
from .public_verify_group import _cfg_value, _safe_int, _send, _staff_only, verify_group

_ATTACHED = False
_LISTENER_ATTACHED = False


def _cfg_int(cfg: Any, *keys: str) -> int:
    for key in keys:
        value = _safe_int(_cfg_value(cfg, key), 0)
        if value > 0:
            return value
    return 0


async def _pick_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    guild = interaction.guild
    if guild is not None:
        try:
            cfg = await get_guild_config(guild.id, refresh=True)
            cid = _cfg_int(cfg, "verify_channel_id", "verification_channel_id")
            saved = guild.get_channel(cid) if cid > 0 else None
            if isinstance(saved, discord.TextChannel):
                return saved
        except Exception:
            pass
    current = interaction.channel
    return current if isinstance(current, discord.TextChannel) else None


def _install_basic_verify_runtime(bot: Any) -> bool:
    global _LISTENER_ATTACHED
    registered = register_basic_verify_runtime(bot)

    if _LISTENER_ATTACHED:
        return bool(registered)

    listen = getattr(bot, "listen", None)
    if not callable(listen):
        return bool(registered)

    @bot.listen("on_interaction")
    async def _basic_verify_panel_runtime(interaction: discord.Interaction) -> None:
        try:
            await maybe_handle_basic_verify_interaction(interaction)
        except Exception:
            pass

    _LISTENER_ATTACHED = True
    try:
        print("public_verify_basic_panel runtime installed")
    except Exception:
        pass
    return True


async def verify_panel(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass
    if not await _staff_only(interaction):
        return
    target = await _pick_channel(interaction)
    if target is None:
        return await _send(interaction, "Pick a text channel or save one in /dank setup.")
    try:
        runtime_ready = _install_basic_verify_runtime(getattr(interaction, "client", None))
        result = await post_basic_verify_panel(target, actor_id=int(getattr(interaction.user, "id", 0) or 0))
        suffix = " Runtime handler ready." if runtime_ready else " Runtime handler was not confirmed; restart the bot if the button still fails."
        await _send(interaction, f"Panel {result} in {target.mention}.{suffix}")
    except Exception as exc:
        await _send(interaction, f"Could not post panel: {type(exc).__name__}")


def _attach() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True
    try:
        existing = getattr(verify_group, "get_command", lambda _name: None)("panel")
        if existing is not None:
            _ATTACHED = True
            return True
    except Exception:
        pass
    try:
        command = app_commands.Command(name="panel", description="Post or refresh the server verify panel.", callback=verify_panel)
        verify_group.add_command(command)
        _ATTACHED = True
        try:
            print("public_verify_basic_panel attached panel command")
        except Exception:
            pass
        return True
    except Exception as exc:
        try:
            print(f"public_verify_basic_panel failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


def register_public_verify_basic_panel_commands(bot: Any, tree: Any) -> None:
    _ = tree
    _install_basic_verify_runtime(bot)
    _attach()


def apply() -> bool:
    return _attach()


apply()

__all__ = ["apply", "register_public_verify_basic_panel_commands", "verify_panel"]
