from __future__ import annotations

"""Clean public ticket panel flow.

Single native owner for `/ticket-panel post`, `/ticket-panel health`, and the
public Create Ticket button. No runtime patch modules are required for ticket
creation.
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
_CREATE_LOCKS: Dict[Tuple[int, int], asyncio.Lock] = {}
_NUMBER_LOCKS: Dict[int, asyncio.Lock] = {}

PANEL_BUTTON_CUSTOM_ID = "sv:ticket:panel:create:clean:v1"
PANEL_BUTTON_CUSTOM_IDS = {PANEL_BUTTON_CUSTOM_ID}

DEFAULT_ROWS: Tuple[Dict[str, Any], ...] = (
    {"slug": "verification", "name": "Verification", "description": "Help with verification or approval issues.", "sort_order": 10},
    {"slug": "support", "name": "Support", "description": "General help from staff.", "sort_order": 20, "is_default": True},
    {"slug": "report", "name": "Report a Member", "description": "Report a member or server issue.", "sort_order": 30},
    {"slug": "appeal", "name": "Appeal", "description": "Appeal a moderation action or access restriction.", "sort_order": 40},
    {"slug": "bug", "name": "Bug Report", "description": "Report a bot or server workflow issue.", "sort_order": 50},
    {"slug": "question", "name": "Other Question", "description": "Ask something that does not fit the other options.", "sort_order": 60},
)

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
    return any(_safe_str(os.getenv(k)) for k in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"))


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
    ch = _channel(guild, _cfg_get(config, "ticket_category_id", "active_ticket_category_id", "ticket_active_category_id", "open_ticket_category_id"))
    if isinstance(ch, discord.CategoryChannel):
        return ch
    for c in guild.categories:
        if "active" in c.name.lower() and "ticket" in c.name.lower():
            return c
    return None


async def _archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    config = await _cfg(guild.id)
    ch = _channel(guild, _cfg_get(config, "ticket_archive_category_id", "archive_ticket_category_id", "ticket_archived_category_id"))
    if isinstance(ch, discord.CategoryChannel):
        return ch
    for c in guild.categories:
        if "archive" in c.name.lower() and "ticket" in c.name.lower():
            return c
    return None


async def _panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    config = await _cfg(guild.id)
    ch = _channel(guild, _cfg_get(config, "ticket_panel_channel_id", "support_channel_id", "ticket_support_channel_id"))
    return ch if isinstance(ch, discord.TextChannel) else None


async def _transcript_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    config = await _cfg(guild.id)
    ch = _channel(guild, _cfg_get(config, "transcripts_channel_id", "ticket_transcripts_channel_id", "transcript_channel_id"))
    return ch if isinstance(ch, discord.TextChannel) else None


async def _staff_role(guild: discord.Guild) -> Optional[discord.Role]:
    config = await _cfg(guild.id)
    rid = _safe_int(_cfg_get(config, "staff_role_id", "ticket_staff_role_id", "support_role_id", "vc_staff_role_id"), 0)
    return guild.get_role(rid) if rid > 0 else None


def _has_perm(perms: discord.Permissions, *names: str) -> bool:
    for name in names:
        try:
            if bool(getattr(perms, name, False)):
                return True
        except Exception:
            continue
    return False


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
        checks += [("Manage Channels", p.manage_channels), ("Manage Permissions", _has_perm(p, "manage_roles", "manage_permissions"))]
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
        ("Manage Permissions", _has_perm(p, "manage_roles", "manage_permissions")),
    ]
    return [n for n, ok in checks if not ok]


def _ticket_category_shape_blockers(cat: discord.CategoryChannel, staff: Optional[discord.Role]) -> List[str]:
    blockers: List[str] = []
    try:
        default_ow = cat.overwrites_for(cat.guild.default_role)
        if default_ow.view_channel is not False:
            blockers.append("Active Tickets category must deny @everyone View Channel so new tickets stay private.")
    except Exception:
        blockers.append("Could not inspect @everyone permissions on the Active Tickets category.")
    if staff is not None:
        try:
            staff_ow = cat.overwrites_for(staff)
            if staff_ow.view_channel is not True and not staff.permissions.administrator:
                blockers.append(f"Ticket staff role {staff.mention} must be allowed View Channel on the Active Tickets category.")
        except Exception:
            blockers.append("Could not inspect staff permissions on the Active Tickets category.")
    return blockers


def _row_slug(row: Dict[str, Any]) -> str:
    return _slug(row.get("slug") or row.get("category_slug") or row.get("name") or row.get("title") or "support")


def _canon_key(raw: Any) -> str:
    text = _slug(raw)
    if "verify" in text or "verification" in text:
        return "verification"
    if "support" in text or "help" in text or "general" in text:
        return "support"
    if "report" in text:
        return "report"
    if "appeal" in text or "ban" in text or "mute" in text or "timeout" in text:
        return "appeal"
    if "bug" in text or "technical" in text or "issue" in text:
        return "bug"
    if "question" in text or "other" in text or "custom" in text:
        return "question"
    return text or "support"


def _row_name(row: Dict[str, Any]) -> str:
    raw = _safe_str(row.get("button_label") or row.get("name") or row.get("display_name") or row.get("title") or _row_slug(row), "Support")
    key = _canon_key(f"{_row_slug(row)} {raw}")
    labels = {"verification": "Verification", "support": "Support", "report": "Report a Member", "appeal": "Appeal", "bug": "Bug Report", "question": "Other Question"}
    return labels.get(key, raw[:100])


def _row_desc(row: Dict[str, Any]) -> str:
    raw = _safe_str(row.get("description") or row.get("intake_type") or "", "")
    key = _canon_key(f"{_row_slug(row)} {_row_name(row)}")
    descriptions = {
        "verification": "Help with verification or approval issues.",
        "support": "General help from staff.",
        "report": "Report a member or server issue.",
        "appeal": "Appeal a moderation action or access restriction.",
        "bug": "Report a bot or server workflow issue.",
        "question": "Ask something that does not fit the other options.",
    }
    return descriptions.get(key, raw[:100] if raw else "Open a support ticket.")


def _row_sort(row: Dict[str, Any]) -> int:
    return _safe_int(row.get("sort_order", row.get("position", 999)), 999)


def _canon(row: Dict[str, Any]) -> str:
    return _canon_key(f"{_row_slug(row)} {_safe_str(row.get('name') or row.get('title') or '')}")


def _rows(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return [dict(x) for x in DEFAULT_ROWS]
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("is_enabled") is False or item.get("enabled") is False:
            continue
        row = dict(item)
        key = _canon(row)
        if key in seen:
            continue
        seen.add(key)
        row["slug"] = _row_slug(row)
        row["name"] = _row_name(row)
        row["description"] = _row_desc(row)
        out.append(row)
    if not out:
        out = [dict(x) for x in DEFAULT_ROWS]
    return sorted(out, key=lambda r: (_row_sort(r), _row_name(r).lower()))[:25]


async def _load_rows(guild: discord.Guild) -> Tuple[List[Dict[str, Any]], str]:
    sb = _sb()
    if sb is None:
        return [dict(x) for x in DEFAULT_ROWS], "Using default ticket categories because Supabase is unavailable."

    def sync() -> Tuple[List[Dict[str, Any]], str]:
        try:
            resp = sb.table("ticket_categories").select("*").eq("guild_id", str(guild.id)).order("sort_order").execute()
            return _rows(getattr(resp, "data", None) or []), ""
        except Exception as e:
            return [dict(x) for x in DEFAULT_ROWS], f"Using default ticket categories because `ticket_categories` could not be read: {type(e).__name__}: {_short(e, 220)}"

    return await _to_thread(sync, ([dict(x) for x in DEFAULT_ROWS], "Using default ticket categories because loading failed."))


def _ticket_number_from_channel(ch: discord.TextChannel) -> int:
    try:
        m = re.match(r"^(?:ticket|closed)-(\d+)$", str(ch.name or ""), re.I)
        if m:
            return _safe_int(m.group(1), 0)
        m2 = re.search(r"(?:^|[;\s])ticket_number=(\d+)(?:$|[;\s])", str(ch.topic or ""))
        if m2:
            return _safe_int(m2.group(1), 0)
    except Exception:
        return 0
    return 0


async def _db_max_ticket_number(guild: discord.Guild) -> int:
    sb = _sb()
    if sb is None:
        return 0

    def sync() -> int:
        try:
            resp = sb.table("tickets").select("ticket_number").eq("guild_id", str(guild.id)).order("ticket_number", desc=True).limit(1).execute()
            rows = getattr(resp, "data", None) or []
            return _safe_int(rows[0].get("ticket_number"), 0) if rows else 0
        except Exception:
            return 0

    return await _to_thread(sync, 0)


async def _next_number(guild: discord.Guild, parent: Optional[discord.CategoryChannel]) -> int:
    lock = _NUMBER_LOCKS.get(int(guild.id))
    if lock is None:
        lock = asyncio.Lock()
        _NUMBER_LOCKS[int(guild.id)] = lock
    async with lock:
        max_num = await _db_max_ticket_number(guild)
        candidates: List[discord.TextChannel] = []
        if parent is not None:
            candidates.extend(list(parent.text_channels))
        candidates.extend(list(guild.text_channels))
        for ch in candidates:
            if isinstance(ch, discord.TextChannel):
                max_num = max(max_num, _ticket_number_from_channel(ch))
        return max_num + 1


def _channel_is_closed_like(ch: discord.TextChannel) -> bool:
    name = str(ch.name or "").lower()
    topic = str(ch.topic or "").lower()
    return name.startswith("closed-") or "status=closed" in topic or "status=deleted" in topic


def _topic_owner_id(topic: Any) -> int:
    m = re.search(r"(?:^|[;\s])owner_id=(\d+)(?:$|[;\s])", _safe_str(topic))
    return _safe_int(m.group(1), 0) if m else 0


async def _existing_open_from_db(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    sb = _sb()
    if sb is None:
        return None

    def sync() -> List[Dict[str, Any]]:
        try:
            resp = sb.table("tickets").select("channel_id,discord_thread_id,status,user_id").eq("guild_id", str(guild.id)).eq("user_id", str(member.id)).limit(10).execute()
            return list(getattr(resp, "data", None) or [])
        except Exception:
            return []

    for row in await _to_thread(sync, []):
        if not isinstance(row, dict):
            continue
        if _safe_str(row.get("status"), "open").lower() not in {"open", "claimed", "active"}:
            continue
        cid = _safe_int(row.get("channel_id") or row.get("discord_thread_id"), 0)
        ch = guild.get_channel(cid) if cid > 0 else None
        if isinstance(ch, discord.TextChannel) and not _channel_is_closed_like(ch):
            return ch
    return None


async def _existing_open_from_channels(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    parent = await _active_category(guild)
    candidates: List[discord.TextChannel] = []
    if isinstance(parent, discord.CategoryChannel):
        candidates.extend(list(parent.text_channels))
    candidates.extend(list(guild.text_channels))
    seen: set[int] = set()
    for ch in candidates:
        if not isinstance(ch, discord.TextChannel) or int(ch.id) in seen:
            continue
        seen.add(int(ch.id))
        if _channel_is_closed_like(ch) or not str(ch.name or "").lower().startswith("ticket-"):
            continue
        if _topic_owner_id(getattr(ch, "topic", None)) == int(member.id):
            return ch
    return None


async def _existing_open(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    found = await _existing_open_from_db(guild, member)
    return found or await _existing_open_from_channels(guild, member)


def _owner_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)


def _ticket_insert_payload(guild: discord.Guild, owner: discord.Member, channel: discord.TextChannel, row: Dict[str, Any], number: int) -> Dict[str, Any]:
    return {
        "guild_id": str(guild.id),
        "user_id": str(owner.id),
        "username": str(owner),
        "title": _row_name(row),
        "category": _row_slug(row),
        "status": "open",
        "channel_id": str(channel.id),
        "discord_thread_id": str(channel.id),
        "ticket_number": int(number),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


async def _insert_row(guild: discord.Guild, owner: discord.Member, channel: discord.TextChannel, row: Dict[str, Any], number: int) -> str:
    sb = _sb()
    if sb is None:
        return "Supabase client unavailable."
    payload = _ticket_insert_payload(guild, owner, channel, row, number)

    def sync() -> str:
        try:
            sb.table("tickets").insert({k: payload[k] for k in TICKET_REQUIRED_COLUMNS if k in payload}).execute()
            return ""
        except Exception as e:
            return f"{type(e).__name__}: {_short(e, 240)}"

    return await _to_thread(sync, "Could not write tickets row.")


async def _maybe_post_verification_panel(channel: discord.TextChannel, owner: discord.Member, row: Dict[str, Any]) -> str:
    if _canon(row) != "verification":
        return ""
    try:
        from ..startup_guards import unverified_ticket_panel_flow as verify_flow
        if not await verify_flow._is_unverified_only_member(owner):
            return ""
        cfg = await verify_flow._get_guild_config_safe(channel.guild.id)
        vc_locked, vc_message = await verify_flow._ensure_configured_vc_verify_locked(channel.guild, cfg)
        if not vc_locked:
            return f"Verification panel was not posted because VC verification setup is not safe yet. Reason: {vc_message}."
        return "" if await verify_flow._post_verify_ui(channel, owner) else "Verification panel failed to post."
    except Exception as e:
        _warn(f"verification panel failed channel={channel.id} user={owner.id}: {type(e).__name__}: {_short(e, 220)}")
        return "Verification panel failed to post."


async def _open_message(channel: discord.TextChannel, owner: discord.Member, row: Dict[str, Any]) -> None:
    embed = discord.Embed(title=f"🎫 {_row_name(row)} Ticket", description=f"{owner.mention}, staff will help you here.", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Category", value=f"`{_row_slug(row)}`", inline=True)
    embed.add_field(name="Opened by", value=owner.mention, inline=True)
    view = None
    try:
        from ..tickets_new.panel import TicketChannelActionsView
        view = TicketChannelActionsView()
    except Exception as e:
        _warn(f"ticket action view unavailable: {type(e).__name__}: {_short(e, 220)}")
    try:
        await channel.send(content=owner.mention, embed=embed, view=view, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    except TypeError:
        await channel.send(content=owner.mention, embed=embed, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    except Exception as e:
        _warn(f"open message failed channel={channel.id}: {type(e).__name__}: {_short(e, 220)}")


async def _ephemeral(i: discord.Interaction, content: str, *, embed: Optional[discord.Embed] = None, view: Optional[discord.ui.View] = None) -> None:
    payload: Dict[str, Any] = {"content": content, "ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
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
        _warn(f"ephemeral reply failed content={_short(content, 80)!r} error={type(e).__name__}: {_short(e, 220)}")


async def _edit_or_reply(i: discord.Interaction, *, content: str, embed: Optional[discord.Embed] = None, view: Optional[discord.ui.View] = None) -> None:
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
        _warn(f"defer failed error={type(e).__name__}: {_short(e, 220)}")


async def _create_synced_ticket_channel(guild: discord.Guild, owner: discord.Member, parent: discord.CategoryChannel, row: Dict[str, Any], number: int) -> discord.TextChannel:
    """Create under the category and add only the requester-specific access.

    Do not add staff-role or bot-member overwrites here. Staff and bot access must
    come from the Active Tickets category or Administrator. Adding unnecessary
    bot/staff overwrites can trigger Discord 50013 after the channel is created.
    """
    channel = await guild.create_text_channel(
        name=f"ticket-{number:04d}",
        category=parent,
        topic=f"owner_id={owner.id};category={_row_slug(row)};ghost=false;ticket_number={number}",
        reason=f"Ticket opened by {owner} from category menu",
    )
    try:
        await channel.set_permissions(owner, overwrite=_owner_overwrite(), reason="Ticket owner access")
    except Exception:
        try:
            await channel.delete(reason="Ticket owner permission setup failed after channel create")
        except Exception:
            pass
        raise
    return channel


async def _create_ticket(i: discord.Interaction, row: Dict[str, Any]) -> None:
    await _defer(i, True)
    guild = i.guild
    owner = i.user if isinstance(i.user, discord.Member) else None
    if guild is None or owner is None:
        return await _ephemeral(i, "❌ This must be used inside a server.")
    lock_key = (int(guild.id), int(owner.id))
    lock = _CREATE_LOCKS.get(lock_key) or asyncio.Lock()
    _CREATE_LOCKS[lock_key] = lock
    async with lock:
        try:
            parent = await asyncio.wait_for(_active_category(guild), timeout=6.0)
        except asyncio.TimeoutError:
            parent = None
        if parent is None:
            return await _ephemeral(i, "❌ Active Tickets category is not set. Run `/dank setup` → **Setup Check**.")
        try:
            staff = await asyncio.wait_for(_staff_role(guild), timeout=6.0)
        except asyncio.TimeoutError:
            staff = None
        missing = _missing_category_perms(parent, guild.me)
        shape_blockers = _ticket_category_shape_blockers(parent, staff)
        if missing or shape_blockers:
            details: List[str] = []
            if missing:
                details.append(f"Missing bot permissions: {', '.join(missing)}")
            details.extend(shape_blockers)
            return await _ephemeral(i, f"❌ I cannot safely create tickets in **{parent.name}**. {' '.join(details)} Run `/dank setup` → **Setup Check**, or fix the category permissions.")
        if staff is None:
            return await _ephemeral(i, "❌ Ticket staff role is not set. Run `/dank setup` → **Setup Check**.")
        try:
            existing = await asyncio.wait_for(_existing_open(guild, owner), timeout=6.0)
        except asyncio.TimeoutError:
            existing = None
        if existing:
            return await _ephemeral(i, f"You already have an open ticket: {existing.mention}")
        number = await _next_number(guild, parent)
        try:
            channel = await _create_synced_ticket_channel(guild, owner, parent, row, number)
        except discord.Forbidden as e:
            _warn(f"discord ticket setup forbidden guild={guild.id} user={owner.id}: {_short(e, 300)}")
            return await _ephemeral(i, f"❌ Discord denied ticket setup in **{parent.name}**. Exact error: `{_short(e, 240)}`. If the bot was already installed, re-inviting with Administrator may not update the existing bot role; manually check the Dank Shield role permissions and role position.")
        except Exception as e:
            _warn(f"discord ticket setup failed guild={guild.id} user={owner.id}: {type(e).__name__}: {_short(e, 220)}")
            return await _ephemeral(i, f"❌ Failed to create ticket in **{parent.name}**: `{type(e).__name__}: {_short(e, 220)}`")
        db_warning = await _insert_row(guild, owner, channel, row, number)
        await _open_message(channel, owner, row)
        verify_warning = await _maybe_post_verification_panel(channel, owner, row)
        if db_warning:
            _warn(f"ticket created but DB logging warning channel={channel.id}: {db_warning}")
        if verify_warning:
            _warn(f"ticket created but verification panel warning channel={channel.id}: {verify_warning}")
            try:
                await channel.send(f"⚠️ Verification setup needs attention: `{_short(verify_warning, 500)}`", allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                pass
            return await _ephemeral(i, f"⚠️ Ticket created: {channel.mention}\nVerification setup needs attention. Staff can see details inside the ticket.")
        return await _ephemeral(i, f"✅ Ticket created: {channel.mention}")


def _category_embed(row: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title="Confirm Ticket Category", description=f"You selected **{_row_name(row)}**.\n\nPress **Confirm** to open the ticket, or **Back** to choose a different category.", color=discord.Color.blurple())
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
        await _create_ticket(i, self.row)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="↩️")
    async def back(self, i: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        embed = discord.Embed(title="Create Ticket", description="Choose the type of ticket you want to open.", color=discord.Color.blurple())
        embed.set_footer(text="Pick a category. You can review it before anything is created.")
        await _edit_or_reply(i, content="Choose a ticket type.", embed=embed, view=TicketSelectView(self.rows))


class TicketSelect(discord.ui.Select):
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        super().__init__(placeholder="Choose a ticket type", min_values=1, max_values=1, options=[discord.SelectOption(label=_row_name(r), value=_row_slug(r), description=_row_desc(r), emoji="🎫") for r in rows[:25]])

    async def callback(self, i: discord.Interaction) -> None:
        slug = _safe_str(self.values[0], "support")
        row = next((r for r in self.rows if _row_slug(r) == slug), {"slug": slug, "name": "Support"})
        await _edit_or_reply(i, content="Confirm this ticket type.", embed=_category_embed(row), view=TicketConfirmView(self.rows, row))


class TicketSelectView(discord.ui.View):
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        super().__init__(timeout=1800)
        self.add_item(TicketSelect(rows))


async def _handle_panel_button(i: discord.Interaction) -> None:
    guild = i.guild
    member = i.user if isinstance(i.user, discord.Member) else None
    await _defer(i, True)
    if guild is None or member is None:
        return await _ephemeral(i, "❌ This must be used inside a server.")
    try:
        existing = await asyncio.wait_for(_existing_open(guild, member), timeout=6.0)
    except asyncio.TimeoutError:
        existing = None
    if existing:
        return await _ephemeral(i, f"You already have an open ticket: {existing.mention}")
    try:
        rows, warning = await asyncio.wait_for(_load_rows(guild), timeout=6.0)
    except asyncio.TimeoutError:
        rows, warning = [dict(x) for x in DEFAULT_ROWS], "Ticket category loading timed out; using fallback categories."
    embed = discord.Embed(title="Create Ticket", description="Choose the type of ticket you want to open.", color=discord.Color.blurple())
    embed.set_footer(text="Pick a category. You can review it before anything is created.")
    if warning:
        embed.add_field(name="Setup Notice", value=_short(warning, 900), inline=False)
    await _ephemeral(i, "Choose a ticket type.", embed=embed, view=TicketSelectView(rows))


class PublicCreateTicketPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.green, emoji="🎫", custom_id=PANEL_BUTTON_CUSTOM_ID)
    async def create_ticket(self, i: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _handle_panel_button(i)


async def _component_fallback_listener(i: discord.Interaction) -> None:
    try:
        if i.type is not discord.InteractionType.component:
            return
        data = i.data if isinstance(i.data, dict) else {}
        custom_id = _safe_str(data.get("custom_id"))
        if custom_id not in PANEL_BUTTON_CUSTOM_IDS:
            return
        await asyncio.sleep(0.15)
        if i.response.is_done():
            return
        _warn("persistent view missed Create Ticket button; fallback handled it")
        await _handle_panel_button(i)
    except Exception as e:
        _warn(f"panel fallback listener crashed: {type(e).__name__}: {_short(e, 220)}")


def _panel_embed(guild: discord.Guild) -> discord.Embed:
    e = discord.Embed(title="🎫 Need help? Open a ticket", description="Press **Create Ticket** below, then pick the ticket type.\n\nNo form first. No guessing. You can confirm the category before anything is created.", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
    e.add_field(name="How it works", value="1. Press **Create Ticket**\n2. Pick a ticket type\n3. Confirm or go back\n4. A private ticket channel opens", inline=False)
    e.set_footer(text=f"{guild.name} • Dank Shield ticket panel • category-menu")
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
        return await reply_once(i, {"content": f"❌ I cannot post in {target.mention}. Missing: {', '.join(missing)}.", "ephemeral": True})
    try:
        msg = await target.send(embed=_panel_embed(guild), view=PublicCreateTicketPanelView(), allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        return await reply_once(i, {"content": f"❌ Failed posting ticket panel in {target.mention}: `{type(e).__name__}: {_short(e, 220)}`", "ephemeral": True})
    try:
        from .public_setup_config_writer import upsert_guild_config
        from ..guild_config import invalidate_guild_config
        await upsert_guild_config(guild.id, {"ticket_panel_channel_id": str(target.id), "ticket_panel_message_id": str(msg.id)})
        invalidate_guild_config(guild.id)
    except Exception as e:
        _warn(f"saving panel config failed guild={guild.id}: {type(e).__name__}: {_short(e, 220)}")
    await reply_once(i, {"content": f"✅ Posted the public **category-menu Create Ticket** panel in {target.mention}.", "ephemeral": True})


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
    staff = await _staff_role(guild)
    if not active:
        blockers.append("Active Tickets category is not set. Use `/dank setup` → Ticket Basics.")
    else:
        m = _missing_category_perms(active, guild.me)
        shape = _ticket_category_shape_blockers(active, staff)
        if m:
            blockers.append(f"Active Tickets category missing: {', '.join(m)}: {active.mention}.")
        else:
            ok.append(f"Active Tickets category has required bot permissions: {active.mention}.")
        blockers.extend(shape)
        if not shape:
            ok.append("Active Tickets category privacy/staff shape looks ready.")
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
    if not staff:
        blockers.append("Ticket staff role is not set. Use `/dank setup` → Ticket Basics.")
    else:
        ok.append(f"Ticket staff role configured: {staff.mention}.")
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
        warnings.append("Auto-create/repair missing tables requires `SUPABASE_DB_URL` or `DATABASE_URL` in Discloud.")
    return blockers, warnings, ok


def _field(lines: List[str], empty: str) -> str:
    return empty if not lines else "\n".join(f"• {x}" for x in lines)[:1024]


async def _send_health(i: discord.Interaction) -> None:
    if not _staff_check(i):
        return await reply_once(i, {"content": "❌ Staff only.", "ephemeral": True})
    guild = i.guild
    if guild is None:
        return await reply_once(i, {"content": "❌ Guild only.", "ephemeral": True})
    await _defer(i, True)
    blockers, warnings, ok = await _health_lines(guild)
    embed = discord.Embed(title="🩺 Ticket Panel Health", description="🚫 Fix the blockers first." if blockers else "✅ Ticket panel creation path looks ready.", color=discord.Color.red() if blockers else discord.Color.gold() if warnings else discord.Color.green(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Blockers", value=_field(blockers, "✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field(warnings, "✅ None"), inline=False)
    embed.add_field(name="Passing", value=_field(ok[:10], "No passing checks."), inline=False)
    await _ephemeral(i, "Ticket panel health check complete.", embed=embed)


def _ticket_panel_group() -> app_commands.Group:
    group = app_commands.Group(name="ticket-panel", description="Manage and post ticket panels.")

    @group.command(name="post", description="Post the public category-menu Create Ticket panel.")
    @app_commands.describe(channel="Optional channel. Defaults to saved support/ticket-panel channel, then current channel.")
    async def post(i: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        await _post_panel(i, channel)

    @group.command(name="health", description="Check ticket panel creation permissions and setup.")
    async def health(i: discord.Interaction) -> None:
        await _send_health(i)

    return group


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
            _log("registered clean /ticket-panel post and /ticket-panel health")
        except Exception as e:
            _warn(f"could not register /ticket-panel commands: {e!r}")


__all__ = ["register_public_ticket_panel_clean"]
