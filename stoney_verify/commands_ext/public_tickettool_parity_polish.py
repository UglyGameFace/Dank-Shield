from __future__ import annotations

"""Small public TicketTool parity polish layer.

This module owns the public /ticket-panel post command in the public command
surface. It intentionally does not import public_ticket_panel_commands.py because
that older module posts TicketPanelView(), which opens the Discord reason modal.

The public panel flow is intentionally boring and reliable:
- /ticket-panel post posts one user-facing Create Ticket button
- Create Ticket opens a category dropdown first
- selecting a category creates the ticket through tickets_new.service
- the created channel is repaired into the configured Active Tickets category
"""

import asyncio
import inspect
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands

from .common import _staff_check, reply_once
from . import ticket_category_admin as legacy
from . import ticket_admin as legacy_ticket_admin
from . import public_ticket_category_group as category_group_module
from .public_ticket_category_group import ticket_category_group, _add_governance_warnings

_ATTACHED = False
_CHECKER_PATCHED = False
_PANEL_VIEW_REGISTERED = False
_PANEL_GROUP_REGISTERED = False

PANEL_BUTTON_CUSTOM_ID = "sv:ticket:panel:create:v6"

_DUPLICATE_CATEGORY_CANONICALS: Dict[str, str] = {
    "verification-help": "verification",
    "verification-issue": "verification",
    "verify": "verification",
    "bug-report": "bug",
    "bug-technical-support": "bug",
    "technical-support": "bug",
    "other": "support",
    "general": "support",
    "general-support": "support",
}

_CANONICAL_PRIORITY: Tuple[str, ...] = (
    "verification",
    "account-access",
    "payments-refunds",
    "appeal",
    "report",
    "staff-complaint",
    "cod-services",
    "service-request",
    "vouch-referral",
    "giveaway-reward",
    "content-media",
    "partnership",
    "question",
    "bug",
    "support",
)


def _log(message: str) -> None:
    try:
        print(f"✅ public_tickettool_parity_polish: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_tickettool_parity_polish: {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
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


def _slugify(value: Any, limit: int = 100) -> str:
    raw = _safe_str(value).lower().replace("&", " and ")
    out: List[str] = []
    prev_dash = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_", "/", ":"}:
            if not prev_dash:
                out.append("-")
                prev_dash = True
    return "".join(out).strip("-")[:limit]


def _truncate(value: Any, limit: int = 1000) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


async def _staff_only(interaction: discord.Interaction) -> bool:
    if not _staff_check(interaction):
        await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
        return False
    return True


async def _guild_only(interaction: discord.Interaction) -> Optional[discord.Guild]:
    guild = interaction.guild
    if guild is None:
        await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})
        return None
    return guild


