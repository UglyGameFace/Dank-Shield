from __future__ import annotations

"""Clean public ticket panel flow.

This is the single public `/ticket-panel post` owner.

What this file guarantees:
- no modal-first ticket creation
- persistent Create Ticket button opens a category select first
- category select shows Confirm / Back before creating anything
- ticket creates only after Confirm in the saved Active Tickets category
- the panel button ACKs immediately so Discord does not show silent interaction failures
- the public Create Ticket button stays usable across bot restarts
- ticket DB fallback includes required title/username fields
- stale open DB rows from already-closed/deleted tickets do not block new tickets
- health check reports setup/category/permission/schema problems clearly

Do not add router/patch modules on top of this file for ticket-panel behavior.
Fix this file directly.
"""

import asyncio
import os
import re
from datetime import timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord
from discord import app_commands

from .common import _staff_check, reply_once

_PANEL_VIEW_REGISTERED = False
_PANEL_GROUP_REGISTERED = False
_PANEL_FALLBACK_LISTENER_REGISTERED = False
_HEALTH_PATCHED = False

PANEL_BUTTON_CUSTOM_ID = "sv:ticket:panel:create:clean:v1"
PANEL_BUTTON_CUSTOM_IDS = {PANEL_BUTTON_CUSTOM_ID}

# Simple, boring TicketTool-style defaults.
# The order is intentional: most common/urgent paths first, "Other" last.
DEFAULT_ROWS: Tuple[Dict[str, Any], ...] = (
    {
        "slug": "verification",
        "name": "Verification",
        "description": "Help with verification or approval issues.",
        "sort_order": 10,
    },
    {
        "slug": "support",
        "name": "Support",
        "description": "General help from staff.",
        "sort_order": 20,
        "is_default": True,
    },
    {
        "slug": "report",
        "name": "Report a Member",
        "description": "Report scams, abuse, spam, raids, or rule breaks.",
        "sort_order": 30,
    },
    {
        "slug": "appeal",
        "name": "Appeal",
        "description": "Appeal a ban, timeout, mute, or access restriction.",
        "sort_order": 40,
    },
    {
        "slug": "bug",
        "name": "Bug Report",
        "description": "Report a bot or server workflow issue.",
        "sort_order": 50,
    },
    {
        "slug": "question",
        "name": "Other Question",
        "description": "Ask something that does not fit the other options.",
        "sort_order": 60,
    },
)

# Required for both health checks and the minimal DB fallback insert.
# Do not include generated columns like id.
TICKET_REQUIRED_COLUMNS: Tuple[str, ...] = (
    "guild_id",
    "user_id",
    "username",
    "title",
    "status",
    "category",
    "channel_id",
    "discord_thread_id",
    "ticket_number",
    "created_at",
    "updated_at",
)

TICKET_CATEGORY_REQUIRED_COLUMNS: Tuple[str, ...] = (
    "guild_id",
    "slug",
    "name",
    "description",
    "sort_order",
    "is_default",
    "is_enabled",
)


def _log(msg: str) -> None:
    try:
        print(f"✅ public_ticket_panel_clean: {msg}")
    except Exception:
        pass


def _warn(msg: str) -> None:
    try:
        print(f"⚠️ public_ticket_panel_clean: {msg}")
    except Exception:
        pass


def _safe_str(v: Any, default: str = "") -> str:
    try:
        s = str(v or "").strip()
        return s if s else default
    except Exception:
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or isinstance(v, bool):
            return int(default)
        s = str(v).strip()
        return int(s) if s else int(default)
    except Exception:
        return int(default)


def _short(v: Any, limit: int = 180) -> str:
    s = _safe_str(v)
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def _slug(v: Any) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _safe_str(v, "support").lower()).strip("-")
    return s[:80] or "support"


def _now_iso() -> str:
    return discord.utils.utcnow().astimezone(timezone.utc).isoformat()


def _db_url_present() -> bool:
    return any(
        _safe_str(os.getenv(k))
        for k in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL")
    )


def _sb() -> Any:
    try:
        from ..globals import get_supabase

        return get_supabase()
    except Exception as e:
        _warn(f"supabase unavailable: {type(e).__name__}: {_short(e, 220)}")
        return None


async def _to_thread(fn, default: Any = None) -> Any:
    try:
        return await asyncio.to_thread(fn)
    except Exception as e:
        _warn(f"db op failed: {type(e).__name__}: {_short(e, 220)}")
        return default


async def _cfg(guild_id: int) -> Any:
    try:
        from ..guild_config import get_guild_config

        return await get_guild_config(int(guild_id), refresh=True)
    except Exception as e:
        _warn(f"guild config load failed guild={guild_id}: {type(e).__name__}: {_short(e, 220)}")
        return None


def _cfg_get(config: Any, *names: str) -> Any:
    for n in names:
        try:
            v = config.get(n) if hasattr(config, "get") else getattr(config, n, None)
            if v not in {None, "", 0, "0"}:
                return v
        except Exception:
            pass
    return None


