from __future__ import annotations

"""Queue-backed channel font rename preview/apply/undo.

This guard intentionally uses stoney_verify.operation_queue so the font rename
flow meshes with Dank Shield's existing global caps, per-guild concurrency
locks, duplicate-click protection, and operation metrics.
"""

import time
from typing import Any

import discord

MAX_PLAN_ITEMS = 150
MAX_PENDING_SECONDS = 30 * 60
_PENDING: dict[str, dict[str, Any]] = {}
_LAST_UNDO: dict[str, dict[str, Any]] = {}
_PATCHED = False


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


def _key(guild_id: int, user_id: int) -> str:
    return f"{int(guild_id)}:{int(user_id)}"


def _purge() -> None:
    now = time.time()
    for key, payload in list(_PENDING.items()):
        if now - float(payload.get("created_at") or 0) > MAX_PENDING_SECONDS:
            _PENDING.pop(key, None)


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
    getter = getattr(cfg, "get", lambda *_: None)
    return {
        "ticket_category_id": _safe_int(getter("ticket_category_id"), 0),
        "ticket_archive_category_id": _safe_int(getter("ticket_archive_category_id"), 0),
        "ticket_prefix": _safe_str(getter("ticket_prefix"), "ticket").lower(),
    }


def _kind(channel: Any) -> str:
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


def _skip(channel: Any, ctx: dict[str, Any]) -> bool:
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
    return "ticket archive" in parent_name or "active tickets" in parent_name or "transcript" in parent_name


async def build_plan(guild: discord.Guild, options: dict[str, str]) -> list[dict[str, Any]]:
    from stoney_verify.services.channel_builder_runtime import format_channel_builder_name
    ctx = await _skip_context(int(guild.id))
    channels = list(getattr(guild, "categories", []) or []) + [c for c in list(getattr(guild, "channels", []) or []) if not isinstance(c, discord.CategoryChannel)]
    seen: set[int] = set()
    plan: list[dict[str, Any]] = []
    for channel in channels:
        cid = _safe_int(getattr(channel, "id", 0), 0)
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        if _kind(channel) == "other" or _skip(channel, ctx):
            continue
        before = _safe_str(getattr(channel, "name", ""))
        after = _safe_str(format_channel_builder_name(before, {**options, "emoji": None}))[:100]
        if after and after != before:
            plan.append({"channel_id": str(cid), "before": before, "after": after, "kind": _kind(channel)})
        if len(plan) >= MAX_PLAN_ITEMS:
            break
    return plan


def _plan_text(plan: list[dict[str, Any]], limit: int = 18) -> str:
    if not plan:
        return "No rename changes found for the current font settings."
    rows = [f"`{item.get('before')}` → `{item.get('after')}`" for item in plan[:limit]]
    if len(plan) > limit:
        rows.append(f"…and {len(plan) - limit} more")
    return "\n".join(rows)[:3900]


async def _preview_embed(guild: discord.Guild, user_id: int, options: dict[str, str]) -> tuple[discord.Embed, list[dict[str, Any]]]:
    _purge()
    plan = await build_plan(guild, options)
    _PENDING[_key(int(guild.id), int(user_id))] = {"created_at": time.time(), "plan": plan, "options": dict(options)}
    embed = discord.Embed(
        title="🔤 Preview Channel Font Renames",
        description="This is the actual rename plan. Nothing has been changed yet. Ticket/archive/transcript areas are skipped.",
        color=discord.Color.orange() if plan else discord.Color.blurple(),
    )
    embed.add_field(name="Changes found", value=str(len(plan)), inline=True)
    embed.add_field(name="Safety cap", value=str(MAX_PLAN_ITEMS), inline=True)
    embed.add_field(name="Preview", value=_plan_text(plan), inline=False)
    return embed, plan


async def _apply_plan(interaction: discord.Interaction, plan: list[dict[str, Any]]) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None
    changed = 0
    failed: list[str] = []
    undo: list[dict[str, str]] = []
    for item in plan[:MAX_PLAN_ITEMS]:
        channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
        if channel is None:
            failed.append(f"missing `{item.get('before')}`")
            continue
        current = _safe_str(getattr(channel, "name", ""))
        before = _safe_str(item.get("before"))
        after = _safe_str(item.get("after"))[:100]
        if not after or current == after:
            continue
        if current != before:
            failed.append(f"stale `{before}` now `{current}`")
            continue
        try:
            await channel.edit(name=after, reason=f"Dank Shield channel font apply by {int(interaction.user.id)}")
            changed += 1
            undo.append({"channel_id": str(getattr(channel, "id", "")), "before": before, "after": after})
        except Exception as exc:
            failed.append(f"`{current}`: {type(exc).__name__}")
    if undo:
        _LAST_UNDO[_key(int(guild.id), int(interaction.user.id))] = {"created_at": time.time(), "undo": undo}
    return {"status": "partial" if failed else "succeeded", "changed": changed, "failed": failed[:10], "undo": undo}


async def _undo_plan(interaction: discord.Interaction, plan: list[dict[str, Any]]) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None
    reverted = 0
    failed: list[str] = []
    for item in reversed(plan[:MAX_PLAN_ITEMS]):
        channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
        if channel is None:
            failed.append(f"missing `{item.get('after')}`")
            continue
        current = _safe_str(getattr(channel, "name", ""))
        applied = _safe_str(item.get("after"))
        old = _safe_str(item.get("before"))[:100]
        if current != applied:
            failed.append(f"stale `{applied}` now `{current}`")
            continue
        try:
            await channel.edit(name=old, reason=f"Dank Shield channel font undo by {int(interaction.user.id)}")
            reverted += 1
        except Exception as exc:
            failed.append(f"`{current}`: {type(exc).__name__}")
    return {"status": "partial" if failed else "succeeded", "reverted": reverted, "failed": failed[:10]}


class QueuedFontRenamePreviewButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(label="Preview Channel Renames", emoji="👀", style=discord.ButtonStyle.success, custom_id="dank_setup_font:preview_renames", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.startup_guards.setup_channel_font_mode_guard import load_channel_font_options
        options = await load_channel_font_options(int(interaction.guild.id))
        embed, plan = await _preview_embed(interaction.guild, int(interaction.user.id), options)
        await interaction.response.edit_message(embed=embed, view=QueuedFontRenameConfirmView(enabled=bool(plan)))


class QueuedFontRenameConfirmView(discord.ui.View):
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
        pending = _PENDING.get(_key(int(guild.id), int(interaction.user.id))) or {}
        plan = list(pending.get("plan") or [])
        if not plan:
            return await interaction.response.edit_message(content="No saved preview found. Press Preview Channel Renames again.", embed=None, view=None)
        await interaction.response.defer(ephemeral=True, thinking=False)
        from stoney_verify.operation_queue import run_interaction_exclusive
        result = await run_interaction_exclusive(
            interaction=interaction,
            operation_type="channel_font_rename_apply",
            action_label="Channel font rename apply",
            factory=lambda: _apply_plan(interaction, plan),
            fingerprint={"plan": plan},
            risk_level="dangerous",
            concurrency_class="channel_mutation",
            concurrency_key="channel_font_rename",
            timeout_seconds=900.0,
        )
        if result is None:
            return
        _PENDING.pop(_key(int(guild.id), int(interaction.user.id)), None)
        embed = discord.Embed(title="✅ Channel Font Renames Applied", description=f"Renamed **{int(result.get('changed', 0) or 0)}** channel(s).", color=discord.Color.green() if not result.get("failed") else discord.Color.orange())
        if result.get("failed"):
            embed.add_field(name="Skipped / failed", value="\n".join(result.get("failed")[:10])[:1024], inline=False)
        if result.get("undo"):
            embed.add_field(name="Undo available", value="Use **Undo Last Font Rename** if this needs to be rolled back.", inline=False)
        await interaction.edit_original_response(embed=embed, view=QueuedFontRenameDoneView(can_undo=bool(result.get("undo"))))

    @discord.ui.button(label="Back to Font Settings", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:back_to_fonts", row=0)
    async def back_to_fonts(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.startup_guards.setup_channel_font_mode_guard import build_channel_font_embed, load_channel_font_options, ChannelFontModeView
        options = await load_channel_font_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id), options_override=options), view=ChannelFontModeView(options))


class QueuedFontRenameDoneView(discord.ui.View):
    def __init__(self, *, can_undo: bool = False) -> None:
        super().__init__(timeout=900)
        self.undo_last.disabled = not can_undo

    @discord.ui.button(label="Undo Last Font Rename", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_setup_font:undo_last", row=0)
    async def undo_last(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        undo = list((_LAST_UNDO.get(_key(int(guild.id), int(interaction.user.id))) or {}).get("undo") or [])
        if not undo:
            return await interaction.response.send_message("No undo snapshot found for your last font rename.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=False)
        from stoney_verify.operation_queue import run_interaction_exclusive
        result = await run_interaction_exclusive(
            interaction=interaction,
            operation_type="channel_font_rename_undo",
            action_label="Channel font rename undo",
            factory=lambda: _undo_plan(interaction, undo),
            fingerprint={"undo": undo},
            risk_level="dangerous",
            concurrency_class="channel_mutation",
            concurrency_key="channel_font_rename",
            timeout_seconds=900.0,
        )
        if result is None:
            return
        _LAST_UNDO.pop(_key(int(guild.id), int(interaction.user.id)), None)
        embed = discord.Embed(title="↩️ Channel Font Rename Undo Complete", description=f"Restored **{int(result.get('reverted', 0) or 0)}** channel name(s).", color=discord.Color.green() if not result.get("failed") else discord.Color.orange())
        if result.get("failed"):
            embed.add_field(name="Skipped / failed", value="\n".join(result.get("failed")[:10])[:1024], inline=False)
        await interaction.edit_original_response(embed=embed, view=QueuedFontRenameDoneView(can_undo=False))

    @discord.ui.button(label="Back to Font Settings", emoji="🔤", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:done_back", row=0)
    async def done_back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.startup_guards.setup_channel_font_mode_guard import build_channel_font_embed, load_channel_font_options, ChannelFontModeView
        options = await load_channel_font_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=await build_channel_font_embed(int(interaction.guild.id), options_override=options), view=ChannelFontModeView(options))


def _patch_font_view() -> bool:
    try:
        from stoney_verify.startup_guards import setup_channel_font_mode_guard as font_guard
        view_cls = getattr(font_guard, "ChannelFontModeView", None)
        if view_cls is None or getattr(view_cls, "_queue_rename_patched", False):
            return False
        original_init = view_cls.__init__
        def patched_init(self: Any, options: dict[str, str]) -> None:
            original_init(self, options)
            if not any(str(getattr(child, "custom_id", "")) == "dank_setup_font:preview_renames" for child in getattr(self, "children", []) or []):
                self.add_item(QueuedFontRenamePreviewButton(row=3))
        view_cls.__init__ = patched_init
        setattr(view_cls, "_queue_rename_patched", True)
        return True
    except Exception:
        return False


def apply() -> bool:
    _patch_font_view()
    try:
        print("🔤 channel_font_rename_queue_guard active; font renames use operation_queue channel_mutation locks")
    except Exception:
        pass
    return True


apply()

__all__ = ["apply", "QueuedFontRenamePreviewButton", "build_plan"]
