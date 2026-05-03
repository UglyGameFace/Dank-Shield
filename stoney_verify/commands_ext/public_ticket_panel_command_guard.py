from __future__ import annotations

"""Public ticket panel command owner.

This module intentionally owns the public panel commands used by server admins:

- /ticket-panel post
- /ticket-intake post-panel

Both commands post the same menu-first Create Ticket panel. The old modal-first
panel is not used here.
"""

import asyncio
import builtins
import inspect
import sys
from typing import Any, Optional

import discord
from discord import app_commands

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")
_PATCHED_REGISTER = False
_PERSISTENT_VIEW_REGISTERED = False
TICKET_PANEL_GROUP_NAME = "ticket-panel"
TICKET_PANEL_POST_NAME = "post"
TICKET_INTAKE_POST_PANEL_NAME = "post-panel"


def _log(message: str) -> None:
    try:
        print(f"🎫 public_ticket_panel_command_guard {message}")
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
    return _safe_text(row.get("button_label") or row.get("name") or row.get("display_name") or row.get("category_name") or _row_slug(row), "Support")[:100]


def _row_desc(row: dict[str, Any]) -> str:
    return _safe_text(row.get("description") or row.get("intake_type") or row.get("type") or "Open a support ticket", "Open a support ticket")[:100]


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


async def _reply_once(
    interaction: discord.Interaction,
    content: str = "",
    *,
    ephemeral: bool = True,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        kwargs: dict[str, Any] = {"ephemeral": ephemeral, "allowed_mentions": discord.AllowedMentions.none()}
        if embed is not None:
            kwargs["embed"] = embed
            if content:
                kwargs["content"] = content
        else:
            kwargs["content"] = content
        if view is not None:
            kwargs["view"] = view
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except Exception:
        pass


async def _defer(interaction: discord.Interaction, *, thinking: bool = False) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=thinking)
    except Exception:
        pass


def _staff_check(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.commands_ext.common import _staff_check as common_staff_check
        return bool(common_staff_check(interaction))
    except Exception:
        member = interaction.user
        return bool(
            isinstance(member, discord.Member)
            and (
                member.guild_permissions.administrator
                or member.guild_permissions.manage_guild
                or member.guild_permissions.manage_channels
            )
        )


async def _configured_ticket_panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.guild_config import get_guild_config
        cfg = await get_guild_config(guild.id, refresh=True)
        for attr in ("ticket_panel_channel_id", "support_channel_id", "verify_channel_id"):
            cid = _safe_int(getattr(cfg, attr, 0), 0)
            if cid <= 0:
                continue
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                return ch
    except Exception:
        pass
    return None


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
        value="1. Press **Create Ticket**\n2. Pick a ticket type from the menu\n3. A private ticket channel opens",
        inline=False,
    )
    embed.set_footer(text=f"{guild.name} • Stoney Verify ticket panel • menu-first")
    return embed


async def _maybe_async(func: Any, *args: Any, **kwargs: Any) -> Any:
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


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
        _log(f"ticket menu row fallback used: {e!r}")
    return [{"name": "Support", "slug": "support", "description": "General support request.", "sort_order": 999, "is_default": True}]