async def _send_ephemeral(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: Dict[str, Any] = {
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


def _row_value(row: Dict[str, Any]) -> str:
    slug = legacy._safe_str(row.get("slug"), "unknown")
    intake_type = legacy._safe_str(row.get("intake_type"), "general")
    is_default = bool(row.get("is_default"))
    sort_order = row.get("sort_order")
    keywords = row.get("match_keywords") or []
    keyword_count = len(keywords) if isinstance(keywords, list) else 0
    default_text = " ⭐ **default**" if is_default else ""
    return (
        f"Slug: `{slug}`{default_text}\n"
        f"Type: `{intake_type}` • Sort: `{sort_order}` • Keywords: `{keyword_count}`"
    )[:1024]


async def _category_list_callback(interaction: discord.Interaction) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_only(interaction)
    if guild is None:
        return

    rows = await legacy._fetch_categories(guild.id)
    if not rows:
        return await reply_once(
            interaction,
            {
                "content": "ℹ️ No ticket categories are configured yet. Use `/ticket-category create` or `/stoney setup-defaults`.",
                "ephemeral": True,
            },
        )

    embed = discord.Embed(
        title="🎫 Ticket Categories",
        description=f"{len(rows)} configured category/categories for this server.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    for index, row in enumerate(rows[:25], start=1):
        name = legacy._safe_str(row.get("name"), "Unnamed Category")
        embed.add_field(name=f"{index}. {name}"[:256], value=_row_value(row), inline=False)

    if len(rows) > 25:
        embed.add_field(name="More categories", value=f"Showing 25 of {len(rows)} categories.", inline=False)

    _add_governance_warnings(embed, rows)
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


async def _category_update_callback(
    interaction: discord.Interaction,
    slug: str,
    name: Optional[str] = None,
    intake_type: Optional[str] = None,
    description: Optional[str] = None,
    keywords: Optional[str] = None,
    is_default: Optional[bool] = None,
    sort_order: Optional[int] = None,
) -> None:
    target = getattr(category_group_module, "category_edit", None)
    callback = getattr(target, "callback", None) or target
    if not callable(callback):
        return await reply_once(interaction, {"content": "❌ Category edit handler is unavailable.", "ephemeral": True})

    await callback(
        interaction,
        slug,
        name=name,
        intake_type=intake_type,
        description=description,
        keywords=keywords,
        is_default=is_default,
        sort_order=sort_order,
    )


_category_list_callback = app_commands.describe()(_category_list_callback)  # type: ignore[assignment]
_category_update_callback = app_commands.describe(  # type: ignore[assignment]
    slug="Existing category slug to update.",
    name="New display name.",
    intake_type="New intake type.",
    description="New description.",
    keywords="New comma-separated keywords.",
    is_default="Set or unset as default.",
    sort_order="New sort order.",
)(_category_update_callback)


def _ensure_command(name: str, description: str, callback: Any) -> bool:
    try:
        if ticket_category_group.get_command(name) is not None:
            return False
    except Exception:
        pass

    ticket_category_group.add_command(app_commands.Command(name=name, description=description, callback=callback))
    return True


# ============================================================
# Public /ticket-panel post category-menu flow
# ============================================================

def _ticket_panel_group() -> app_commands.Group:
    group = app_commands.Group(name="ticket-panel", description="Manage and post ticket panels.")

    @group.command(name="post", description="Post the public Create Ticket category menu panel.")
    @app_commands.describe(channel="Optional channel. Defaults to configured support/ticket-panel channel, then current channel.")
    async def post(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        await _post_ticket_panel(interaction, channel=channel)

    return group


def _panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🎫 Need help? Open a ticket",
        description=(
            "Press **Create Ticket** below, then pick the ticket type.\n\n"
            "No form first. No guessing. Just choose the category that matches what you need."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="How it works",
        value="1. Press **Create Ticket**\n2. Pick a ticket type\n3. A private ticket channel opens",
        inline=False,
    )
    embed.set_footer(text=f"{guild.name} • Dank Shield ticket panel • category-menu")
    return embed


async def _configured_panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from ..guild_config import get_guild_config

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


async def _active_ticket_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    try:
        from ..guild_config import get_guild_config

        cfg = await get_guild_config(guild.id, refresh=True)
        for attr in (
            "ticket_category_id",
            "active_ticket_category_id",
            "ticket_active_category_id",
            "ticket_parent_category_id",
            "open_ticket_category_id",
        ):
            cid = _safe_int(getattr(cfg, attr, 0), 0)
            if cid <= 0:
                continue
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.CategoryChannel):
                return ch
    except Exception:
        pass

    try:
        for category in guild.categories:
            text = category.name.lower()
            if "active" in text and "ticket" in text:
                return category
    except Exception:
        pass
    return None


def _row_slug(row: Dict[str, Any]) -> str:
    return _slugify(row.get("slug") or row.get("category_slug") or row.get("name") or "support") or "support"


def _row_name(row: Dict[str, Any]) -> str:
    return _safe_str(row.get("button_label") or row.get("name") or row.get("display_name") or row.get("category_name"), _row_slug(row))[:100]


def _row_description(row: Dict[str, Any]) -> str:
    return _safe_str(row.get("description") or row.get("intake_type") or row.get("type"), "Open a support ticket")[:100]


def _row_sort(row: Dict[str, Any]) -> int:
    return _safe_int(row.get("sort_order", row.get("position", 999)), 999)


def _canonical_category_key(row: Dict[str, Any]) -> str:
    slug = _row_slug(row).replace("_", "-")
    name = _slugify(_row_name(row)).replace("_", "-")
    text = f"{slug} {name}".lower()

    if slug in _DUPLICATE_CATEGORY_CANONICALS:
        return _DUPLICATE_CATEGORY_CANONICALS[slug]
    if "verification" in text or text.startswith("verify"):
        return "verification"
    if "staff" in text and "complaint" in text:
        return "staff-complaint"
    if "cod" in text or "call-of-duty" in text:
        return "cod-services"
    if "vouch" in text or "referral" in text or "invite" in text:
        return "vouch-referral"
    if "giveaway" in text or "reward" in text:
        return "giveaway-reward"
    if "content" in text or "media" in text:
        return "content-media"
    if "partner" in text:
        return "partnership"
    if "appeal" in text:
        return "appeal"
    if "report" in text:
        return "report"
    if "account" in text or "access" in text:
        return "account-access"
    if "payment" in text or "refund" in text:
        return "payments-refunds"
    if "service" in text:
        return "service-request"
    if "question" in text:
        return "question"
    if "bug" in text or "technical" in text:
        return "bug"
    if "other" in text or "support" in text or "general" in text:
        return "support"
    return slug


def _canonical_rank(key: str) -> int:
    try:
        return _CANONICAL_PRIORITY.index(key)
    except ValueError:
        return 999


def _prefer_category_row(existing: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    existing_slug = _row_slug(existing)
    candidate_slug = _row_slug(candidate)

    # Prefer explicit, polished default category names over older helper aliases.
    bad_slugs = {"verification-help", "bug-report", "other", "general"}
    if existing_slug in bad_slugs and candidate_slug not in bad_slugs:
        return candidate
    if candidate_slug in bad_slugs and existing_slug not in bad_slugs:
        return existing

    existing_sort = _row_sort(existing)
    candidate_sort = _row_sort(candidate)
    if candidate_sort < existing_sort:
        return candidate
    return existing


def _normalize_ticket_rows(raw_rows: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        for item in raw_rows or []:
            if isinstance(item, dict):
                rows.append(dict(item))
    except Exception:
        pass

    by_canonical: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = _canonical_category_key(row)
        if key not in by_canonical:
            by_canonical[key] = row
        else:
            by_canonical[key] = _prefer_category_row(by_canonical[key], row)

    out = list(by_canonical.values())
    out.sort(key=lambda r: (_canonical_rank(_canonical_category_key(r)), _row_sort(r), _row_name(r).lower(), _row_slug(r)))
    return out[:25]


async def _load_ticket_rows(guild: discord.Guild) -> List[Dict[str, Any]]:
    try:
        from ..tickets_new import panel

        fetcher = getattr(panel, "_fetch_dashboard_ticket_categories_sync", None)
        if callable(fetcher):
            rows = await asyncio.to_thread(fetcher, int(guild.id))
            normalized = _normalize_ticket_rows(rows)
            if normalized:
                return normalized

        seeder = getattr(panel, "_seed_dashboard_ticket_categories_sync", None)
        if callable(seeder):
            rows = await asyncio.to_thread(seeder, int(guild.id))
            normalized = _normalize_ticket_rows(rows)
            if normalized:
                return normalized

        defaults = _normalize_ticket_rows(getattr(panel, "_DEFAULT_BOOTSTRAP_CATEGORIES", None))
        if defaults:
            return defaults
    except Exception as e:
        _warn(f"ticket category menu fallback used: {e!r}")

    return [{"name": "Support", "slug": "support", "description": "General support request.", "sort_order": 999, "is_default": True}]


async def _existing_open_ticket(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    try:
        from ..tickets_new.service import find_open_ticket_for_owner

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


def _extract_created_channel(result: Any, guild: discord.Guild) -> Optional[discord.TextChannel]:
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
            ch = _extract_created_channel(item, guild)
            if ch is not None:
                return ch
    return None


async def _maybe_async(func: Any, *args: Any, **kwargs: Any) -> Any:
    result = func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _signature_accepts(signature: inspect.Signature, key: str) -> bool:
    return key in signature.parameters


def _filtered_kwargs(signature: inspect.Signature, values: Dict[str, Any]) -> Dict[str, Any]:
    # Do not pass through **kwargs wrappers. Several startup guards wrap the
    # service with *args/**kwargs and forward directly to the original, so only
    # pass names that the visible callable explicitly declares.
    return {key: value for key, value in values.items() if value is not None and _signature_accepts(signature, key)}


async def _call_create_ticket_channel(
    *,
    create_ticket_channel: Any,
    guild: discord.Guild,
    member: discord.Member,
    slug: str,
    name: str,
    reason: str,
    active_category: Optional[discord.CategoryChannel],
    row: Dict[str, Any],
) -> Any:
    metadata = {
        "source": "ticket_panel_category_menu",
        "selected_category_slug": slug,
        "selected_category_name": name,
        "ticket_category_row_id": _safe_str(row.get("id")),
    }

    values: Dict[str, Any] = {
        "guild": guild,
        "owner": member,
        "member": member,
        "user": member,
        "requester": member,
        "category": slug,
        "category_slug": slug,
        "category_name": name,
        "reason": reason,
        "message": reason,
        "description": reason,
        "priority": "medium",
        "is_ghost": False,
        "ghost": False,
        "metadata": metadata,
        "meta": metadata,
        "ticket_category_row_id": _safe_str(row.get("id")) or None,
        "matched_category_id": _safe_str(row.get("id")) or None,
        "matched_category_name": name,
        "matched_category_slug": slug,
        "matched_intake_type": _safe_str(row.get("intake_type") or row.get("type") or "custom"),
        "category_override": True,
    }

    if active_category is not None:
        values.update(
            {
                "parent_category": active_category,
                "category_channel": active_category,
                "ticket_category": active_category,
                "ticket_parent": active_category,
                "parent_category_id": int(active_category.id),
                "ticket_category_id": int(active_category.id),
                "ticket_parent_category_id": int(active_category.id),
                "active_ticket_category_id": int(active_category.id),
                "explicit_parent_category_id": int(active_category.id),
            }
        )

    attempts: List[Tuple[Tuple[Any, ...], Dict[str, Any], str]] = []

    try:
        signature = inspect.signature(create_ticket_channel)
        filtered = _filtered_kwargs(signature, values)
        if filtered:
            attempts.append(((), filtered, "filtered_kwargs"))
    except Exception:
        pass

    attempts.extend(
        [
            ((), {"guild": guild, "owner": member, "category": slug, "reason": reason}, "guild_owner_category_reason"),
            ((), {"guild": guild, "member": member, "category": slug, "reason": reason}, "guild_member_category_reason"),
            ((), {"guild": guild, "user": member, "category": slug, "reason": reason}, "guild_user_category_reason"),
            ((guild, member, slug, reason), {}, "pos_guild_member_slug_reason"),
            ((guild, member, slug), {}, "pos_guild_member_slug"),
            ((guild, member), {}, "pos_guild_member"),
        ]
    )

    last_error: Optional[BaseException] = None
    for args, kwargs, label in attempts:
        try:
            return await _maybe_async(create_ticket_channel, *args, **kwargs)
        except TypeError as e:
            last_error = e
            text = repr(e).lower()
            if "unexpected keyword" in text or "positional" in text or "required positional" in text or "missing" in text:
                continue
            raise
        except Exception:
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("No compatible create_ticket_channel signature was available.")


async def _create_ticket_from_row(interaction: discord.Interaction, row: Dict[str, Any]) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

    await _defer_ephemeral(interaction, thinking=True)

    existing = await _existing_open_ticket(guild, member)
    if existing is not None:
        return await _send_ephemeral(interaction, f"You already have an open ticket: {existing.mention}")

    active_category = await _active_ticket_category(guild)
    slug = _row_slug(row)
    name = _row_name(row)
    reason = f"{name} ticket opened from public ticket category menu."

    try:
        from ..tickets_new.service import create_ticket_channel
    except Exception as e:
        return await _send_ephemeral(interaction, f"❌ Ticket creation service unavailable: `{type(e).__name__}`")

    try:
        result = await _call_create_ticket_channel(
            create_ticket_channel=create_ticket_channel,
            guild=guild,
            member=member,
            slug=slug,
            name=name,
            reason=reason,
            active_category=active_category,
            row=row,
        )
    except Exception as e:
        return await _send_ephemeral(interaction, f"❌ Failed to create ticket: `{type(e).__name__}: {_truncate(e, 240)}`")

    channel = _extract_created_channel(result, guild)
    if channel is not None and active_category is not None:
        try:
            if getattr(channel.category, "id", 0) != active_category.id:
                await channel.edit(category=active_category, sync_permissions=False, reason="Ticket panel category placement repair")
        except Exception as e:
            return await _send_ephemeral(
                interaction,
                f"⚠️ Ticket created at {channel.mention}, but I could not move it to **{active_category.name}**. Error: `{type(e).__name__}: {_truncate(e, 180)}`",
            )

    if channel is not None:
        return await _send_ephemeral(interaction, f"✅ Ticket created: {channel.mention}")

    return await _send_ephemeral(interaction, "✅ Ticket created. If you do not see it, ask staff to check ticket category permissions.")


class TicketCategorySelect(discord.ui.Select):
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        options = [
            discord.SelectOption(label=_row_name(row), value=_row_slug(row), description=_row_description(row), emoji="🎫")
            for row in rows[:25]
        ] or [discord.SelectOption(label="Support", value="support", description="General support request", emoji="🎫")]
        super().__init__(placeholder="Choose a ticket type", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = _safe_str(self.values[0], "support")
        row = next((item for item in self.rows if _row_slug(item) == selected), {"name": "Support", "slug": selected})
        await _create_ticket_from_row(interaction, row)


class TicketCategorySelectView(discord.ui.View):
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        super().__init__(timeout=300)
        self.add_item(TicketCategorySelect(rows))


class PublicCreateTicketPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green, emoji="🎫", custom_id=PANEL_BUTTON_CUSTOM_ID)
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _send_ephemeral(interaction, "❌ This must be used inside a server.")

        try:
            from ..startup_guards.unverified_ticket_panel_flow import _handle_unverified_panel_click

            if await _handle_unverified_panel_click(interaction):
                return
        except Exception:
            pass

        existing = await _existing_open_ticket(guild, member)
        if existing is not None:
            return await _send_ephemeral(interaction, f"You already have an open ticket: {existing.mention}")

        rows = await _load_ticket_rows(guild)
        embed = discord.Embed(
            title="Create Ticket",
            description="Choose the type of ticket you want to open.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Pick a category. No form needed.")
        await _send_ephemeral(interaction, "Choose a ticket type.", embed=embed, view=TicketCategorySelectView(rows))


async def _post_ticket_panel(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    if not await _staff_only(interaction):
        return

    await _defer_ephemeral(interaction)

    guild = interaction.guild
    if guild is None:
        return await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})

    target = channel or await _configured_panel_channel(guild)
    if target is None and isinstance(interaction.channel, discord.TextChannel):
        target = interaction.channel
    if target is None:
        return await reply_once(interaction, {"content": "❌ I could not find a text channel to post the ticket panel.", "ephemeral": True})

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
        msg = await target.send(embed=_panel_embed(guild), view=PublicCreateTicketPanelView(), allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        return await reply_once(
            interaction,
            {"content": f"❌ Failed posting ticket panel in {target.mention}: `{type(e).__name__}: {_truncate(e, 180)}`", "ephemeral": True},
        )

    try:
        from .public_setup_config_writer import upsert_guild_config
        from ..guild_config import invalidate_guild_config

        await upsert_guild_config(guild.id, {"ticket_panel_channel_id": str(target.id), "ticket_panel_message_id": str(msg.id)})
        invalidate_guild_config(guild.id)
    except Exception:
        pass

    await reply_once(interaction, {"content": f"✅ Posted the public **category-menu Create Ticket** panel in {target.mention}.", "ephemeral": True})


def _register_ticket_panel_group(bot: Any, tree: Any) -> None:
    global _PANEL_GROUP_REGISTERED, _PANEL_VIEW_REGISTERED

    if not _PANEL_VIEW_REGISTERED:
        try:
            bot.add_view(PublicCreateTicketPanelView())
            _PANEL_VIEW_REGISTERED = True
            _log("registered persistent category-menu Create Ticket view")
        except Exception as e:
            _warn(f"could not register persistent Create Ticket view: {e!r}")

    if _PANEL_GROUP_REGISTERED:
        return

    try:
        if tree.get_command("ticket-panel", guild=None) is not None:
            tree.remove_command("ticket-panel", guild=None)
            _log("removed old /ticket-panel command before registering category-menu group")
    except Exception:
        pass

    try:
        tree.add_command(_ticket_panel_group())
        _PANEL_GROUP_REGISTERED = True
        _log("registered /ticket-panel post category-menu command")
    except Exception as e:
        _warn(f"could not register /ticket-panel post: {e!r}")


def _expose_staff_action_view_for_parity_check() -> None:
    try:
        from ..tickets_new.panel import TicketChannelActionsView

        setattr(legacy_ticket_admin, "TicketChannelActionsView", TicketChannelActionsView)
        _log("exposed TicketChannelActionsView to parity checker")
    except Exception as e:
        _warn(f"could not expose TicketChannelActionsView: {e!r}")


def _patch_tickettool_checker_strictness(tree: Any) -> None:
    global _CHECKER_PATCHED
    if _CHECKER_PATCHED:
        return

    try:
        from . import public_tickettool_check as checker

        original = getattr(checker, "_command_surface_checks", None)
        if not callable(original) or getattr(original, "_panel_strict_wrapped", False):
            _CHECKER_PATCHED = True
            return

        def _strict_command_surface_checks():
            blockers, warnings, ok = original()

            has_top_level = False
            try:
                has_top_level = tree.get_command("ticket-panel", guild=None) is not None
            except Exception:
                has_top_level = False

            if has_top_level:
                ok.append("`/ticket-panel post` public Create Ticket category-menu command is registered.")
            else:
                blockers.append("Missing `/ticket-panel post` command for posting the public Create Ticket button.")

            return blockers, warnings, ok

        try:
            setattr(_strict_command_surface_checks, "_panel_strict_wrapped", True)
        except Exception:
            pass
        setattr(checker, "_command_surface_checks", _strict_command_surface_checks)
        _CHECKER_PATCHED = True
        _log("tightened TicketTool checker to require /ticket-panel post")
    except Exception as e:
        _warn(f"could not patch TicketTool checker strictness: {e!r}")


def register_public_tickettool_parity_polish(bot: Any, tree: Any) -> None:
    global _ATTACHED

    _register_ticket_panel_group(bot, tree)

    if not _ATTACHED:
        added: List[str] = []
        if _ensure_command("list", "List configured dashboard ticket categories.", _category_list_callback):
            added.append("list")
        if _ensure_command("update", "Alias for editing/updating an existing ticket category.", _category_update_callback):
            added.append("update")
        _ATTACHED = True
        if added:
            _log(f"attached /ticket-category aliases: {', '.join(added)}")

    _expose_staff_action_view_for_parity_check()
    _patch_tickettool_checker_strictness(tree)


__all__ = ["register_public_tickettool_parity_polish"]
