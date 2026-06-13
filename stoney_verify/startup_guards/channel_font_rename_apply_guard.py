from __future__ import annotations

"""Preview/apply channel font renames from the Discord setup font tool."""

from typing import Any

import discord

_PENDING: dict[str, list[dict[str, Any]]] = {}


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _pending_key(guild_id: int, user_id: int) -> str:
    return f"{int(guild_id)}:{int(user_id)}"


async def _require_setup(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        return bool(await solid._require_setup_permission(interaction))
    except Exception:
        return False


async def _skip_context(guild_id: int) -> dict[str, Any]:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(guild_id, refresh=True)
    except Exception:
        cfg = {}
    return {
        "ticket_category_id": _safe_int(getattr(cfg, "get", lambda *_: None)("ticket_category_id"), 0),
        "ticket_archive_category_id": _safe_int(getattr(cfg, "get", lambda *_: None)("ticket_archive_category_id"), 0),
        "ticket_prefix": _safe_str(getattr(cfg, "get", lambda *_: None)("ticket_prefix"), "ticket").lower(),
    }


def _channel_kind(channel: Any) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    forum_cls = getattr(discord, "ForumChannel", None)
    if forum_cls is not None and isinstance(channel, forum_cls):
        return "forum"
    if isinstance(channel, discord.TextChannel):
        return "text"
    return "other"


def _should_skip(channel: Any, ctx: dict[str, Any]) -> bool:
    name = _safe_str(getattr(channel, "name", "")).lower()
    if not name:
        return True
    if name.startswith((_safe_str(ctx.get("ticket_prefix"), "ticket") + "-", "ticket-")):
        return True
    if "ticket archive" in name or "active tickets" in name or "transcript" in name:
        return True
    cid = _safe_int(getattr(channel, "id", 0), 0)
    if cid in {_safe_int(ctx.get("ticket_category_id"), 0), _safe_int(ctx.get("ticket_archive_category_id"), 0)}:
        return True
    parent = getattr(channel, "category", None)
    parent_id = _safe_int(getattr(parent, "id", 0), 0)
    if parent_id in {_safe_int(ctx.get("ticket_category_id"), 0), _safe_int(ctx.get("ticket_archive_category_id"), 0)}:
        return True
    parent_name = _safe_str(getattr(parent, "name", "")).lower()
    if "ticket archive" in parent_name or "active tickets" in parent_name or "transcript" in parent_name:
        return True
    return False


async def build_font_rename_plan(guild: discord.Guild, options: dict[str, str]) -> list[dict[str, Any]]:
    from stoney_verify.services.channel_builder_runtime import format_channel_builder_name

    ctx = await _skip_context(int(guild.id))
    out: list[dict[str, Any]] = []
    channels = list(getattr(guild, "categories", []) or []) + [
        ch for ch in list(getattr(guild, "channels", []) or []) if not isinstance(ch, discord.CategoryChannel)
    ]
    seen: set[int] = set()
    for channel in channels:
        channel_id = _safe_int(getattr(channel, "id", 0), 0)
        if channel_id <= 0 or channel_id in seen:
            continue
        seen.add(channel_id)
        if _channel_kind(channel) == "other" or _should_skip(channel, ctx):
            continue
        before = _safe_str(getattr(channel, "name", ""))
        after = _safe_str(format_channel_builder_name(before, {**options, "emoji": None}))[:100]
        if not after or after == before:
            continue
        out.append({
            "channel_id": str(channel_id),
            "kind": _channel_kind(channel),
            "before": before,
            "after": after,
        })
    return out[:150]


def _plan_text(plan: list[dict[str, Any]], limit: int = 18) -> str:
    if not plan:
        return "No rename changes found for the current font settings."
    rows: list[str] = []
    for item in plan[:limit]:
        rows.append(f"`{item.get('before')}` → `{item.get('after')}`")
    if len(plan) > limit:
        rows.append(f"…and {len(plan) - limit} more")
    return "\n".join(rows)[:3900]


async def preview_font_renames_embed(guild: discord.Guild, user_id: int, options: dict[str, str]) -> tuple[discord.Embed, list[dict[str, Any]]]:
    plan = await build_font_rename_plan(guild, options)
    _PENDING[_pending_key(int(guild.id), int(user_id))] = plan
    embed = discord.Embed(
        title="🔤 Preview Channel Font Renames",
        description=(
            "This is the actual rename plan. Nothing has been changed yet.\n\n"
            "Ticket/archive/transcript areas are skipped by default."
        ),
        color=discord.Color.orange() if plan else discord.Color.blurple(),
    )
    embed.add_field(name="Changes found", value=str(len(plan)), inline=True)
    embed.add_field(name="Preview", value=_plan_text(plan), inline=False)
    embed.set_footer(text="Review this first. Press Apply Previewed Renames only when it looks right.")
    return embed, plan


class FontRenamePreviewButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(label="Preview Channel Renames", emoji="👀", style=discord.ButtonStyle.success, custom_id="dank_setup_font:preview_renames", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.startup_guards.setup_channel_font_mode_guard import load_channel_font_options

        options = await load_channel_font_options(int(interaction.guild.id))
        embed, plan = await preview_font_renames_embed(interaction.guild, int(interaction.user.id), options)
        await interaction.response.edit_message(embed=embed, view=FontRenameConfirmView(enabled=bool(plan)))


class FontRenameConfirmView(discord.ui.View):
    def __init__(self, *, enabled: bool) -> None:
        super().__init__(timeout=900)
        self.apply_preview.disabled = not enabled

    @discord.ui.button(label="Apply Previewed Renames", emoji="✅", style=discord.ButtonStyle.danger, custom_id="dank_setup_font:apply_preview", row=0)
    async def apply_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        key = _pending_key(int(guild.id), int(interaction.user.id))
        plan = list(_PENDING.get(key) or [])
        if not plan:
            return await interaction.response.edit_message(content="No saved rename preview found. Press Preview Channel Renames again.", embed=None, view=None)
        await interaction.response.defer(ephemeral=True, thinking=False)
        changed = 0
        failed: list[str] = []
        for item in plan[:150]:
            channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
            if channel is None:
                failed.append(f"missing `{item.get('before')}`")
                continue
            before = _safe_str(getattr(channel, "name", ""))
            after = _safe_str(item.get("after"))[:100]
            if not after or before == after:
                continue
            try:
                await channel.edit(name=after, reason=f"Dank Shield channel font apply by {int(interaction.user.id)}")
                changed += 1
            except Exception as exc:
                failed.append(f"`{before}`: {type(exc).__name__}")
        _PENDING.pop(key, None)
        embed = discord.Embed(
            title="✅ Channel Font Renames Applied",
            description=f"Renamed **{changed}** channel(s).",
            color=discord.Color.green() if not failed else discord.Color.orange(),
        )
        if failed:
            embed.add_field(name="Skipped / failed", value="\n".join(failed[:10])[:1024], inline=False)
        await interaction.edit_original_response(embed=embed, view=FontRenameDoneView())

    @discord.ui.button(label="Back to Font Settings", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:back_to_fonts", row=0)
    async def back_to_fonts(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.startup_guards.setup_channel_font_mode_guard import build_channel_font_embed, load_channel_font_options, ChannelFontModeView

        options = await load_channel_font_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id), options_override=options), view=ChannelFontModeView(options))


class FontRenameDoneView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Back to Font Settings", emoji="🔤", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:done_back", row=0)
    async def done_back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.startup_guards.setup_channel_font_mode_guard import build_channel_font_embed, load_channel_font_options, ChannelFontModeView

        options = await load_channel_font_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id), options_override=options), view=ChannelFontModeView(options))


def apply() -> bool:
    try:
        print("🔤 channel_font_rename_apply_guard active; font setup can preview/apply real channel renames")
    except Exception:
        pass
    return True


apply()

__all__ = ["apply", "FontRenamePreviewButton", "build_font_rename_plan"]
