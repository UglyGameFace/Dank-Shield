from __future__ import annotations

"""Queue-backed channel font rename preview/apply/undo.

Uses Dank Shield's operation queue plus the shared channel mutation throttle.
Each apply/undo only processes a small paced batch.

Preview performs a bot-access preflight so channels that Dank Shield cannot edit
are shown as blocked before Apply, rather than becoming surprise skips later.
"""

import time
from typing import Any

import discord

from stoney_verify.services.channel_mutation_throttle import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DELAY_SECONDS,
    DEFAULT_MAX_ITEMS,
    run_paced_channel_mutations,
)

MAX_PLAN_ITEMS = min(150, DEFAULT_MAX_ITEMS)
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


def _bot_member(guild: discord.Guild) -> discord.Member | None:
    try:
        if isinstance(guild.me, discord.Member):
            return guild.me
    except Exception:
        pass
    try:
        state = getattr(guild, "_state", None)
        user = getattr(state, "user", None)
        user_id = _safe_int(getattr(user, "id", 0), 0)
        member = guild.get_member(user_id) if user_id else None
        return member if isinstance(member, discord.Member) else None
    except Exception:
        return None


def _is_font_blocker(reason: Any) -> bool:
    text = str(reason or "").strip().lower()
    return (
        text.startswith("selected font")
        or "decode proof" in text
        or "did not visibly transform" in text
        or "font is unavailable" in text
    )