def _channel(guild: discord.Guild, cid: Any) -> Optional[discord.abc.GuildChannel]:
    i = _safe_int(cid, 0)
    return guild.get_channel(i) if i > 0 else None


async def _active_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    config = await _cfg(guild.id)
    ch = _channel(
        guild,
        _cfg_get(
            config,
            "ticket_category_id",
            "active_ticket_category_id",
            "ticket_active_category_id",
            "open_ticket_category_id",
        ),
    )
    if isinstance(ch, discord.CategoryChannel):
        return ch

    for c in guild.categories:
        n = c.name.lower()
        if "active" in n and "ticket" in n:
            return c

    return None


async def _archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    config = await _cfg(guild.id)
    ch = _channel(
        guild,
        _cfg_get(config, "ticket_archive_category_id", "archive_ticket_category_id", "ticket_archived_category_id"),
    )
    if isinstance(ch, discord.CategoryChannel):
        return ch

    for c in guild.categories:
        n = c.name.lower()
        if "archive" in n and "ticket" in n:
            return c

    return None


async def _panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    config = await _cfg(guild.id)
    ch = _channel(guild, _cfg_get(config, "ticket_panel_channel_id", "support_channel_id", "ticket_support_channel_id"))
    return ch if isinstance(ch, discord.TextChannel) else None


async def _transcript_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    config = await _cfg(guild.id)
    ch = _channel(
        guild,
        _cfg_get(config, "transcripts_channel_id", "ticket_transcripts_channel_id", "transcript_channel_id"),
    )
    return ch if isinstance(ch, discord.TextChannel) else None


async def _staff_role(guild: discord.Guild) -> Optional[discord.Role]:
    # Per-guild only. No global STAFF_ROLE_ID fallback for public servers.
    config = await _cfg(guild.id)
    rid = _safe_int(_cfg_get(config, "staff_role_id", "ticket_staff_role_id", "support_role_id"), 0)
    return guild.get_role(rid) if rid > 0 else None


def _missing_text_perms(ch: discord.TextChannel, me: Optional[discord.Member], manage: bool = False) -> List[str]:
    if me is None:
        return ["Bot member unavailable"]

    p = ch.permissions_for(me)
    checks = [
        ("View Channel", p.view_channel),
        ("Send Messages", p.send_messages),
        ("Read Message History", p.read_message_history),
        ("Embed Links", p.embed_links),
        ("Attach Files", p.attach_files),
    ]

    if manage:
        checks += [("Manage Channels", p.manage_channels), ("Manage Permissions", p.manage_permissions)]

    return [n for n, ok in checks if not ok]


def _missing_category_perms(cat: discord.CategoryChannel, me: Optional[discord.Member]) -> List[str]:
    if me is None:
        return ["Bot member unavailable"]

    p = cat.permissions_for(me)
    checks = [
        ("View Channel", p.view_channel),
        ("Send Messages", p.send_messages),
        ("Read Message History", p.read_message_history),
        ("Embed Links", p.embed_links),
        ("Attach Files", p.attach_files),
        ("Manage Channels", p.manage_channels),
        ("Manage Permissions", p.manage_permissions),
    ]
    return [n for n, ok in checks if not ok]


def _row_slug(row: Dict[str, Any]) -> str:
    return _slug(row.get("slug") or row.get("category_slug") or row.get("name") or row.get("title") or "support")


def _canon_key(raw: Any) -> str:
    text = _slug(raw)
    if "verify" in text or "verification" in text:
        return "verification"
    if "support" in text or "help" in text or "general" in text:
        return "support"
    if "report" in text or "scam" in text or "raid" in text or "abuse" in text:
        return "report"
    if "appeal" in text or "ban" in text or "mute" in text or "timeout" in text:
        return "appeal"
    if "bug" in text or "technical" in text or "issue" in text:
        return "bug"
    if "question" in text or "other" in text or "custom" in text:
        return "question"
    return text or "support"


def _canonical_label(slug: str, current: str) -> str:
    key = _canon_key(f"{slug} {current}")
    labels = {
        "verification": "Verification",
        "support": "Support",
        "report": "Report a Member",
        "appeal": "Appeal",
        "bug": "Bug Report",
        "question": "Other Question",
    }
    return labels.get(key, _safe_str(current, "Support")[:100])


def _canonical_description(slug: str, current: str) -> str:
    key = _canon_key(f"{slug} {current}")
    descriptions = {
        "verification": "Help with verification or approval issues.",
        "support": "General help from staff.",
        "report": "Report scams, abuse, spam, raids, or rule breaks.",
        "appeal": "Appeal a ban, timeout, mute, or access restriction.",
        "bug": "Report a bot or server workflow issue.",
        "question": "Ask something that does not fit the other options.",
    }
    raw = _safe_str(current)
    return descriptions.get(key, raw[:100] if raw else "Open a support ticket.")


