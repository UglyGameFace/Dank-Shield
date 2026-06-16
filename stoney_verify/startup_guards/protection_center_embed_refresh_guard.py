from __future__ import annotations

"""Keep Protection Center updates inside the embed card."""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_EMBED: Any = None
_ORIGINAL_REFRESH: Any = None
_ORIGINAL_INIT: Any = None
_LAST: dict[str, Any] = {}


def _cfg_bool(center: Any, cfg: Any, key: str, default: bool = False) -> bool:
    try:
        return bool(center._cfg_bool(cfg, key, default))
    except Exception:
        raw = default
        try:
            raw = center._cfg_value(cfg, key, default)
        except Exception:
            pass
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _cfg_value(center: Any, cfg: Any, key: str, default: Any = None) -> Any:
    try:
        return center._cfg_value(cfg, key, default)
    except Exception:
        return default


def _saved_filters(center: Any, cfg: Any) -> list[str]:
    try:
        return list(center._csv_items(_cfg_value(center, cfg, "automod_bad_words", "")))
    except Exception:
        return []


def _state(center: Any, cfg: Any, spam: dict[str, Any]) -> dict[str, Any]:
    return {
        "preset": str(_cfg_value(center, cfg, "automod_preset", "custom") or "custom"),
        "automod": _cfg_bool(center, cfg, "automod_enabled", False),
        "invites": _cfg_bool(center, cfg, "automod_block_invites", False),
        "links": _cfg_bool(center, cfg, "automod_block_links", False),
        "spam": bool((spam or {}).get("enabled")),
        "filters": len(_saved_filters(center, cfg)),
    }


def _status_text(data: dict[str, Any]) -> str:
    off = not bool(data.get("automod")) and not bool(data.get("spam"))
    return (
        f"**Safe Defaults:** {'✅ active' if data.get('preset') == 'safe' else '⚪ off'}\n"
        f"**Strict Mode:** {'✅ active' if data.get('preset') == 'strict' else '⚪ off'}\n"
        f"**Turn Off:** {'✅ active' if off else '⚪ off'}\n"
        f"**Invite Shield:** {'✅ on' if data.get('invites') else '⚪ off'}\n"
        f"**Link Shield:** {'✅ on' if data.get('links') else '⚪ off'}\n"
        f"**Spam Guard:** {'✅ on' if data.get('spam') else '⚪ off'}\n"
        f"**Tracked Filters:** `{data.get('filters', 0)}`"
    )


def _add_status(embed: discord.Embed, data: dict[str, Any]) -> discord.Embed:
    try:
        fields = [f for f in list(embed.fields) if str(getattr(f, "name", "") or "").lower() != "live status"]
        embed.clear_fields()
        embed.add_field(name="Live Status", value=_status_text(data), inline=False)
        for field in fields:
            embed.add_field(name=field.name, value=field.value, inline=field.inline)
    except Exception:
        try:
            embed.add_field(name="Live Status", value=_status_text(data), inline=False)
        except Exception:
            pass
    return embed


def _decorate(view: Any, data: dict[str, Any]) -> None:
    for child in list(getattr(view, "children", []) or []):
        cid = str(getattr(child, "custom_id", "") or "")
        try:
            if cid == "dank_protection:safe":
                child.label = "Safe Defaults" + (" ✅" if data.get("preset") == "safe" else "")
                child.style = discord.ButtonStyle.success if data.get("preset") == "safe" else discord.ButtonStyle.secondary
            elif cid == "dank_protection:strict":
                child.label = "Strict Mode" + (" ✅" if data.get("preset") == "strict" else "")
                child.style = discord.ButtonStyle.primary if data.get("preset") == "strict" else discord.ButtonStyle.secondary
            elif cid == "dank_protection:off":
                off = not bool(data.get("automod")) and not bool(data.get("spam"))
                child.label = "Turn Off" + (" ✅" if off else "")
                child.style = discord.ButtonStyle.danger if off else discord.ButtonStyle.secondary
            elif cid == "dank_protection:invite_scope":
                invites_on = bool(data.get("invites"))
                child.label = f"Invite Shield: {'ON' if invites_on else 'OFF'}"
                child.style = discord.ButtonStyle.success if invites_on else discord.ButtonStyle.secondary
            elif cid == "dank_protection:block_links":
                links_on = bool(data.get("links"))
                child.label = f"Link Shield: {'ON' if links_on else 'OFF'}"
                child.style = discord.ButtonStyle.success if links_on else discord.ButtonStyle.secondary
            elif cid == "dank_protection:add_filter":
                child.label = f"Bad Word Filter ({data.get('filters', 0)})"
        except Exception:
            pass


async def _card(center: Any, interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
    cfg = await center.get_guild_config(int(guild.id), refresh=True)
    spam, source = await center._load_spam_settings(int(guild.id))
    embed = center._protection_embed(guild, cfg, spam, source)
    view = center.ProtectionCenterView(author_id=int(interaction.user.id))
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=None, embed=embed, view=view)
        else:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        return
    except Exception:
        pass
    try:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def apply() -> bool:
    global _PATCHED, _ORIGINAL_EMBED, _ORIGINAL_REFRESH, _ORIGINAL_INIT
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_protection_center as center
        _ORIGINAL_EMBED = center._protection_embed
        _ORIGINAL_REFRESH = center._refresh_panel
        _ORIGINAL_INIT = center.ProtectionCenterView.__init__

        def patched_embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str) -> discord.Embed:
            data = _state(center, cfg, spam)
            _LAST.clear()
            _LAST.update(data)
            return _add_status(_ORIGINAL_EMBED(guild, cfg, spam, spam_source), data)

        async def patched_refresh(interaction: discord.Interaction, *, content: str | None = None) -> None:
            _ = content
            await _card(center, interaction)

        def patched_init(self: Any, *, author_id: int) -> None:
            _ORIGINAL_INIT(self, author_id=author_id)
            _decorate(self, dict(_LAST))

        center._protection_embed = patched_embed
        center._refresh_panel = patched_refresh
        center.ProtectionCenterView.__init__ = patched_init
        _PATCHED = True
        print("✅ protection_center_embed_refresh_guard active; protection status updates stay inside the card")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_embed_refresh_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]