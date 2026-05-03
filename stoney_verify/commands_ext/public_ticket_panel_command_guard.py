from __future__ import annotations

"""
Public ticket panel command guard.

Owns the public, user-facing Create Ticket panel for the public command set:

- /ticket-panel
- /ticket-intake post-panel

This file intentionally posts a menu-first panel. Users press Create Ticket,
choose a ticket type, and a ticket opens. It does not post the old modal-first
panel that immediately asks users to describe their issue.
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
_GROUP_PATCHED = False
_REGISTER_PATCHED = False
_PERSISTENT_VIEW_REGISTERED = False
_TOP_LEVEL_COMMAND_NAME = "ticket-panel"


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
    last_dash = False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return ("".join(out).strip("-") or "support")[:80]


def _row_slug(row: dict[str, Any]) -> str:
    return _slugify(row.get("slug") or row.get("category_slug") or row.get("name") or row.get("display_name") or "support")


def _row_name(row: dict[str, Any]) -> str:
    return _safe_text(row.get("button_label") or row.get("name") or row.get("display_name") or row.get("category_name") or _row_slug(row), "Support")[:100]


def _row_description(row: dict[str, Any]) -> str:
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
    content: str,
    *,
    ephemeral: bool = True,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        kwargs: dict[str, Any] = {
            "ephemeral": ephemeral,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if embed is not None:
            kwargs["embed"] = embed
        else:
            kwargs["content"] = content
        if view is not None:
            kwargs["view"] = view

        if not interaction.response.is_done():
            await interaction.response.send_message(**kwargs)
        else:
            await interaction.followup.send(**kwargs)
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
        try:
            member = interaction.user
            return bool(
                isinstance(member, discord.Member)
                and (
                    member.guild_permissions.administrator
                    or member.guild_permissions.manage_guild
                    or member.guild_permissions.manage_channels
                )
            )
        except Exception:
            return False


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
        value=(
            "1. Press **Create Ticket**\n"
            "2. Pick a ticket type from the menu\n"
            "3. A private ticket channel opens"
        ),
        inline=False,
    )
    embed.set_footer(text=f"{guild.name} • Stoney Verify ticket panel")
    return embed


async def _maybe_async(func: Any, *args: Any, **kwargs: Any) -> Any:
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _load_ticket_menu_rows(guild: discord.Guild) -> list[dict[str, Any]]:
    try:
        from stoney_verify.tickets_new import panel

        fetcher = getattr(panel, "_fetch_dashboard_ticket_categories_sync", None)
        if callable(fetcher):
            rows = await asyncio.to_thread(fetcher, int(guild.id))
            normalized = _normalize_rows(rows)
            if normalized:
                return normalized

        seeder = getattr(panel, "_seed_dashboard_ticket_categories_sync", None)
        if callable(seeder):
            rows = await asyncio.to_thread(seeder, int(guild.id))
            normalized = _normalize_rows(rows)
            if normalized:
                return normalized

        defaults = getattr(panel, "_DEFAULT_BOOTSTRAP_CATEGORIES", None)
        normalized = _normalize_rows(defaults)
        if normalized:
            return normalized
    except Exception as e:
        _log(f"ticket menu row load fallback used: {e!r}")

    return [
        {
            "name": "Support",
            "slug": "support",
            "description": "General support request.",
            "intake_type": "support",
            "sort_order": 999,
            "is_default": True,
        }
    ]


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


def _extract_channel_from_create_result(result: Any, guild: discord.Guild) -> Optional[discord.TextChannel]:
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
            ch = _extract_channel_from_create_result(item, guild)
            if ch is not None:
                return ch
    return None


async def _create_ticket_from_menu_choice(interaction: discord.Interaction, row: dict[str, Any]) -> None:
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

    common_values = {
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
        kwargs = {
            key: value
            for key, value in common_values.items()
            if key in signature.parameters and value is not None
        }
        if kwargs:
            result = await _maybe_async(create_ticket_channel, **kwargs)
        else:
            result = await _maybe_async(create_ticket_channel, guild, member, slug, reason)
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

    channel = _extract_channel_from_create_result(result, guild)
    if channel is not None:
        return await _reply_once(interaction, f"✅ Ticket created: {channel.mention}")
    return await _reply_once(interaction, "✅ Ticket created. If you do not see it, ask staff to check the ticket category permissions.")


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
            discord.SelectOption(
                label=_row_name(row),
                value=_row_slug(row),
                description=_row_description(row),
                emoji="🎫",
            )
            for row in rows[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Support", value="support", description="General support request", emoji="🎫")]
        super().__init__(placeholder="Choose a ticket type", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = _safe_text(self.values[0], "support")
        row = next((item for item in self.rows if _row_slug(item) == selected), {"name": "Support", "slug": selected})
        await _create_ticket_from_menu_choice(interaction, row)


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
        custom_id="sv:ticket:public:create_menu:v1",
    )
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
        embed = discord.Embed(
            title="Create Ticket",
            description="Choose the type of ticket you want to open.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Pick a category. No form needed.")
        return await _reply_once(interaction, "Choose a ticket type.", embed=embed, view=PublicTicketTypeView(rows))


def _register_persistent_public_panel_view() -> None:
    global _PERSISTENT_VIEW_REGISTERED
    if _PERSISTENT_VIEW_REGISTERED:
        return
    try:
        from stoney_verify.globals import bot

        bot.add_view(PublicTicketPanelButtonView())
        _PERSISTENT_VIEW_REGISTERED = True
        _log("registered persistent menu-first public Create Ticket view")
    except Exception as e:
        _log(f"persistent menu-first public Create Ticket view registration skipped: {e!r}")


async def _post_ticket_panel_command(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    if not _staff_check(interaction):
        return await _reply_once(interaction, "❌ Staff only.")

    await _defer(interaction)

    guild = interaction.guild
    if guild is None:
        return await _reply_once(interaction, "❌ This command must be used inside a server.")

    target = channel
    if target is None:
        target = await _configured_ticket_panel_channel(guild)
    if target is None and isinstance(interaction.channel, discord.TextChannel):
        target = interaction.channel

    if target is None:
        return await _reply_once(interaction, "❌ I could not find a text channel to post the ticket panel. Pick a channel explicitly.")

    me = guild.me
    if me is not None:
        perms = target.permissions_for(me)
        missing: list[str] = []
        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.send_messages:
            missing.append("Send Messages")
        if not perms.embed_links:
            missing.append("Embed Links")
        if missing:
            return await _reply_once(interaction, f"❌ I cannot post the ticket panel in {target.mention}. Missing: {', '.join(missing)}.")

    try:
        msg = await target.send(
            embed=_panel_embed(guild),
            view=PublicTicketPanelButtonView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as e:
        return await _reply_once(interaction, f"❌ Failed posting ticket panel in {target.mention}: `{type(e).__name__}: {_truncate(e, 180)}`")

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

    return await _reply_once(interaction, f"✅ Posted the **menu-first Create Ticket** panel in {target.mention}.")


@app_commands.command(
    name=_TOP_LEVEL_COMMAND_NAME,
    description="Post the public Create Ticket menu panel for users.",
)
@app_commands.describe(channel="Optional channel. Defaults to configured support/ticket-panel channel, then current channel.")
async def ticket_panel_top_level_command(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> None:
    await _post_ticket_panel_command(interaction, channel)


def _attach_group_command(module: Any) -> None:
    global _GROUP_PATCHED
    if _GROUP_PATCHED:
        return

    group = getattr(module, "ticket_intake_group", None)
    if group is None:
        return

    try:
        existing = group.get_command("post-panel")
    except Exception:
        existing = None
    if existing is not None:
        _GROUP_PATCHED = True
        return

    try:
        command = app_commands.Command(
            name="post-panel",
            description="Post the public Create Ticket menu panel for users.",
            callback=_post_ticket_panel_command,
        )
        try:
            command._params["channel"].description = "Optional channel. Defaults to configured support/ticket-panel channel."
        except Exception:
            pass
        group.add_command(command)
        _GROUP_PATCHED = True
        _log("attached /ticket-intake post-panel menu-first command")
    except Exception as e:
        _log(f"failed attaching /ticket-intake post-panel: {e!r}")


def _tree_has_command(tree: Any, name: str) -> bool:
    try:
        return tree.get_command(name, guild=None) is not None
    except Exception:
        return False


def _add_top_level_command(tree: Any) -> None:
    if _tree_has_command(tree, _TOP_LEVEL_COMMAND_NAME):
        return
    try:
        tree.add_command(ticket_panel_top_level_command)
        _log("registered /ticket-panel menu-first direct command")
    except Exception as e:
        _log(f"failed registering /ticket-panel direct command: {e!r}")


def _patch_register_function(module: Any) -> None:
    global _REGISTER_PATCHED
    if _REGISTER_PATCHED:
        return

    original = getattr(module, "register_public_ticket_intake_group_commands", None)
    if not callable(original):
        return
    if getattr(original, "_ticket_panel_command_wrapped", False):
        _REGISTER_PATCHED = True
        return

    def register_public_ticket_intake_group_commands_patched(bot: Any, tree: Any) -> None:
        _register_persistent_public_panel_view()
        _attach_group_command(module)
        try:
            original(bot, tree)
        finally:
            _add_top_level_command(tree)

    try:
        setattr(register_public_ticket_intake_group_commands_patched, "_ticket_panel_command_wrapped", True)
    except Exception:
        pass
    setattr(module, "register_public_ticket_intake_group_commands", register_public_ticket_intake_group_commands_patched)
    _REGISTER_PATCHED = True
    _log("patched intake registration to include menu-first /ticket-panel direct command")


def _patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.commands_ext.public_ticket_intake_group")
        if module is not None:
            _attach_group_command(module)
            _patch_register_function(module)
    except Exception:
        pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.commands_ext.public_ticket_intake_group" or name.endswith("commands_ext.public_ticket_intake_group"):
            target = sys.modules.get("stoney_verify.commands_ext.public_ticket_intake_group") or sys.modules.get(name)
            if target is not None:
                _attach_group_command(target)
                _patch_register_function(target)
        else:
            _patch_loaded()
    except Exception:
        pass
    return module


builtins.__import__ = _safe_import
_register_persistent_public_panel_view()
_patch_loaded()
_log("loaded; menu-first public ticket panel command guard active")


__all__ = ["PublicTicketPanelButtonView", "ticket_panel_top_level_command"]