def _row_name(row: Dict[str, Any]) -> str:
    raw = _safe_str(
        row.get("button_label") or row.get("name") or row.get("display_name") or row.get("title") or _row_slug(row),
        "Support",
    )
    return _canonical_label(_row_slug(row), raw)[:100]


def _row_desc(row: Dict[str, Any]) -> str:
    raw = _safe_str(row.get("description") or row.get("intake_type") or "", "")
    return _canonical_description(_row_slug(row), raw)[:100]


def _row_sort(row: Dict[str, Any]) -> int:
    return _safe_int(row.get("sort_order", row.get("position", 999)), 999)


def _canon(row: Dict[str, Any]) -> str:
    return _canon_key(f"{_row_slug(row)} {_safe_str(row.get('name') or row.get('title') or '')}")


def _rows(raw: Any) -> List[Dict[str, Any]]:
    priority = {"verification": 0, "support": 1, "report": 2, "appeal": 3, "bug": 4, "question": 5}
    by: Dict[str, Dict[str, Any]] = {}

    for x in raw or []:
        if not isinstance(x, dict) or x.get("is_enabled") is False:
            continue

        k = _canon(x)
        if k not in by or _row_sort(x) < _row_sort(by[k]):
            by[k] = dict(x)

    out = list(by.values())
    out.sort(key=lambda r: (priority.get(_canon(r), 99), _row_sort(r), _row_name(r).lower()))
    return out[:25]


async def _load_rows(guild: discord.Guild) -> Tuple[List[Dict[str, Any]], str]:
    sb = _sb()

    if sb is None:
        return list(DEFAULT_ROWS), "Supabase client unavailable; using fallback categories."

    def sync() -> Tuple[List[Dict[str, Any]], str]:
        try:
            res = sb.table("ticket_categories").select("*").eq("guild_id", str(guild.id)).execute()
            found = _rows(getattr(res, "data", None) or [])
            if found:
                return found, ""
            return list(DEFAULT_ROWS), "No ticket menu rows found; using fallback categories."
        except Exception as e:
            return list(DEFAULT_ROWS), f"Could not read ticket_categories: {type(e).__name__}: {_short(e, 220)}"

    return await _to_thread(sync, (list(DEFAULT_ROWS), "Could not read ticket categories."))


def _ticket_num(name: str) -> int:
    try:
        return int(name.rsplit("-", 1)[-1]) if name.startswith("ticket-") else 0
    except Exception:
        return 0


async def _next_number(guild: discord.Guild, parent: discord.CategoryChannel) -> int:
    n = max([0] + [_ticket_num(c.name) for c in list(parent.text_channels) + list(guild.text_channels)])
    sb = _sb()

    if sb is not None:
        def sync() -> int:
            try:
                data = getattr(
                    sb.table("tickets")
                    .select("ticket_number")
                    .eq("guild_id", str(guild.id))
                    .order("ticket_number", desc=True)
                    .limit(1)
                    .execute(),
                    "data",
                    None,
                ) or []
                return _safe_int(data[0].get("ticket_number"), 0) if data else 0
            except Exception:
                return 0

        n = max(n, _safe_int(await _to_thread(sync, 0), 0))

    return n + 1


async def _fetch_text(guild: discord.Guild, cid: int) -> Optional[discord.TextChannel]:
    if cid <= 0:
        return None

    ch = guild.get_channel(cid)
    if isinstance(ch, discord.TextChannel):
        return ch

    try:
        fetched = await guild.fetch_channel(cid)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


def _channel_is_closed_like(ch: discord.TextChannel) -> bool:
    try:
        name = (ch.name or "").lower()
        cat = (ch.category.name if ch.category else "").lower()
        return name.startswith("closed-") or ("archive" in cat and "ticket" in cat)
    except Exception:
        return False


async def _mark_row_stale(row_id: str, reason: str) -> None:
    sb = _sb()

    if sb is None or not row_id:
        return

    def sync() -> None:
        try:
            sb.table("tickets").update(
                {"status": "closed", "closed_at": _now_iso(), "updated_at": _now_iso(), "deleted_reason": reason}
            ).eq("id", row_id).execute()
        except Exception as e:
            _warn(f"stale ticket row repair failed id={row_id}: {type(e).__name__}: {_short(e, 220)}")

    await _to_thread(sync, None)


async def _existing_open(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    sb = _sb()

    if sb is None:
        return None

    for col in ("owner_id", "user_id", "requester_id"):
        def sync(c: str = col) -> List[Dict[str, Any]]:
            try:
                data = getattr(
                    sb.table("tickets")
                    .select("*")
                    .eq("guild_id", str(guild.id))
                    .eq(c, str(member.id))
                    .in_("status", ["open", "claimed"])
                    .order("created_at", desc=True)
                    .limit(5)
                    .execute(),
                    "data",
                    None,
                ) or []
                return [r for r in data if isinstance(r, dict)]
            except Exception as e:
                _warn(f"existing-open query failed guild={guild.id} user={member.id} col={c}: {type(e).__name__}: {_short(e, 220)}")
                return []

        for row in await _to_thread(sync, []):
            row_id = _safe_str(row.get("id"))
            ch = await _fetch_text(guild, _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0))

            if ch and not _channel_is_closed_like(ch):
                return ch

            await _mark_row_stale(row_id, "stale open row ignored by ticket panel")

    return None