async def _existing_open_ticket(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.tickets_new.service import find_open_ticket_for_owner
        row = await find_open_ticket_for_owner(guild_id=int(guild.id), owner_id=int(member.id), category=None)
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
            if isinstance(result.get(key), discord.TextChannel):
                return result[key]
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


async def _create_ticket_from_choice(interaction: discord.Interaction, row: dict[str, Any]) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply_once(interaction, "❌ This must be used inside a server.")

    await _defer(interaction, thinking=True)

    existing = await _existing_open_ticket(guild, member)
    if existing is not None:
        return await _reply_once(interaction, f"You already have an open ticket: {existing.mention}")

    try:
        from stoney_verify.tickets_new.service import create_ticket_channel
    except Exception as e:
        return await _reply_once(interaction, f"❌ Ticket creator is unavailable: `{type(e).__name__}`")

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
    values = {
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
        "category_id": _safe_text(row.get("id")) or None,
        "category_override": True,
    }

    try:
        signature = inspect.signature(create_ticket_channel)
        kwargs = {k: v for k, v in values.items() if k in signature.parameters and v is not None}
        result = await _maybe_async(create_ticket_channel, **kwargs) if kwargs else await _maybe_async(create_ticket_channel, guild, member, slug, reason)
    except TypeError:
        attempts = (
            ((), {"guild": guild, "owner": member, "category": slug, "reason": reason}),
            ((), {"guild": guild, "member": member, "category": slug, "reason": reason}),
            ((guild, member, slug, reason), {}),
            ((member, slug, reason), {}),
        )
        last_error: Optional[Exception] = None
        result = None
        for args, kwargs in attempts:
            try:
                result = await _maybe_async(create_ticket_channel, *args, **kwargs)
                last_error = None
                break
            except Exception as e:
                last_error = e
        if last_error is not None:
            return await _reply_once(interaction, f"❌ Ticket creation failed: `{type(last_error).__name__}: {_truncate(last_error, 240)}`")
    except Exception as e:
        return await _reply_once(interaction, f"❌ Ticket creation failed: `{type(e).__name__}: {_truncate(e, 240)}`")

    channel = _extract_channel(result, guild)
    if channel is not None:
        return await _reply_once(interaction, f"✅ Ticket created: {channel.mention}")
    return await _reply_once(interaction, "✅ Ticket created. If you do not see it, ask staff to check ticket category permissions.")


async def _maybe_route_unverified(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.startup_guards.unverified_ticket_panel_flow import _handle_unverified_panel_click
        return bool(await _handle_unverified_panel_click(interaction))
    except Exception:
        return False


class PublicTicketTypeSelect(discord.ui.Select):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        options = [
            discord.SelectOption(label=_row_name(row), value=_row_slug(row), description=_row_desc(row), emoji="🎫")
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

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green, emoji="🎫", custom_id="sv:ticket:public:create_menu:v2")
    async def create_ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if await _maybe_route_unverified(interaction):
            return
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _reply_once(interaction, "❌ This must be used inside a server.")
        existing = await _existing_open_ticket(guild, member)
        if existing is not None:
            return await _reply_once(interaction, f"You already have an open ticket: {existing.mention}")
        rows = await _load_ticket_menu_rows(guild)
        embed = discord.Embed(title="Create Ticket", description="Choose the type of ticket you want to open.", color=discord.Color.blurple())
        embed.set_footer(text="Pick a category. No form needed.")
        await _reply_once(interaction, "Choose a ticket type.", embed=embed, view=PublicTicketTypeView(rows))


def _register_persistent_public_panel_view() -> None:
    global _PERSISTENT_VIEW_REGISTERED
    if _PERSISTENT_VIEW_REGISTERED:
        return
    try:
        from stoney_verify.globals import bot
        bot.add_view(PublicTicketPanelButtonView())
        _PERSISTENT_VIEW_REGISTERED = True
        _log("registered persistent menu-first Create Ticket view v2")
    except Exception as e:
        _log(f"persistent view registration skipped: {e!r}")


async def _post_ticket_panel_command(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    if not _staff_check(interaction):
        return await _reply_once(interaction, "❌ Staff only.")
    await _defer(interaction)
    guild = interaction.guild
    if guild is None:
        return await _reply_once(interaction, "❌ This command must be used inside a server.")
    target = channel or await _configured_ticket_panel_channel(guild)
    if target is None and isinstance(interaction.channel, discord.TextChannel):
        target = interaction.channel
    if target is None:
        return await _reply_once(interaction, "❌ I could not find a text channel to post the panel.")

    me = guild.me
    if me is not None:
        perms = target.permissions_for(me)
        missing = [label for label, ok in (("View Channel", perms.view_channel), ("Send Messages", perms.send_messages), ("Embed Links", perms.embed_links)) if not ok]
        if missing:
            return await _reply_once(interaction, f"❌ I cannot post in {target.mention}. Missing: {', '.join(missing)}.")

    try:
        msg = await target.send(embed=_panel_embed(guild), view=PublicTicketPanelButtonView(), allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        return await _reply_once(interaction, f"❌ Failed posting panel in {target.mention}: `{type(e).__name__}: {_truncate(e, 180)}`")

    try:
        from stoney_verify.commands_ext.public_setup_config_writer import upsert_guild_config
        from stoney_verify.guild_config import invalidate_guild_config
        await upsert_guild_config(guild.id, {"ticket_panel_channel_id": str(int(target.id)), "ticket_panel_message_id": str(int(msg.id))})
        invalidate_guild_config(guild.id)
    except Exception:
        pass

    await _reply_once(interaction, f"✅ Posted the **menu-first Create Ticket** panel in {target.mention}.")


ticket_panel_group = app_commands.Group(name=TICKET_PANEL_GROUP_NAME, description="Ticket panel commands.")


@ticket_panel_group.command(name=TICKET_PANEL_POST_NAME, description="Post the public menu-first Create Ticket panel.")
@app_commands.describe(channel="Optional channel. Defaults to configured support/ticket-panel channel, then current channel.")
async def ticket_panel_post(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    await _post_ticket_panel_command(interaction, channel)


def _build_intake_post_panel_command() -> app_commands.Command:
    command = app_commands.Command(
        name=TICKET_INTAKE_POST_PANEL_NAME,
        description="Post the public menu-first Create Ticket panel.",
        callback=_post_ticket_panel_command,
    )
    try:
        command._params["channel"].description = "Optional channel. Defaults to configured support/ticket-panel channel."
    except Exception:
        pass
    return command


def _remove_group_child(group: Any, name: str) -> None:
    try:
        if group.get_command(name) is None:
            return
    except Exception:
        pass
    for remover_name in ("remove_command", "_remove_command"):
        remover = getattr(group, remover_name, None)
        if callable(remover):
            try:
                remover(name)
                return
            except Exception:
                pass
    try:
        children = getattr(group, "_children", None)
        if isinstance(children, dict):
            children.pop(name, None)
    except Exception:
        pass


def _replace_intake_post_panel(module: Any) -> None:
    group = getattr(module, "ticket_intake_group", None)
    if group is None:
        return
    _remove_group_child(group, TICKET_INTAKE_POST_PANEL_NAME)
    try:
        group.add_command(_build_intake_post_panel_command())
        _log("registered /ticket-intake post-panel as menu-first")
    except Exception as e:
        _log(f"failed registering /ticket-intake post-panel: {e!r}")


def _replace_tree_ticket_panel_group(tree: Any) -> None:
    try:
        if tree.get_command(TICKET_PANEL_GROUP_NAME, guild=None) is not None:
            tree.remove_command(TICKET_PANEL_GROUP_NAME, guild=None)
            _log("removed old /ticket-panel command/group before registering menu-first group")
    except Exception:
        pass
    try:
        tree.add_command(ticket_panel_group)
        _log("registered /ticket-panel post as menu-first")
    except Exception as e:
        _log(f"failed registering /ticket-panel post: {e!r}")


def _patch_register_function(module: Any) -> None:
    global _PATCHED_REGISTER
    original = getattr(module, "register_public_ticket_intake_group_commands", None)
    if not callable(original):
        return
    if getattr(original, "_menu_first_panel_wrapped", False):
        _PATCHED_REGISTER = True
        return

    def patched_register(bot: Any, tree: Any) -> None:
        _register_persistent_public_panel_view()
        _replace_intake_post_panel(module)
        try:
            original(bot, tree)
        finally:
            _replace_intake_post_panel(module)
            _replace_tree_ticket_panel_group(tree)

    try:
        setattr(patched_register, "_menu_first_panel_wrapped", True)
    except Exception:
        pass
    setattr(module, "register_public_ticket_intake_group_commands", patched_register)
    _PATCHED_REGISTER = True
    _log("patched ticket intake registration; /ticket-panel post is now menu-first")


def _patch_loaded() -> None:
    module = sys.modules.get("stoney_verify.commands_ext.public_ticket_intake_group")
    if module is not None:
        _patch_register_function(module)
        _replace_intake_post_panel(module)


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.commands_ext.public_ticket_intake_group" or name.endswith("commands_ext.public_ticket_intake_group"):
            target = sys.modules.get("stoney_verify.commands_ext.public_ticket_intake_group") or sys.modules.get(name)
            if target is not None:
                _patch_register_function(target)
                _replace_intake_post_panel(target)
        else:
            _patch_loaded()
    except Exception:
        pass
    return module


builtins.__import__ = _safe_import
_register_persistent_public_panel_view()
_patch_loaded()
_log("loaded; /ticket-panel post and /ticket-intake post-panel are menu-first")


__all__ = ["PublicTicketPanelButtonView", "ticket_panel_group", "ticket_panel_post"]
