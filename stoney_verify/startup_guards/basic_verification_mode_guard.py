from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"✅ basic_verification_mode_guard: {message}")
    except Exception:
        pass


def _cfg_int(cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            raw = cfg.get(key) if hasattr(cfg, "get") else getattr(cfg, key, 0)
            value = int(str(raw or "0").strip() or "0")
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _patch_interaction_router() -> bool:
    try:
        from stoney_verify import interaction_handlers as handlers
        from stoney_verify.verification_new.basic_verify import maybe_handle_basic_verify_interaction
    except Exception as exc:
        _log(f"interaction patch skipped: {type(exc).__name__}")
        return False

    original = getattr(handlers, "handle_component_interaction", None)
    if not callable(original):
        return False
    if getattr(original, "_basic_verify_wrapped", False):
        return True

    async def handle_component_interaction_with_basic_verify(interaction: discord.Interaction) -> None:
        try:
            if await maybe_handle_basic_verify_interaction(interaction):
                return
        except Exception:
            pass
        return await original(interaction)

    setattr(handle_component_interaction_with_basic_verify, "_basic_verify_wrapped", True)
    handlers.handle_component_interaction = handle_component_interaction_with_basic_verify  # type: ignore[assignment]
    return True


def _patch_verify_group_panel_command() -> bool:
    try:
        from stoney_verify.commands_ext import public_verify_group as verify_mod
        from stoney_verify.guild_config import get_guild_config
        from stoney_verify.verification_new.basic_verify import post_basic_verify_panel
    except Exception as exc:
        _log(f"verify group panel command skipped: {type(exc).__name__}")
        return False

    group = getattr(verify_mod, "verify_group", None)
    if group is None:
        return False
    if getattr(group, "_basic_verify_panel_command_added", False):
        return True

    async def post_basic_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        ack = getattr(verify_mod, "_ack", None)
        staff_only = getattr(verify_mod, "_staff_only", None)
        send = getattr(verify_mod, "_send", None)
        if callable(ack):
            await ack(interaction)
        if callable(staff_only) and not await staff_only(interaction):
            return
        guild = interaction.guild
        if guild is None:
            if callable(send):
                await send(interaction, "❌ This command must be used inside a server.")
            return

        target = channel
        if target is None:
            try:
                cfg = await get_guild_config(guild.id, refresh=True)
                cid = _cfg_int(cfg, "verify_channel_id", "verification_channel_id")
                candidate = guild.get_channel(cid) if cid > 0 else None
                if isinstance(candidate, discord.TextChannel):
                    target = candidate
            except Exception:
                target = None
        if target is None:
            current = interaction.channel
            target = current if isinstance(current, discord.TextChannel) else None
        if target is None:
            if callable(send):
                await send(interaction, "❌ Pick a text channel for the Basic Verify panel.")
            return

        try:
            result = await post_basic_verify_panel(target, actor_id=int(getattr(interaction.user, "id", 0) or 0))
            if callable(send):
                await send(interaction, f"✅ Basic Verify panel {result} in {target.mention}.")
        except Exception as exc:
            if callable(send):
                await send(interaction, f"❌ Could not post Basic Verify panel: `{type(exc).__name__}`")

    post_basic_panel.__name__ = "verify_basic_panel"
    post_basic_panel.__doc__ = "Post or refresh this server's Basic Verify button panel."
    try:
        command = app_commands.Command(
            name="panel",
            description="Post/refresh the Basic Verify button panel.",
            callback=post_basic_panel,
        )
        group.add_command(command)
        setattr(group, "_basic_verify_panel_command_added", True)
        return True
    except Exception as exc:
        _log(f"panel command add skipped: {type(exc).__name__}")
        setattr(group, "_basic_verify_panel_command_added", True)
        return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    ok_interactions = _patch_interaction_router()
    ok_command = _patch_verify_group_panel_command()
    _PATCHED = bool(ok_interactions or ok_command)
    if _PATCHED:
        _log(f"active interactions={ok_interactions} panel_command={ok_command}")
    return _PATCHED


apply()

__all__ = ["apply"]