def _split_blockers(blocked: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    access: list[dict[str, Any]] = []
    font: list[dict[str, Any]] = []
    for row in blocked:
        if _is_font_blocker(row.get("blocked_reason")):
            font.append(row)
        else:
            access.append(row)
    return access, font


def _with_safe_font(options: dict[str, str]) -> dict[str, str]:
    new_options = dict(options or {})
    new_options["unicodeStyle"] = "bold_sans"
    new_options["unicode_style"] = "bold_sans"
    new_options["font"] = "bold_sans"
    return new_options


def _has_font_blockers_for_user(guild_id: int, user_id: int) -> bool:
    payload = _PENDING.get(_key(int(guild_id), int(user_id))) or {}
    return bool(payload.get("blocked_font"))


def _has_access_blockers_for_user(guild_id: int, user_id: int) -> bool:
    payload = _PENDING.get(_key(int(guild_id), int(user_id))) or {}
    return bool(payload.get("blocked_access"))


def _bot_access_reason(guild: discord.Guild, channel: Any) -> str | None:
    me = _bot_member(guild)
    if me is None:
        return "bot member is not resolved"
    try:
        perms = channel.permissions_for(me)
    except Exception:
        return "cannot calculate permissions"
    if not bool(getattr(perms, "view_channel", False)):
        return "bot cannot view this channel/category"
    if not bool(getattr(perms, "manage_channels", False)):
        return "bot lacks Manage Channels here"
    parent = getattr(channel, "category", None)
    if parent is not None:
        try:
            parent_perms = parent.permissions_for(me)
            if not bool(getattr(parent_perms, "view_channel", False)):
                return "bot cannot view parent category"
            if not bool(getattr(parent_perms, "manage_channels", False)):
                return "bot lacks Manage Channels on parent category"
        except Exception:
            pass
    return None


async def _build_plan_parts(guild: discord.Guild, options: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    from stoney_verify.services.channel_builder_runtime import format_channel_builder_name
    ctx = await _skip_context(int(guild.id))
    channels = list(getattr(guild, "categories", []) or []) + [c for c in list(getattr(guild, "channels", []) or []) if not isinstance(c, discord.CategoryChannel)]
    seen: set[int] = set()
    ready: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    policy_skipped = 0
    for channel in channels:
        cid = _safe_int(getattr(channel, "id", 0), 0)
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        if _kind(channel) == "other":
            continue
        if _skip(channel, ctx):
            policy_skipped += 1
            continue
        before = _safe_str(getattr(channel, "name", ""))
        after = _safe_str(format_channel_builder_name(before, {**options, "emoji": None}))[:100]
        if not after or after == before:
            continue
        row = {"channel_id": str(cid), "before": before, "after": after, "kind": _kind(channel)}
        reason = _bot_access_reason(guild, channel)
        if reason:
            row["blocked_reason"] = reason
            blocked.append(row)
            continue
        ready.append(row)
        if len(ready) >= MAX_PLAN_ITEMS:
            break
    return ready, blocked, policy_skipped


async def build_plan(guild: discord.Guild, options: dict[str, str]) -> list[dict[str, Any]]:
    ready, _blocked, _policy_skipped = await _build_plan_parts(guild, options)
    return ready


def _plan_text(plan: list[dict[str, Any]], limit: int = 12) -> str:
    if not plan:
        return "No rename changes found for the current font settings."
    rows = [f"**Old:** `{item.get('before')}`\n**New:** `{item.get('after')}`" for item in plan[:limit]]
    if len(plan) > limit:
        rows.append(f"…and {len(plan) - limit} more")
    return "\n\n".join(rows)[:3900]


def _blocked_text(blocked: list[dict[str, Any]], limit: int = 8) -> str:
    if not blocked:
        return "None"
    rows = [f"`{item.get('before')}` — {item.get('blocked_reason') or 'blocked'}" for item in blocked[:limit]]
    if len(blocked) > limit:
        rows.append(f"…and {len(blocked) - limit} more")
    return "\n".join(rows)[:1024]


async def _preview_embed(guild: discord.Guild, user_id: int, options: dict[str, str]) -> tuple[discord.Embed, list[dict[str, Any]]]:
    _purge()
    plan, blocked, policy_skipped = await _build_plan_parts(guild, options)
    access_blocked, font_blocked = _split_blockers(blocked)

    _PENDING[_key(int(guild.id), int(user_id))] = {
        "created_at": time.time(),
        "plan": plan,
        "options": dict(options),
        "blocked": blocked,
        "blocked_access": access_blocked,
        "blocked_font": font_blocked,
        "policy_skipped": policy_skipped,
    }

    embed = discord.Embed(
        title="🔤 Preview Channel Font Renames",
        description=(
            "Nothing has been changed yet. Apply only includes channels marked ready.\n\n"
            "Bot-access blockers can be repaired. Font blockers mean the selected font cannot safely transform those letters."
        ),
        color=discord.Color.orange() if blocked else (discord.Color.green() if plan else discord.Color.blurple()),
    )
    embed.add_field(name="Ready to rename", value=str(len(plan)), inline=True)
    embed.add_field(name="Blocked by bot access", value=str(len(access_blocked)), inline=True)
    embed.add_field(name="Blocked by selected font", value=str(len(font_blocked)), inline=True)
    embed.add_field(name="Protected by policy", value=str(policy_skipped), inline=True)
    embed.add_field(name="Batch size", value=str(DEFAULT_BATCH_SIZE), inline=True)
    embed.add_field(name="Delay between edits", value=f"{DEFAULT_DELAY_SECONDS:.1f}s", inline=True)

    if access_blocked:
        embed.add_field(name="Fix bot access before applying", value=_blocked_text(access_blocked), inline=False)
    if font_blocked:
        embed.add_field(
            name="Auto-fix available",
            value=(
                "The selected font cannot transform some letters. "
                "Use **Auto-Fix Unsupported Font** to switch this preview to a safer supported font."
            ),
            inline=False,
        )
        embed.add_field(name="Font blockers", value=_blocked_text(font_blocked), inline=False)

    embed.add_field(name="Ready preview", value=_plan_text(plan), inline=False)
    return embed, plan



def _remaining_plan(guild_id: int, user_id: int) -> list[dict[str, Any]]:
    _purge()
    return list((_PENDING.get(_key(guild_id, user_id)) or {}).get("plan") or [])


def _set_remaining_plan(guild_id: int, user_id: int, plan: list[dict[str, Any]]) -> None:
    key = _key(guild_id, user_id)
    payload = _PENDING.get(key) or {"created_at": time.time(), "options": {}}
    payload["plan"] = list(plan)
    payload["created_at"] = time.time()
    _PENDING[key] = payload


async def _apply_batch(interaction: discord.Interaction, plan: list[dict[str, Any]]) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None
    user_id = int(interaction.user.id)

    async def mutate_one(item: dict[str, Any]) -> dict[str, Any]:
        channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
        if channel is None:
            return {"status": "failed", "error": f"missing `{item.get('before')}`"}
        access_reason = _bot_access_reason(guild, channel)
        if access_reason:
            return {"status": "skipped", "error": f"blocked `{item.get('before')}`: {access_reason}"}
        current = _safe_str(getattr(channel, "name", ""))
        before = _safe_str(item.get("before"))
        after = _safe_str(item.get("after"))[:100]
        if not after:
            return {"status": "skipped", "error": f"empty target for `{before}`"}
        if current == after:
            return {"status": "already", "channel_id": str(getattr(channel, "id", "")), "before": before, "after": after}
        if current != before:
            return {"status": "skipped", "error": f"stale `{before}` now `{current}`"}
        try:
            await channel.edit(name=after, reason=f"Dank Shield channel font apply by {user_id}")
            return {"status": "changed", "channel_id": str(getattr(channel, "id", "")), "before": before, "after": after}
        except discord.Forbidden:
            return {"status": "skipped", "error": f"blocked `{before}`: Discord denied access"}

    result = await run_paced_channel_mutations(guild_id=int(guild.id), items=plan, mutate_one=mutate_one)
    remaining = list(plan[result.attempted:])
    _set_remaining_plan(int(guild.id), user_id, remaining)
    if result.changes:
        key = _key(int(guild.id), user_id)
        existing = list((_LAST_UNDO.get(key) or {}).get("undo") or [])
        _LAST_UNDO[key] = {"created_at": time.time(), "undo": existing + list(result.changes)}
    payload = result.to_dict()
    payload["remaining_plan"] = remaining
    return payload


async def _undo_batch(interaction: discord.Interaction, undo: list[dict[str, Any]]) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None
    user_id = int(interaction.user.id)
    reverse_plan = list(reversed(undo))

    async def mutate_one(item: dict[str, Any]) -> dict[str, Any]:
        channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
        if channel is None:
            return {"status": "failed", "error": f"missing `{item.get('after')}`"}
        access_reason = _bot_access_reason(guild, channel)
        if access_reason:
            return {"status": "skipped", "error": f"blocked `{item.get('after')}`: {access_reason}"}
        current = _safe_str(getattr(channel, "name", ""))
        applied = _safe_str(item.get("after"))
        old = _safe_str(item.get("before"))[:100]
        if current == old:
            return {"status": "already", "channel_id": str(getattr(channel, "id", "")), "before": applied, "after": old}
        if current != applied:
            return {"status": "skipped", "error": f"stale `{applied}` now `{current}`"}
        try:
            await channel.edit(name=old, reason=f"Dank Shield channel font undo by {user_id}")
            return {"status": "changed", "channel_id": str(getattr(channel, "id", "")), "before": applied, "after": old}
        except discord.Forbidden:
            return {"status": "skipped", "error": f"blocked `{applied}`: Discord denied access"}

    result = await run_paced_channel_mutations(guild_id=int(guild.id), items=reverse_plan, mutate_one=mutate_one)
    remaining_original_order = list(undo[: max(0, len(undo) - result.attempted)])
    key = _key(int(guild.id), user_id)
    if remaining_original_order:
        _LAST_UNDO[key] = {"created_at": time.time(), "undo": remaining_original_order}
    else:
        _LAST_UNDO.pop(key, None)
    payload = result.to_dict()
    payload["remaining_undo"] = remaining_original_order
    return payload


class QueuedFontRenamePreviewButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(label="Preview & Apply Channel Renames", emoji="👀", style=discord.ButtonStyle.success, custom_id="dank_setup_font:preview_renames", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_setup(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        from stoney_verify.startup_guards.setup_channel_font_mode_guard import load_channel_font_options
        options = await load_channel_font_options(int(interaction.guild.id))
        embed, plan = await _preview_embed(interaction.guild, int(interaction.user.id), options)
        pending = _PENDING.get(_key(int(interaction.guild.id), int(interaction.user.id))) or {}
        await interaction.response.edit_message(
            embed=embed,
            view=QueuedFontRenameConfirmView(
                enabled=bool(plan),
                can_fix_access=bool(pending.get("blocked_access")),
                can_fix_font=bool(pending.get("blocked_font")),
            ),
        )


class AutoFixUnsupportedFontButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Auto-Fix Unsupported Font",
            emoji="🧩",
            style=discord.ButtonStyle.primary,
            custom_id="dank_setup_font:auto_fix_unsupported_font",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_setup(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        pending = _PENDING.get(_key(int(guild.id), int(interaction.user.id))) or {}
        if not pending.get("blocked_font"):
            return await interaction.response.send_message(
                "No unsupported-font blockers found. Run a fresh preview first.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        fixed_options = _with_safe_font(dict(pending.get("options") or {}))
        embed, plan = await _preview_embed(guild, int(interaction.user.id), fixed_options)
        refreshed = _PENDING.get(_key(int(guild.id), int(interaction.user.id))) or {}
        embed.add_field(
            name="Auto-fix applied",
            value="Switched this preview to **Bold Sans**, a safer supported font.",
            inline=False,
        )
        await interaction.response.edit_message(
            embed=embed,
            view=QueuedFontRenameConfirmView(
                enabled=bool(plan),
                can_fix_access=bool(refreshed.get("blocked_access")),
                can_fix_font=bool(refreshed.get("blocked_font")),
            ),
        )


class QueuedFontRenameConfirmView(discord.ui.View):
    def __init__(self, *, enabled: bool, can_fix_access: bool = False, can_fix_font: bool = False) -> None:
        super().__init__(timeout=900)
        self.apply_preview.disabled = not enabled
        if can_fix_access:
            try:
                from stoney_verify.startup_guards.channel_font_access_repair_guard import FontAccessRepairButton
                self.add_item(FontAccessRepairButton())
            except Exception:
                pass
        if can_fix_font:
            self.add_item(AutoFixUnsupportedFontButton())

    @discord.ui.button(label="Apply Next Safe Batch", emoji="✅", style=discord.ButtonStyle.danger, custom_id="dank_setup_font:apply_preview", row=0)
    async def apply_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        plan = _remaining_plan(int(guild.id), int(interaction.user.id))
        if not plan:
            return await interaction.response.edit_message(content="No ready rename plan found. Fix blocked access, then press Preview & Apply Channel Renames again.", embed=None, view=None)
        await interaction.response.defer(ephemeral=True, thinking=False)
        from stoney_verify.operation_queue import run_interaction_exclusive
        result = await run_interaction_exclusive(
            interaction=interaction,
            operation_type="channel_font_rename_apply",
            action_label="Channel font rename apply",
            factory=lambda: _apply_batch(interaction, plan),
            fingerprint={"plan": plan[:DEFAULT_BATCH_SIZE]},
            risk_level="dangerous",
            concurrency_class="channel_mutation",
            concurrency_key="channel_font_rename",
            timeout_seconds=180.0,
        )
        if result is None:
            return
        remaining = list(result.get("remaining_plan") or [])
        embed = discord.Embed(
            title="✅ Channel Font Rename Batch Complete",
            description=(
                f"Attempted **{int(result.get('attempted', 0) or 0)}**. "
                f"Changed **{int(result.get('changed', 0) or 0)}**. "
                f"Already done **{int(result.get('already', 0) or 0)}**. "
                f"Skipped **{int(result.get('skipped', 0) or 0)}**. "
                f"Failed **{int(result.get('failed', 0) or 0)}**.\n\n"
                f"Remaining ready: **{len(remaining)}**"
            ),
            color=discord.Color.green() if not result.get("failures") else discord.Color.orange(),
        )
        failures = list(result.get("failures") or [])
        if failures:
            embed.add_field(name="Skipped / failed", value="\n".join(failures[:10])[:1024], inline=False)
        if remaining:
            embed.add_field(name="Continue", value="Press **Apply Next Safe Batch** to continue without bursting Discord's channel edit route.", inline=False)
        if result.get("changes"):
            embed.add_field(name="Undo available", value="Use **Undo Last Font Rename** to roll back changed batches.", inline=False)
        await interaction.edit_original_response(embed=embed, view=QueuedFontRenameDoneView(can_undo=bool(result.get("changes")), can_continue=bool(remaining)))

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
    def __init__(self, *, can_undo: bool = False, can_continue: bool = False) -> None:
        super().__init__(timeout=900)
        self.undo_last.disabled = not can_undo
        self.continue_apply.disabled = not can_continue

    @discord.ui.button(label="Apply Next Safe Batch", emoji="✅", style=discord.ButtonStyle.danger, custom_id="dank_setup_font:continue_apply", row=0)
    async def continue_apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        view = QueuedFontRenameConfirmView(enabled=True, can_fix_access=False, can_fix_font=False)
        await view.apply_preview.callback(interaction)  # type: ignore[attr-defined]

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
            factory=lambda: _undo_batch(interaction, undo),
            fingerprint={"undo": undo[:DEFAULT_BATCH_SIZE]},
            risk_level="dangerous",
            concurrency_class="channel_mutation",
            concurrency_key="channel_font_rename",
            timeout_seconds=180.0,
        )
        if result is None:
            return
        remaining_undo = list(result.get("remaining_undo") or [])
        embed = discord.Embed(
            title="↩️ Channel Font Rename Undo Batch Complete",
            description=(
                f"Attempted **{int(result.get('attempted', 0) or 0)}**. "
                f"Restored **{int(result.get('changed', 0) or 0)}**. "
                f"Already done **{int(result.get('already', 0) or 0)}**. "
                f"Skipped **{int(result.get('skipped', 0) or 0)}**. "
                f"Failed **{int(result.get('failed', 0) or 0)}**.\n\n"
                f"Remaining undo: **{len(remaining_undo)}**"
            ),
            color=discord.Color.green() if not result.get("failures") else discord.Color.orange(),
        )
        failures = list(result.get("failures") or [])
        if failures:
            embed.add_field(name="Skipped / failed", value="\n".join(failures[:10])[:1024], inline=False)
        await interaction.edit_original_response(embed=embed, view=QueuedFontRenameDoneView(can_undo=bool(remaining_undo), can_continue=bool(_remaining_plan(int(guild.id), int(interaction.user.id)))))

    @discord.ui.button(label="Back to Font Settings", emoji="🔤", style=discord.ButtonStyle.secondary, custom_id="dank_setup_font:done_back", row=1)
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
        print("🔤 channel_font_rename_queue_guard active; font renames preflight bot access and use paced channel mutation throttle")
    except Exception:
        pass
    return True


apply()

__all__ = ["apply", "QueuedFontRenamePreviewButton", "build_plan"]
