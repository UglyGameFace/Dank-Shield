from __future__ import annotations

"""Scoped bot-access repair for Channel Name Fonts.

Adds a repair button to the font preview flow. It only uses the current preview's
blocked targets and only grants Dank Shield the minimum permissions needed to
rename those exact channels/categories.
"""

from typing import Any
import inspect

import discord

_PATCHED = False


def _bot_member(guild: discord.Guild) -> discord.Member | None:
    try:
        return guild.me if isinstance(guild.me, discord.Member) else None
    except Exception:
        return None


def _label(channel: Any) -> str:
    try:
        return str(getattr(channel, "mention", None) or f"`{getattr(channel, 'name', 'unknown')}`")
    except Exception:
        return "`unknown`"


async def _reply(interaction: discord.Interaction, message: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _repair(guild: discord.Guild, actor_id: int, blocked: list[dict[str, Any]]) -> dict[str, list[str]]:
    me = _bot_member(guild)
    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    if me is None:
        return {"changed": [], "unchanged": [], "failed": ["Dank Shield bot member could not be resolved."]}

    seen: set[int] = set()
    for row in blocked[:50]:
        cid = int(str(row.get("channel_id") or "0") or 0)
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        channel = guild.get_channel(cid)
        if channel is None or not callable(getattr(channel, "set_permissions", None)):
            failed.append(f"missing channel `{cid}`")
            continue
        try:
            perms = channel.permissions_for(me)
            if bool(getattr(perms, "view_channel", False)) and bool(getattr(perms, "manage_channels", False)):
                unchanged.append(_label(channel))
                continue
            current = channel.overwrites_for(me)
            expected = discord.PermissionOverwrite.from_pair(*current.pair())
            expected.view_channel = True
            expected.manage_channels = True
            expected.read_message_history = True
            await channel.set_permissions(me, overwrite=expected, reason=f"Dank Shield scoped font rename access repair by {actor_id}")
            changed.append(_label(channel))
        except discord.Forbidden:
            failed.append(f"{_label(channel)} — Discord denied permission edit")
        except Exception as exc:
            failed.append(f"{_label(channel)} — {type(exc).__name__}")
    return {"changed": changed, "unchanged": unchanged, "failed": failed}


class FontAccessRepairButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Fix Bot Access for These", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id="dank_setup_font:fix_bot_access", row=1)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        try:
            from stoney_verify.operation_queue import run_interaction_exclusive
            from stoney_verify.startup_guards import channel_font_rename_queue_guard as guard
        except Exception as exc:
            return await _reply(interaction, f"❌ Repair unavailable: `{type(exc).__name__}`")
        if not await guard._require_setup(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "❌ This must be used inside a server.")
        pending = guard._PENDING.get(guard._key(int(guild.id), int(interaction.user.id))) or {}
        blocked = list(pending.get("blocked_access") or [])
        if not blocked:
            return await _reply(interaction, "No bot-access blockers found. If the selected font cannot transform letters, use Auto-Fix Unsupported Font instead.")
        await interaction.response.defer(ephemeral=True, thinking=False)

        async def job() -> dict[str, list[str]]:
            return await _repair(guild, int(interaction.user.id), blocked)

        result = await run_interaction_exclusive(
            interaction=interaction,
            operation_type="channel_font_access_repair",
            action_label="Channel font bot-access repair",
            factory=job,
            fingerprint={"blocked": [str(x.get("channel_id")) for x in blocked[:50]]},
            risk_level="moderate",
            concurrency_class="channel_mutation",
            concurrency_key="channel_font_access_repair",
            timeout_seconds=180.0,
        )
        if result is None:
            return
        embed = discord.Embed(
            title="🛠️ Bot Access Repair Complete",
            description="Scoped repair only touched the channels/categories from this font preview and only granted Dank Shield bot access.",
            color=discord.Color.green() if not result.get("failed") else discord.Color.orange(),
        )
        embed.add_field(name="Repaired", value=str(len(result.get("changed") or [])), inline=True)
        embed.add_field(name="Already OK", value=str(len(result.get("unchanged") or [])), inline=True)
        embed.add_field(name="Failed", value=str(len(result.get("failed") or [])), inline=True)
        if result.get("changed"):
            embed.add_field(name="Changed", value="\n".join(result["changed"][:8])[:1024], inline=False)
        if result.get("failed"):
            embed.add_field(name="Still blocked", value="\n".join(result["failed"][:8])[:1024], inline=False)
        embed.add_field(name="Next", value="Run **Preview & Apply Channel Renames** again so the ready list refreshes.", inline=False)
        try:
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception:
            pass


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import channel_font_rename_queue_guard as guard
        view_cls = getattr(guard, "QueuedFontRenameConfirmView", None)
        if view_cls is None:
            return False
        try:
            if "can_fix_access" in inspect.signature(view_cls.__init__).parameters:
                _PATCHED = True
                return True
        except Exception:
            pass
        if getattr(view_cls, "_font_access_repair_patched", False):
            _PATCHED = True
            return True
        original_init = view_cls.__init__

        def patched_init(self: Any, *, enabled: bool) -> None:
            original_init(self, enabled=enabled)
            if not any(str(getattr(child, "custom_id", "")) == "dank_setup_font:fix_bot_access" for child in getattr(self, "children", []) or []):
                self.add_item(FontAccessRepairButton())

        view_cls.__init__ = patched_init
        setattr(view_cls, "_font_access_repair_patched", True)
        _PATCHED = True
        print("🔤 channel_font_access_repair_guard active; blocked font previews can repair scoped bot access")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ channel_font_access_repair_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply", "FontAccessRepairButton"]
