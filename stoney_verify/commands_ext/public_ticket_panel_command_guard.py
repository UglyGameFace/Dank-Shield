from __future__ import annotations

"""Menu-first public ticket panel.

This module is intentionally boring:

- owns /ticket-panel post
- exposes post_public_ticket_panel() so /ticket-intake post-panel can reuse it
- registers one persistent Create Ticket button view
- shows a category select first instead of opening the old reason modal
- creates tickets through the normal ticket service and repairs category placement
  only as a final safety net

No import hooks. No monkey-patching another module. No guessed legacy button ID
list. Old panel messages should be deleted and reposted with /ticket-panel post.
"""

import asyncio
import inspect
from typing import Any, Optional

import discord
from discord import app_commands

from .common import _staff_check, reply_once

TICKET_PANEL_GROUP_NAME = "ticket-panel"
TICKET_PANEL_POST_NAME = "post"
MENU_FIRST_CUSTOM_ID = "sv:ticket:public:create_menu:v4"

_PERSISTENT_VIEW_REGISTERED = False


def _log(message: str) -> None:
    try:
        print(f"🎫 public_ticket_panel {message}")
    except Exception:
        pass


def _safe_text(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
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


def _truncate(value: Any, limit: int = 300) -> str:
    text = _safe_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _slugify(value: Any) -> str:
    text = _safe_text(value, "support").lower()
    out: list[str] = []
    dash = False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash:
            out.append("-")
            dash = True
    return ("".join(out).strip("-") or "support")[:80]


def _row_slug(row: dict[str, Any]) -> str:
    return _slugify(row.get("slug") or row.get("category_slug") or row.get("name") or "support")


def _row_name(row: dict[str, Any]) -> str:
    return _safe_text(
        row.get("button_label")
        or row.get("name")
        or row.get("display_name")
        or row.get("category_name")
        or _row_slug(row),
        "Support",
    )[:100]


def _row_desc(row: dict[str, Any]) -> str:
    return _safe_text(
        row.get("description") or row.get("intake_type") or row.get("type") or "Open a support ticket",
        "Open a support ticket",
    )[:100]


def _row_sort(row: dict[str, Any]) -> int:
    return _safe_int(row.get("sort_order", row.get("position", 999)), 999)


def _normalize_rows(raw_rows: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for item in raw_rows or []:
            if isinstance(item, dict):
                rows.append(dict(item))
    except Exception:
        pass

    rows.sort(key=lambda r: (_row_sort(r), _row_name(r).lower(), _row_slug(r)))

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        slug = _row_slug(row)
        if slug in seen:
            continue
        seen.add(slug)
        out.append(row)
    return out[:25]


async def _send_ephemeral(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
    except Exception:
        pass


async def _defer_ephemeral(interaction: discord.Interaction, *, thinking: bool = False) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=thinking)
    except Exception:
        pass


async def _guild_config(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        return await get_guild_config(guild.id, refresh=True)
    except Exception:
        return None


async def _configured_ticket_panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    cfg = await _guild_config(guild)
    for attr in (
        "ticket_panel_channel_id",
        "support_channel_id",
        "verify_channel_id",
        "status_channel_id",
    ):
        cid = _safe_int(getattr(cfg, attr, 0), 0)
        if cid <= 0:
            continue
        ch = guild.get_channel(cid)
        if isinstance(ch, discord.TextChannel):
            return ch
    return None


async def _configured_active_ticket_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    cfg = await _guild_config(guild)
    for attr in (
        "ticket_category_id",
        "active_ticket_category_id",
        "ticket_active_category_id",
        "ticket_parent_category_id",
        "ticket_open_category_id",
        "open_ticket_category_id",
    ):
        cid = _safe_int(getattr(cfg, attr, 0), 0)
        if cid <= 0:
            continue
        ch = guild.get_channel(cid)
        if isinstance(ch, discord.CategoryChannel):
            return ch

    try:
        for category in guild.categories:
            name = _safe_text(category.name).lower()
            if "active" in name and "ticket" in name:
                return category
    except Exception:
        pass

    return None


async def _ensure_channel_in_active_category(
    channel: discord.TextChannel,
    target_category: Optional[discord.CategoryChannel],
) -> None:
    if target_category is None:
        return
    try:
        current_id = int(getattr(getattr(channel, "category", None), "id", 0) or 0)
        if current_id == int(target_category.id):
            return
        await channel.edit(
            category=target_category,
            sync_permissions=False,
            reason="Stoney Verify ticket creation category repair",
        )
        _log(f"repaired ticket channel={channel.id} into active category={target_category.id}")
    except Exception as e:
        _log(f"category repair failed channel={getattr(channel, 'id', '?')} error={e!r}")


def _panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🎫 Need help? Open a ticket",
        description=(
            "Press **Create Ticket** below, then choose the ticket type.\n\n"
            "A private ticket channel will open for you and staff."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="How it works",
        value="1. Press **Create Ticket**\n2. Pick a ticket type\n3. A private ticket channel opens",
        inline=False,
    )
    embed.set_footer(text=f"{guild.name} • Stoney Verify ticket panel • menu-first")
    return embed


async def _load_ticket_menu_rows(guild: discord.Guild) -> list[dict[str, Any]]:
    try:
        from stoney_verify.tickets_new import panel

        for helper_name in ("_fetch_dashboard_ticket_categories_sync", "_seed_dashboard_ticket_categories_sync"):
            helper = getattr(panel, helper_name, None)
            if callable(helper):
                rows = await asyncio.to_thread(helper, int(guild.id))
                normalized = _normalize_rows(rows)
                if normalized:
                    return normalized

        defaults = _normalize_rows(getattr(panel, "_DEFAULT_BOOTSTRAP_CATEGORIES", None))
        if defaults:
            return defaults
    except Exception as e:
        _log(f"ticket menu fallback used: {e!r}")

    return [
        {
            "name": "Support",
            "slug": "support",
            "description": "General support request.",
            "sort_order": 999,
            "is_default": True,
        }
    ]


async def _existing_open_ticket(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.tickets_new.service import find_open_ticket_for_owner

        row = await find_open_ticket_for_owner(
            guild_id=int(guild.id),
            owner_id=int(member.id),
            category=None,
        )
        if not isinstance(row, dict):
            return None
        channel_id = _safe_int(row.get("discord_thread_id") or row.get("channel_id"), 0)
        if channel_id <= 0:
            return None
        ch = guild.get_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            return ch
        fetched = await guild.fetch_channel(channel_id)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


def _extract_channel(result: Any, guild: discord.Guild) -> Optional[discord.TextChannel]:
    if isinstance(result, discord.TextChannel):
        return result
    if isinstance(result, dict):
        for key in ("channel", "ticket_channel"):
            value = result.get(key)
            if isinstance(value, discord.TextChannel):
                return value
        channel_id = _safe_int(result.get("discord_thread_id") or result.get("channel_id"), 0)
        if channel_id > 0:
            ch = guild.get_channel(channel_id)
            if isinstance(ch, discord.TextChannel):
                return ch
    if isinstance(result, (tuple, list)):
        for item in result:
            ch = _extract_channel(item, guild)
            if ch is not None:
                return ch
    return None


def _has_var_keyword(signature: inspect.Signature) -> bool:
    try:
        return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
    except Exception:
        return False


async def _maybe_async(func: Any, *args: Any, **kwargs: Any) -> Any:
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _create_ticket_from_choice(interaction: discord.Interaction, row: dict[str, Any]) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

    await _defer_ephemeral(interaction, thinking=True)

    existing = await _existing_open_ticket(guild, member)
    if existing is not None:
        return await _send_ephemeral(interaction, f"You already have an open ticket: {existing.mention}")

    active_category = await _configured_active_ticket_category(guild)

    try:
        from stoney_verify.tickets_new.service import create_ticket_channel
    except Exception as e:
        return await _send_ephemeral(interaction, f"❌ Ticket creator unavailable: `{type(e).__name__}`")

    slug = _row_slug(row)
    name = _row_name(row)
    reason = f"{name} ticket opened from ticket menu."
    metadata = {
        "source": "public_ticket_menu",
        "selected_category_slug": slug,
        "selected_category_name": name,
        "selected_intake_type": _safe_text(row.get("intake_type") or row.get("type") or "custom"),
        "ticket_category_row_id": _safe_text(row.get("id")),
    }

    values: dict[str, Any] = {
        "interaction": interaction,
        "guild": guild,
        "member": member,
        "owner": member,
        "user": member,
        "requester": member,
        "created_by": member,
        "category": slug,
        "category_slug": slug,
        "category_name": name,
        "title": name,
        "reason": reason,
        "message": reason,
        "description": reason,
        "priority": "medium",
        "is_ghost": False,
        "ghost": False,
        "metadata": metadata,
        "meta": metadata,
        "ticket_category_row_id": _safe_text(row.get("id")) or None,
        "matched_category_id": _safe_text(row.get("id")) or None,
        "matched_category_name": name,
        "matched_category_slug": slug,
        "matched_intake_type": _safe_text(row.get("intake_type") or row.get("type") or "custom"),
        "category_override": True,
    }

    if active_category is not None:
        values.update(
            {
                "parent_category": active_category,
                "category_channel": active_category,
                "ticket_category": active_category,
                "ticket_parent": active_category,
                "explicit_parent_category": active_category,
                "parent_category_id": int(active_category.id),
                "ticket_category_id": int(active_category.id),
                "ticket_parent_category_id": int(active_category.id),
                "active_ticket_category_id": int(active_category.id),
                "active_category_id": int(active_category.id),
                "explicit_parent_category_id": int(active_category.id),
                "category_channel_id": int(active_category.id),
            }
        )

    try:
        signature = inspect.signature(create_ticket_channel)
        accepts_kwargs = _has_var_keyword(signature)
        kwargs = {
            key: value
            for key, value in values.items()
            if value is not None and (accepts_kwargs or key in signature.parameters)
        }
        if not kwargs:
            raise TypeError("create_ticket_channel exposes no supported keyword parameters")
        result = await _maybe_async(create_ticket_channel, **kwargs)
    except TypeError as e:
        return await _send_ephemeral(
            interaction,
            f"❌ Ticket creation route is incompatible with this build: `{_truncate(e, 240)}`",
        )
    except Exception as e:
        return await _send_ephemeral(
            interaction,
            f"❌ Ticket creation failed: `{type(e).__name__}: {_truncate(e, 240)}`",
        )

    channel = _extract_channel(result, guild)
    if channel is not None:
        await _ensure_channel_in_active_category(channel, active_category)
        return await _send_ephemeral(interaction, f"✅ Ticket created: {channel.mention}")

    return await _send_ephemeral(
        interaction,
        "✅ Ticket created. If you do not see it, ask staff to check ticket category permissions.",
    )


async def _maybe_route_unverified(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.startup_guards.unverified_ticket_panel_flow import _handle_unverified_panel_click

        return bool(await _handle_unverified_panel_click(interaction))
    except Exception:
        return False


async def show_ticket_type_menu(interaction: discord.Interaction) -> None:
    if await _maybe_route_unverified(interaction):
        return

    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

    existing = await _existing_open_ticket(guild, member)
    if existing is not None:
        return await _send_ephemeral(interaction, f"You already have an open ticket: {existing.mention}")

    rows = await _load_ticket_menu_rows(guild)
    embed = discord.Embed(
        title="Create Ticket",
        description="Choose the type of ticket you want to open.",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Pick a category. No form needed.")
    await _send_ephemeral(interaction, "Choose a ticket type.", embed=embed, view=PublicTicketTypeView(rows))


class PublicTicketTypeSelect(discord.ui.Select):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        options = [
            discord.SelectOption(
                label=_row_name(row),
                value=_row_slug(row),
                description=_row_desc(row),
                emoji="🎫",
            )
            for row in rows[:25]
        ] or [discord.SelectOption(label="Support", value="support", description="General support request", emoji="🎫")]
        super().__init__(placeholder="Choose a ticket type", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = _safe_text(self.values[0], "support")
        row = next((item for item in self.rows if _row_slug(item) == selected), {"name": "Support", "slug": selected})
        await _create_ticket_from_choice(interaction, row)


class PublicTicketTypeView(discord.ui.View):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.add_item(PublicTicketTypeSelect(rows))


class PublicTicketPanelButtonView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.green,
        emoji="🎫",
        custom_id=MENU_FIRST_CUSTOM_ID,
    )
    async def create_ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await show_ticket_type_menu(interaction)


async def post_public_ticket_panel(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    if not _staff_check(interaction):
        return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

    await _defer_ephemeral(interaction)

    guild = interaction.guild
    if guild is None:
        return await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})

    target = channel or await _configured_ticket_panel_channel(guild)
    if target is None and isinstance(interaction.channel, discord.TextChannel):
        target = interaction.channel
    if target is None:
        return await reply_once(interaction, {"content": "❌ I could not find a text channel to post the panel.", "ephemeral": True})

    me = guild.me
    if me is not None:
        perms = target.permissions_for(me)
        missing = [
            label
            for label, ok in (
                ("View Channel", perms.view_channel),
                ("Send Messages", perms.send_messages),
                ("Embed Links", perms.embed_links),
            )
            if not ok
        ]
        if missing:
            return await reply_once(
                interaction,
                {"content": f"❌ I cannot post in {target.mention}. Missing: {', '.join(missing)}.", "ephemeral": True},
            )

    try:
        msg = await target.send(
            embed=_panel_embed(guild),
            view=PublicTicketPanelButtonView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as e:
        return await reply_once(
            interaction,
            {"content": f"❌ Failed posting panel in {target.mention}: `{type(e).__name__}: {_truncate(e, 180)}`", "ephemeral": True},
        )

    try:
        from stoney_verify.commands_ext.public_setup_config_writer import upsert_guild_config
        from stoney_verify.guild_config import invalidate_guild_config

        await upsert_guild_config(
            guild.id,
            {
                "ticket_panel_channel_id": str(int(target.id)),
                "ticket_panel_message_id": str(int(msg.id)),
            },
        )
        invalidate_guild_config(guild.id)
    except Exception:
        pass

    await reply_once(
        interaction,
        {"content": f"✅ Posted the **menu-first Create Ticket** panel in {target.mention}.", "ephemeral": True},
    )


ticket_panel_group = app_commands.Group(name=TICKET_PANEL_GROUP_NAME, description="Ticket panel commands.")


@ticket_panel_group.command(name=TICKET_PANEL_POST_NAME, description="Post the public menu-first Create Ticket panel.")
@app_commands.describe(channel="Optional channel. Defaults to configured panel/support channel, then current channel.")
async def ticket_panel_post(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    await post_public_ticket_panel(interaction, channel)


def _register_persistent_view(bot: Any) -> None:
    global _PERSISTENT_VIEW_REGISTERED
    if _PERSISTENT_VIEW_REGISTERED:
        return
    try:
        bot.add_view(PublicTicketPanelButtonView())
        _PERSISTENT_VIEW_REGISTERED = True
        _log("registered persistent menu-first Create Ticket view v4")
    except Exception as e:
        _log(f"persistent view registration skipped: {e!r}")


def register_public_ticket_panel_command_guard_commands(bot: Any, tree: Any) -> None:
    _register_persistent_view(bot)

    try:
        existing = tree.get_command(TICKET_PANEL_GROUP_NAME, guild=None)
        if existing is not None:
            tree.remove_command(TICKET_PANEL_GROUP_NAME, guild=None)
            _log("removed existing /ticket-panel command/group before clean registration")
    except Exception:
        pass

    tree.add_command(ticket_panel_group)
    _log("registered /ticket-panel post menu-first command")


__all__ = [
    "MENU_FIRST_CUSTOM_ID",
    "PublicTicketPanelButtonView",
    "post_public_ticket_panel",
    "show_ticket_type_menu",
    "register_public_ticket_panel_command_guard_commands",
    "ticket_panel_group",
]