def _overwrites(
    guild: discord.Guild,
    owner: discord.Member,
    bot_member: Optional[discord.Member],
    staff: Optional[discord.Role],
) -> Dict[Any, discord.PermissionOverwrite]:
    out: Dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        owner: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    if bot_member:
        out[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            manage_channels=True,
            manage_messages=True,
            manage_permissions=True,
        )

    if staff:
        out[staff] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
            manage_messages=True,
        )

    return out


def _ticket_insert_payload(
    guild: discord.Guild,
    owner: discord.Member,
    channel: discord.TextChannel,
    row: Dict[str, Any],
    number: int,
) -> Dict[str, Any]:
    slug = _row_slug(row)
    name = _row_name(row)
    return {
        "guild_id": str(guild.id),
        "user_id": str(owner.id),
        "owner_id": str(owner.id),
        "requester_id": str(owner.id),
        "username": str(owner),
        "owner_name": getattr(owner, "display_name", str(owner)),
        "requester_name": getattr(owner, "display_name", str(owner)),
        "title": name,
        "category": slug,
        "status": "open",
        "priority": "medium",
        "channel_id": str(channel.id),
        "discord_thread_id": str(channel.id),
        "channel_name": channel.name,
        "ticket_number": int(number),
        "is_ghost": False,
        "matched_category_name": name,
        "matched_category_slug": slug,
        "matched_intake_type": _safe_str(row.get("intake_type") or slug),
        "category_override": True,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


async def _insert_row(
    guild: discord.Guild,
    owner: discord.Member,
    channel: discord.TextChannel,
    row: Dict[str, Any],
    number: int,
) -> str:
    sb = _sb()

    if sb is None:
        return "Supabase client unavailable."

    payload = _ticket_insert_payload(guild, owner, channel, row, number)

    def sync() -> str:
        # Production-safe schema compatibility:
        # Insert only the columns this project treats as required.
        #
        # Older/live Supabase projects may not have optional columns like
        # metadata, owner_id, requester_id, priority, etc. The ticket should
        # still open cleanly and staff should not see a scary setup warning
        # just because optional analytics fields are missing.
        safe_payload = {k: payload[k] for k in TICKET_REQUIRED_COLUMNS if k in payload}

        try:
            sb.table("tickets").insert(safe_payload).execute()
            return ""
        except Exception as e:
            return f"{type(e).__name__}: {_short(e, 240)}"

    return await _to_thread(sync, "Could not write tickets row.")


async def _open_message(channel: discord.TextChannel, owner: discord.Member, row: Dict[str, Any]) -> None:
    embed = discord.Embed(
        title=f"🎫 {_row_name(row)} Ticket",
        description=f"{owner.mention}, staff will help you here.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Category", value=f"`{_row_slug(row)}`", inline=True)
    embed.add_field(name="Opened by", value=owner.mention, inline=True)

    view = None
    try:
        from ..tickets_new.panel import TicketChannelActionsView

        view = TicketChannelActionsView()
    except Exception as e:
        _warn(f"ticket action view unavailable: {type(e).__name__}: {_short(e, 220)}")

    try:
        await channel.send(
            content=owner.mention,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except TypeError:
        await channel.send(
            content=owner.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except Exception as e:
        _warn(f"open message failed channel={channel.id}: {type(e).__name__}: {_short(e, 220)}")


async def _ephemeral(
    i: discord.Interaction,
    content: str,
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: Dict[str, Any] = {
        "content": content,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if embed:
        payload["embed"] = embed
    if view:
        payload["view"] = view

    try:
        if i.response.is_done():
            await i.followup.send(**payload)
        else:
            await i.response.send_message(**payload)
    except Exception as e:
        _warn(
            "ephemeral reply failed "
            f"guild={getattr(getattr(i, 'guild', None), 'id', None)} "
            f"user={getattr(getattr(i, 'user', None), 'id', None)} "
            f"content={_short(content, 80)!r} error={type(e).__name__}: {_short(e, 220)}"
        )


async def _edit_or_reply(
    i: discord.Interaction,
    *,
    content: str,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        if i.response.is_done():
            await i.edit_original_response(content=content, embed=embed, view=view)
        else:
            await i.response.edit_message(content=content, embed=embed, view=view)
    except Exception:
        await _ephemeral(i, content, embed=embed, view=view)


async def _defer(i: discord.Interaction, thinking: bool = False) -> None:
    try:
        if not i.response.is_done():
            await i.response.defer(ephemeral=True, thinking=thinking)
    except Exception as e:
        _warn(
            "defer failed "
            f"guild={getattr(getattr(i, 'guild', None), 'id', None)} "
            f"user={getattr(getattr(i, 'user', None), 'id', None)} "
            f"error={type(e).__name__}: {_short(e, 220)}"
        )


async def _create_ticket(i: discord.Interaction, row: Dict[str, Any]) -> None:
    await _defer(i, True)

    guild = i.guild
    owner = i.user if isinstance(i.user, discord.Member) else None

    if guild is None or owner is None:
        return await _ephemeral(i, "❌ This must be used inside a server.")

    try:
        parent = await asyncio.wait_for(_active_category(guild), timeout=6.0)
    except asyncio.TimeoutError:
        _warn(f"active category lookup timed out guild={guild.id}")
        parent = None

    if parent is None:
        return await _ephemeral(i, "❌ Active Tickets category is not set. Run `/stoney setup` → **Run Health Check**.")

    missing = _missing_category_perms(parent, guild.me)
    if missing:
        return await _ephemeral(
            i,
            f"❌ I cannot create tickets in **{parent.name}**. Missing: {', '.join(missing)}. "
            "Run `/stoney setup` → **Run Health Check**.",
        )

    try:
        staff = await asyncio.wait_for(_staff_role(guild), timeout=6.0)
    except asyncio.TimeoutError:
        _warn(f"staff role lookup timed out guild={guild.id}")
        staff = None

    if staff is None:
        return await _ephemeral(i, "❌ Ticket staff role is not set. Run `/stoney setup` → **Run Health Check**.")

    try:
        existing = await asyncio.wait_for(_existing_open(guild, owner), timeout=6.0)
    except asyncio.TimeoutError:
        _warn(f"existing-open check timed out guild={guild.id} user={owner.id}; continuing")
        existing = None

    if existing:
        return await _ephemeral(i, f"You already have an open ticket: {existing.mention}")

    number = await _next_number(guild, parent)

    try:
        channel = await guild.create_text_channel(
            name=f"ticket-{number:04d}",
            category=parent,
            overwrites=_overwrites(guild, owner, guild.me, staff),
            topic=f"owner_id={owner.id};category={_row_slug(row)};ghost=false;ticket_number={number}",
            reason=f"Ticket opened by {owner} from category menu",
        )
    except Exception as e:
        _warn(f"discord channel create failed guild={guild.id} user={owner.id}: {type(e).__name__}: {_short(e, 220)}")
        return await _ephemeral(i, f"❌ Failed to create ticket in **{parent.name}**: `{type(e).__name__}: {_short(e, 220)}`")

    db_warning = await _insert_row(guild, owner, channel, row, number)
    await _open_message(channel, owner, row)

    if db_warning:
        _warn(f"ticket created but DB warning channel={channel.id}: {db_warning}")
        try:
            await channel.send(
                f"⚠️ Ticket opened, but database logging needs setup attention: `{_short(db_warning, 350)}`",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass

    await _ephemeral(i, f"✅ Ticket created: {channel.mention}")


def _category_embed(row: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title="Confirm Ticket Category",
        description=(
            f"You selected **{_row_name(row)}**.\n\n"
            "Press **Confirm** to open the ticket, or **Back** to choose a different category."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Category", value=f"`{_row_slug(row)}`", inline=True)
    embed.add_field(name="What this is for", value=_row_desc(row), inline=False)
    embed.set_footer(text="No ticket is created until you press Confirm.")
    return embed


class TicketConfirmView(discord.ui.View):
    def __init__(self, rows: List[Dict[str, Any]], row: Dict[str, Any]) -> None:
        super().__init__(timeout=1800)
        self.rows = rows
        self.row = row

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, i: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            await _create_ticket(i, self.row)
        except Exception as e:
            _warn(
                "ticket confirm crashed "
                f"guild={getattr(getattr(i, 'guild', None), 'id', None)} "
                f"user={getattr(getattr(i, 'user', None), 'id', None)} "
                f"error={type(e).__name__}: {_short(e, 220)}"
            )
            await _ephemeral(i, f"❌ Ticket creation failed: `{type(e).__name__}: {_short(e, 160)}`")

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back(self, i: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            embed = discord.Embed(
                title="Create Ticket",
                description="Choose the type of ticket you want to open.",
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="Pick a category. You can review it before anything is created.")
            await _edit_or_reply(i, content="Choose a ticket type.", embed=embed, view=TicketSelectView(self.rows))
        except Exception as e:
            _warn(f"ticket confirm back failed: {type(e).__name__}: {_short(e, 220)}")
            await _ephemeral(i, f"❌ Could not go back: `{type(e).__name__}: {_short(e, 160)}`")

    async def on_timeout(self) -> None:
        for item in self.children:
            try:
                item.disabled = True  # type: ignore[attr-defined]
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        _warn(
            "ticket confirm view error "
            f"guild={getattr(getattr(interaction, 'guild', None), 'id', None)} "
            f"user={getattr(getattr(interaction, 'user', None), 'id', None)} "
            f"item={type(item).__name__} error={type(error).__name__}: {_short(error, 220)}"
        )
        await _ephemeral(interaction, f"❌ Ticket confirmation failed: `{type(error).__name__}: {_short(error, 160)}`")


class TicketSelect(discord.ui.Select):
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        super().__init__(
            placeholder="Choose a ticket type",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=_row_name(r),
                    value=_row_slug(r),
                    description=_row_desc(r),
                    emoji="🎫",
                )
                for r in rows[:25]
            ],
        )

    async def callback(self, i: discord.Interaction) -> None:
        try:
            slug = _safe_str(self.values[0], "support")
            row = next((r for r in self.rows if _row_slug(r) == slug), {"slug": slug, "name": "Support"})
            await _edit_or_reply(
                i,
                content="Confirm this ticket type.",
                embed=_category_embed(row),
                view=TicketConfirmView(self.rows, row),
            )
        except Exception as e:
            _warn(
                "ticket type select crashed "
                f"guild={getattr(getattr(i, 'guild', None), 'id', None)} "
                f"user={getattr(getattr(i, 'user', None), 'id', None)} "
                f"values={getattr(self, 'values', None)} error={type(e).__name__}: {_short(e, 220)}"
            )
            await _ephemeral(i, f"❌ Ticket category selection failed: `{type(e).__name__}: {_short(e, 160)}`")


class TicketSelectView(discord.ui.View):
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        # Temporary user picker. The public panel itself is permanent.
        super().__init__(timeout=1800)
        self.add_item(TicketSelect(rows))

    async def on_timeout(self) -> None:
        for item in self.children:
            try:
                item.disabled = True  # type: ignore[attr-defined]
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        _warn(
            "ticket select view error "
            f"guild={getattr(getattr(interaction, 'guild', None), 'id', None)} "
            f"user={getattr(getattr(interaction, 'user', None), 'id', None)} "
            f"item={type(item).__name__} error={type(error).__name__}: {_short(error, 220)}"
        )
        await _ephemeral(interaction, f"❌ Ticket menu failed: `{type(error).__name__}: {_short(error, 160)}`")


async def _handle_panel_button(i: discord.Interaction) -> None:
    guild = i.guild
    member = i.user if isinstance(i.user, discord.Member) else None

    # ACK immediately. This prevents Discord's generic "interaction failed"
    # when Supabase/config checks are slow after a ticket close/archive.
    await _defer(i, True)

    try:
        if guild is None or member is None:
            return await _ephemeral(i, "❌ This must be used inside a server.")

        try:
            existing = await asyncio.wait_for(_existing_open(guild, member), timeout=6.0)
        except asyncio.TimeoutError:
            _warn(f"existing-open check timed out guild={guild.id} user={member.id}; continuing")
            existing = None

        if existing:
            return await _ephemeral(i, f"You already have an open ticket: {existing.mention}")

        try:
            rows, warning = await asyncio.wait_for(_load_rows(guild), timeout=6.0)
        except asyncio.TimeoutError:
            _warn(f"ticket category load timed out guild={guild.id}; using fallback categories")
            rows, warning = list(DEFAULT_ROWS), "Ticket category loading timed out; using fallback categories."

        embed = discord.Embed(
            title="Create Ticket",
            description="Choose the type of ticket you want to open.",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Pick a category. You can review it before anything is created.")

        if warning:
            embed.add_field(name="Setup Notice", value=_short(warning, 900), inline=False)

        await _ephemeral(i, "Choose a ticket type.", embed=embed, view=TicketSelectView(rows))
    except Exception as e:
        _warn(
            "public create ticket button crashed "
            f"guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)} "
            f"error={type(e).__name__}: {_short(e, 220)}"
        )
        await _ephemeral(i, f"❌ Ticket panel failed: `{type(e).__name__}: {_short(e, 160)}`")


class PublicCreateTicketPanelView(discord.ui.View):
    def __init__(self) -> None:
        # This is the permanent panel. It should never expire.
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.green,
        emoji="🎫",
        custom_id=PANEL_BUTTON_CUSTOM_ID,
    )
    async def create_ticket(self, i: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _handle_panel_button(i)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        _warn(
            "public ticket panel view error "
            f"guild={getattr(getattr(interaction, 'guild', None), 'id', None)} "
            f"user={getattr(getattr(interaction, 'user', None), 'id', None)} "
            f"item={type(item).__name__} error={type(error).__name__}: {_short(error, 220)}"
        )
        await _ephemeral(interaction, f"❌ Ticket panel failed: `{type(error).__name__}: {_short(error, 160)}`")


async def _component_fallback_listener(i: discord.Interaction) -> None:
    """Last-resort handler for the permanent panel button.

    If the persistent View registry misses the custom_id after a restart/reload,
    Discord otherwise shows "This interaction failed" with no useful log.
    This listener is in this same consolidated file, not a separate patch module.
    """
    try:
        if i.type is not discord.InteractionType.component:
            return

        data = i.data if isinstance(i.data, dict) else {}
        custom_id = _safe_str(data.get("custom_id"))
        if custom_id not in PANEL_BUTTON_CUSTOM_IDS:
            return

        # Give discord.py's persistent View dispatch a tiny chance to answer first.
        await asyncio.sleep(0.15)
        if i.response.is_done():
            return

        _warn(
            "persistent view missed Create Ticket button; fallback handled it "
            f"guild={getattr(getattr(i, 'guild', None), 'id', None)} "
            f"user={getattr(getattr(i, 'user', None), 'id', None)} custom_id={custom_id!r}"
        )
        await _handle_panel_button(i)
    except Exception as e:
        _warn(
            "panel fallback listener crashed "
            f"guild={getattr(getattr(i, 'guild', None), 'id', None)} "
            f"user={getattr(getattr(i, 'user', None), 'id', None)} "
            f"error={type(e).__name__}: {_short(e, 220)}"
        )
        try:
            await _ephemeral(i, f"❌ Ticket panel fallback failed: `{type(e).__name__}: {_short(e, 160)}`")
        except Exception:
            pass


def _panel_embed(guild: discord.Guild) -> discord.Embed:
    e = discord.Embed(
        title="🎫 Need help? Open a ticket",
        description=(
            "Press **Create Ticket** below, then pick the ticket type.\n\n"
            "No form first. No guessing. You can confirm the category before anything is created."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    e.add_field(
        name="How it works",
        value=(
            "1. Press **Create Ticket**\n"
            "2. Pick a ticket type\n"
            "3. Confirm or go back\n"
            "4. A private ticket channel opens"
        ),
        inline=False,
    )
    e.set_footer(text=f"{guild.name} • Stoney Verify ticket panel • category-menu")
    return e


async def _post_panel(i: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    if not _staff_check(i):
        return await reply_once(i, {"content": "❌ Staff only.", "ephemeral": True})

    guild = i.guild
    if guild is None:
        return await reply_once(i, {"content": "❌ Guild only.", "ephemeral": True})

    await _defer(i)

    target = channel or await _panel_channel(guild) or (i.channel if isinstance(i.channel, discord.TextChannel) else None)
    if target is None:
        return await reply_once(i, {"content": "❌ I could not find a text channel to post the ticket panel.", "ephemeral": True})

    missing = _missing_text_perms(target, guild.me)
    if missing:
        return await reply_once(
            i,
            {"content": f"❌ I cannot post in {target.mention}. Missing: {', '.join(missing)}.", "ephemeral": True},
        )

    try:
        msg = await target.send(
            embed=_panel_embed(guild),
            view=PublicCreateTicketPanelView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as e:
        return await reply_once(
            i,
            {
                "content": f"❌ Failed posting ticket panel in {target.mention}: `{type(e).__name__}: {_short(e, 220)}`",
                "ephemeral": True,
            },
        )

    try:
        from .public_setup_config_writer import upsert_guild_config
        from ..guild_config import invalidate_guild_config

        await upsert_guild_config(
            guild.id,
            {"ticket_panel_channel_id": str(target.id), "ticket_panel_message_id": str(msg.id)},
        )
        invalidate_guild_config(guild.id)
    except Exception as e:
        _warn(f"saving panel config failed guild={guild.id}: {type(e).__name__}: {_short(e, 220)}")

    await reply_once(
        i,
        {"content": f"✅ Posted the public **category-menu Create Ticket** panel in {target.mention}.", "ephemeral": True},
    )


def _ticket_panel_group() -> app_commands.Group:
    group = app_commands.Group(name="ticket-panel", description="Manage and post ticket panels.")

    @group.command(name="post", description="Post the public category-menu Create Ticket panel.")
    @app_commands.describe(channel="Optional channel. Defaults to saved support/ticket-panel channel, then current channel.")
    async def post(i: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        await _post_panel(i, channel)

    return group


async def _table_probe(table: str, columns: Sequence[str]) -> Tuple[bool, str]:
    sb = _sb()
    if sb is None:
        return False, "Supabase client unavailable"

    select_expr = ",".join(columns)

    def sync() -> Tuple[bool, str]:
        try:
            sb.table(table).select(select_expr).limit(1).execute()
            return True, "ok"
        except Exception as e:
            return False, f"{type(e).__name__}: {_short(e, 260)}"

    return await _to_thread(sync, (False, "unknown error"))


async def _health_lines(guild: discord.Guild) -> Tuple[List[str], List[str], List[str]]:
    blockers: List[str] = []
    warnings: List[str] = []
    ok: List[str] = []

    active = await _active_category(guild)
    if not active:
        blockers.append("Active Tickets category is not set. Use `/stoney setup` → Ticket Basics.")
    else:
        m = _missing_category_perms(active, guild.me)
        (blockers if m else ok).append(f"Active Tickets category {'missing: ' + ', '.join(m) if m else 'ready'}: {active.mention}.")

    archive = await _archive_category(guild)
    if not archive:
        warnings.append("Archive category is not set. Closed ticket archiving may fail.")
    else:
        m = _missing_category_perms(archive, guild.me)
        (warnings if m else ok).append(f"Archive category {'missing: ' + ', '.join(m) if m else 'ready'}: {archive.mention}.")

    panel = await _panel_channel(guild)
    if not panel:
        warnings.append("Ticket panel channel is not saved. Use `/ticket-panel post` in the support channel.")
    else:
        m = _missing_text_perms(panel, guild.me)
        (blockers if m else ok).append(f"Ticket panel channel {'missing: ' + ', '.join(m) if m else 'ready'}: {panel.mention}.")

    transcripts = await _transcript_channel(guild)
    if not transcripts:
        warnings.append("Transcripts channel is not set. Close/delete transcripts may not post.")
    else:
        m = _missing_text_perms(transcripts, guild.me)
        (warnings if m else ok).append(f"Transcripts channel {'missing: ' + ', '.join(m) if m else 'ready'}: {transcripts.mention}.")

    staff = await _staff_role(guild)
    if not staff:
        blockers.append("Ticket staff role is not set. Use `/stoney setup` → Ticket Basics.")
    else:
        ok.append(f"Ticket staff role ready: {staff.mention}.")

    cat_ok, cat_msg = await _table_probe("ticket_categories", TICKET_CATEGORY_REQUIRED_COLUMNS)
    tic_ok, tic_msg = await _table_probe("tickets", TICKET_REQUIRED_COLUMNS)

    if cat_ok:
        ok.append("Supabase `ticket_categories` table has required menu columns.")
    else:
        blockers.append(f"Supabase `ticket_categories` missing table/required columns: {cat_msg}")

    if tic_ok:
        ok.append("Supabase `tickets` table has required ticket columns.")
    else:
        blockers.append(f"Supabase `tickets` missing table/required columns: {tic_msg}")

    if (not cat_ok or not tic_ok) and not _db_url_present():
        warnings.append(
            "Auto-create/repair missing tables requires `SUPABASE_DB_URL` or `DATABASE_URL` in Discloud. "
            "GitHub/Supabase integration alone does not provide that runtime URL."
        )

    return blockers, warnings, ok


def _field(lines: List[str], empty: str) -> str:
    return empty if not lines else "\n".join(f"• {x}" for x in lines)[:1024]


def _patch_health() -> None:
    global _HEALTH_PATCHED

    if _HEALTH_PATCHED:
        return

    try:
        from . import public_setup_solid as solid

        original = getattr(solid, "_build_health_embed", None)
        if not callable(original) or getattr(original, "_ticket_panel_clean_wrapped", False):
            _HEALTH_PATCHED = True
            return

        async def wrapped(guild: discord.Guild) -> discord.Embed:
            embed = await original(guild)
            b, w, ok = await _health_lines(guild)

            embed.add_field(name="Ticket Creation Blockers", value=_field(b, "✅ None"), inline=False)
            embed.add_field(name="Ticket Creation Warnings", value=_field(w, "✅ None"), inline=False)
            embed.add_field(name="Ticket Creation Passing", value=_field(ok[:8], "No passing checks."), inline=False)

            if b:
                embed.color = discord.Color.red()
                embed.description = "🚫 **Fix the blockers first.** Ticket creation is not ready."

            return embed

        setattr(wrapped, "_ticket_panel_clean_wrapped", True)
        setattr(solid, "_build_health_embed", wrapped)
        _HEALTH_PATCHED = True
        _log("patched /stoney setup health check")
    except Exception as e:
        _warn(f"could not patch health check: {e!r}")


def register_public_ticket_panel_clean(bot: Any, tree: Any) -> None:
    global _PANEL_VIEW_REGISTERED, _PANEL_GROUP_REGISTERED, _PANEL_FALLBACK_LISTENER_REGISTERED

    if not _PANEL_VIEW_REGISTERED:
        try:
            bot.add_view(PublicCreateTicketPanelView())
            _PANEL_VIEW_REGISTERED = True
            _log(f"registered persistent Create Ticket view custom_id={PANEL_BUTTON_CUSTOM_ID}")
        except Exception as e:
            _warn(f"could not register persistent view: {e!r}")

    if not _PANEL_FALLBACK_LISTENER_REGISTERED:
        try:
            bot.add_listener(_component_fallback_listener, "on_interaction")
            _PANEL_FALLBACK_LISTENER_REGISTERED = True
            _log("registered Create Ticket component fallback listener")
        except Exception as e:
            _warn(f"could not register Create Ticket fallback listener: {e!r}")

    if not _PANEL_GROUP_REGISTERED:
        try:
            if tree.get_command("ticket-panel", guild=None) is not None:
                tree.remove_command("ticket-panel", guild=None)
        except Exception:
            pass

        try:
            tree.add_command(_ticket_panel_group())
            _PANEL_GROUP_REGISTERED = True
            _log("registered clean /ticket-panel post")
        except Exception as e:
            _warn(f"could not register /ticket-panel post: {e!r}")

    _patch_health()


__all__ = ["register_public_ticket_panel_clean"]
