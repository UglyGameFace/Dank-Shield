from __future__ import annotations

"""Add a saved content-filter list button to Protection Center."""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_INIT: Any = None


def _cfg_value(center: Any, cfg: Any, key: str, default: Any = None) -> Any:
    try:
        return center._cfg_value(cfg, key, default)
    except Exception:
        try:
            return getattr(cfg, key, default)
        except Exception:
            return default


def _items(center: Any, cfg: Any) -> list[str]:
    try:
        return list(center._csv_items(_cfg_value(center, cfg, "automod_bad_words", "")))
    except Exception:
        return []


class TrackedFiltersButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Tracked Filters", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="dank_protection:tracked_filters", row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext import public_protection_center as center
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        cfg = await center.get_guild_config(int(guild.id), refresh=True)
        values = _items(center, cfg)
        embed = discord.Embed(title="📋 Tracked Content Filters", color=discord.Color.blurple())
        if values:
            body = "\n".join(f"• `{item}`" for item in values[:45])
            if len(values) > 45:
                body += f"\n…and {len(values) - 45} more"
            embed.description = body[:3900]
        else:
            embed.description = "No saved content filters yet. Use **Bad Word Filter** to add one."
        embed.set_footer(text=f"{len(values)} saved filter(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def _chain_cleanup_picker() -> None:
    try:
        from stoney_verify.startup_guards import protection_invite_cleanup_picker_guard as picker
        picker.apply()
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED, _ORIGINAL_INIT
    _chain_cleanup_picker()
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_protection_center as center
        _ORIGINAL_INIT = center.ProtectionCenterView.__init__

        def patched_init(self: Any, *, author_id: int) -> None:
            _ORIGINAL_INIT(self, author_id=author_id)
            try:
                for child in list(getattr(self, "children", []) or []):
                    if str(getattr(child, "custom_id", "") or "") == "dank_protection:tracked_filters":
                        return
                self.add_item(TrackedFiltersButton())
            except Exception:
                pass

        center.ProtectionCenterView.__init__ = patched_init
        _PATCHED = True
        print("✅ protection_center_filter_list_guard active; saved content filters can be viewed")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_filter_list_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]