from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

import discord

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase
from .repository import (
    add_internal_note as repo_add_internal_note,
    get_ticket_by_any_channel_id,
)

try:
    from .event_service import (
        log_ticket_event,
        log_ticket_closed,
    )
except Exception:
    async def log_ticket_event(*args, **kwargs):  # type: ignore
        return False

    async def log_ticket_closed(*args, **kwargs):  # type: ignore
        return False

try:
    from .service import mark_ticket_closed as service_mark_ticket_closed
except Exception:
    service_mark_ticket_closed = None  # type: ignore


# ============================================================
# tickets_new/macros_service.py
# ------------------------------------------------------------
# Purpose:
# - centralize ticket macro loading + resolution
# - support default macros, global-config macros, and DB macros
# - render placeholders safely and consistently
# - keep slash-command macros and panel macros on one shared path
# - block macro sends into closed/deleted tickets at the service layer
# - avoid duplicate send races when staff spam-click macro actions
# - respect archive-category closed state, not just closed-* names
# - optionally support close_after_send on the shared service path
# ============================================================

MACROS_TABLE = "ticket_macros"

_DEFAULT_ALLOWED_PLACEHOLDERS = {
    # user / owner
    "user_mention",
    "user_name",
    "user_display",
    "user_id",
    "owner_mention",
    "owner_name",
    "owner_display",
    "owner_id",
    # staff / assignee
    "staff_mention",
    "staff_name",
    "staff_display",
    "staff_id",
    "assignee_mention",
    "assignee_name",
    "assignee_display",
    "assignee_id",
    # channel / ticket
    "channel_mention",
    "channel_name",
    "channel_id",
    "ticket_id",
    "ticket_number",
    "ticket_title",
    "category",
    "priority",
    "status",
    # guild / config
    "guild_name",
    "guild_id",
    "verify_site_url",
    "vc_channel_mention",
    "vc_channel_id",
}

_MACROS_TABLE_AVAILABLE: Optional[bool] = None
_MACRO_SEND_LOCKS: Dict[str, asyncio.Lock] = {}

_VALID_BOOL_TRUE = {"1", "true", "yes", "y", "on"}
_VALID_BOOL_FALSE = {"0", "false", "no", "n", "off"}

_VALID_TICKET_STATUSES = {"open", "claimed", "closed", "deleted"}

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


# ============================================================
# Default macros
# ============================================================

DEFAULT_MACROS: List[Dict[str, Any]] = [
    {
        "slug": "welcome",
        "name": "Welcome",
        "category": None,
        "body": (
            "Hey {user_mention}, thanks for opening this ticket.\n\n"
            "A staff member will be with you as soon as possible."
        ),
        "aliases": ["hello", "greeting"],
        "sort_order": 10,
        "active": True,
    },
    {
        "slug": "claim-intro",
        "name": "Claim Intro",
        "category": None,
        "body": (
            "{user_mention} Hey — I’m {staff_mention} and I’ll be handling this ticket.\n\n"
            "Please give me a moment to review everything."
        ),
        "aliases": ["claimed", "intro"],
        "sort_order": 20,
        "active": True,
    },
    {
        "slug": "need-more-info",
        "name": "Need More Info",
        "category": None,
        "body": (
            "{user_mention} I need a bit more information before I can continue.\n\n"
            "Please send any extra details, screenshots, timestamps, or context that might help."
        ),
        "aliases": ["more-info", "info"],
        "sort_order": 30,
        "active": True,
    },
    {
        "slug": "be-patient",
        "name": "Be Patient",
        "category": None,
        "body": (
            "{user_mention} Thanks for your patience.\n\n"
            "We have not forgotten about you — someone will follow up as soon as possible."
        ),
        "aliases": ["wait", "patience"],
        "sort_order": 40,
        "active": True,
    },
    {
        "slug": "closing-soon",
        "name": "Closing Soon",
        "category": None,
        "body": (
            "{user_mention} This ticket looks resolved.\n\n"
            "If you still need help, reply before it is closed."
        ),
        "aliases": ["close-warning", "resolved-check"],
        "sort_order": 50,
        "active": True,
    },
    {
        "slug": "verification-reminder",
        "name": "Verification Reminder",
        "category": "verification_issue",
        "body": (
            "{user_mention} Please complete verification using the secure upload / VC options in this ticket.\n\n"
            "If something is blocking you, explain it clearly so staff can help."
        ),
        "aliases": ["verify-reminder", "verify"],
        "sort_order": 60,
        "active": True,
    },
    {
        "slug": "verification-resubmit",
        "name": "Verification Resubmit",
        "category": "verification_issue",
        "body": (
            "{user_mention} Staff needs you to **resubmit** your verification using the updated secure upload option.\n\n"
            "Make sure the images are clear and complete."
        ),
        "aliases": ["resubmit", "verify-resubmit"],
        "sort_order": 70,
        "active": True,
    },
    {
        "slug": "vc-ready",
        "name": "VC Ready",
        "category": "verification_issue",
        "body": (
            "{user_mention} A staff member is ready for voice verification.\n\n"
            "Join {vc_channel_mention} when instructed."
        ),
        "aliases": ["voice-ready", "vc"],
        "sort_order": 80,
        "active": True,
    },
    {
        "slug": "approved",
        "name": "Approved",
        "category": "verification_issue",
        "body": (
            "{user_mention} You’ve been approved.\n\n"
            "Your access should update shortly. If anything still looks wrong, let us know before this ticket closes."
        ),
        "aliases": ["accept", "verified"],
        "sort_order": 90,
        "active": True,
    },
    {
        "slug": "denied",
        "name": "Denied",
        "category": "verification_issue",
        "body": (
            "{user_mention} Your submission was denied.\n\n"
            "If staff asked for a resubmission, use the updated secure upload option in this ticket."
        ),
        "aliases": ["reject"],
        "sort_order": 100,
        "active": True,
    },
    {
        "slug": "ghost-note",
        "name": "Ghost Note",
        "category": "ghost",
        "body": (
            "Ghost / internal test ticket handled by {staff_mention}.\n"
            "Ticket #{ticket_number} in {channel_mention}."
        ),
        "aliases": ["ghost", "test-note"],
        "sort_order": 110,
        "active": True,
        "send_as_note": True,
    },
]


# ============================================================
# Small helpers
# ============================================================

def _macro_debug(msg: str) -> None:
    try:
        print(f"🧩 ticket_macros {msg}")
    except Exception:
        pass


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default

        text = str(value).strip().lower()
        if text in _VALID_BOOL_TRUE:
            return True
        if text in _VALID_BOOL_FALSE:
            return False
        return default
    except Exception:
        return default


def _clean_text(value: Any, limit: int = 4000) -> str:
    try:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        return text[:limit]
    except Exception:
        return ""


def _normalize_slug(value: Any) -> str:
    text = _safe_str(value).strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9_\-\s]+", "", text)
    text = re.sub(r"[\s\-]+", "-", text).strip("-")
    return text


def _normalize_category(value: Any) -> Optional[str]:
    text = _normalize_slug(value)
    return text or None


def _normalize_aliases(value: Any) -> List[str]:
    out: List[str] = []

    try:
        if isinstance(value, list):
            raw_items = value
        elif isinstance(value, tuple):
            raw_items = list(value)
        elif isinstance(value, str):
            raw_items = [part.strip() for part in value.split(",")]
        else:
            raw_items = []

        for item in raw_items:
            slug = _normalize_slug(item)
            if slug and slug not in out:
                out.append(slug)
    except Exception:
        pass

    return out


def _ticket_status_from_row(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("status")).strip().lower()
        if raw in _VALID_TICKET_STATUSES:
            return raw
        if raw in {"active", "reopened"}:
            return "open"
    except Exception:
        pass
    return "unknown"


def _ticket_archive_category_id() -> int:
    for key in (
        "TICKET_ARCHIVE_CATEGORY_ID",
        "TICKET_ARCHIVED_CATEGORY_ID",
        "ARCHIVED_TICKET_CATEGORY_ID",
        "ARCHIVE_TICKET_CATEGORY_ID",
    ):
        try:
            value = int(globals().get(key, 0) or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _looks_like_archive_category_name(name: str) -> bool:
    text = _safe_str(name).lower()
    if not text:
        return False

    markers = (
        "archive",
        "archived",
        "ticket archive",
        "tickets archive",
        "archived tickets",
        "closed tickets",
    )
    return any(marker in text for marker in markers)


def _resolve_archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_archive_category_id()
    if explicit_id > 0:
        try:
            maybe = guild.get_channel(explicit_id)
            if isinstance(maybe, discord.CategoryChannel):
                return maybe
        except Exception:
            pass

    try:
        for category in guild.categories:
            if _looks_like_archive_category_name(category.name):
                return category
    except Exception:
        pass

    return None


def _channel_looks_closed(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("closed-")
    except Exception:
        return False


def _channel_is_in_archive_category(channel: discord.TextChannel) -> bool:
    try:
        archive = _resolve_archive_category(channel.guild)
        if archive is not None and channel.category is not None:
            if int(channel.category.id) == int(archive.id):
                return True
        if channel.category is not None:
            return _looks_like_archive_category_name(channel.category.name)
    except Exception:
        pass
    return False


def _ticket_is_closed_like(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> bool:
    status = _ticket_status_from_row(row)
    if status == "closed":
        return True
    if _channel_looks_closed(channel):
        return True
    if _channel_is_in_archive_category(channel):
        return True
    return False


def _ticket_is_deleted(row: Optional[Dict[str, Any]]) -> bool:
    return _ticket_status_from_row(row) == "deleted"


async def _move_ticket_to_archive_if_configured(channel: discord.TextChannel) -> bool:
    archive_category = _resolve_archive_category(channel.guild)
    if archive_category is None:
        return False

    try:
        if channel.category is not None and int(channel.category.id) == int(archive_category.id):
            return True
    except Exception:
        pass

    try:
        await channel.edit(
            category=archive_category,
            sync_permissions=False,
            reason="Macro close_after_send -> move to archive category",
        )
        return True
    except Exception as e:
        _macro_debug(f"archive move failed channel={channel.id} error={repr(e)}")
        return False


def _channel_send_lock(channel_id: int | str) -> asyncio.Lock:
    key = str(channel_id)
    lock = _MACRO_SEND_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _MACRO_SEND_LOCKS[key] = lock
    return lock


def _normalize_macro_row(raw: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    try:
        slug = _normalize_slug(
            raw.get("slug")
            or raw.get("key")
            or raw.get("id")
            or raw.get("name")
        )
        if not slug:
            return None

        body = (
            _clean_text(raw.get("body"))
            or _clean_text(raw.get("content"))
            or _clean_text(raw.get("message"))
            or _clean_text(raw.get("text"))
        )
        if not body:
            return None

        name = _clean_text(raw.get("name"), limit=200) or slug.replace("-", " ").title()
        category = _normalize_category(raw.get("category"))
        aliases = _normalize_aliases(raw.get("aliases"))
        sort_order = _safe_int(raw.get("sort_order"), 10_000)

        active_value = raw.get("active")
        if active_value is None and "is_active" in raw:
            active_value = raw.get("is_active")
        active = _safe_bool(active_value, True)

        tags = _normalize_aliases(raw.get("tags"))
        send_as_note = _safe_bool(raw.get("send_as_note"), False)
        close_after_send = _safe_bool(raw.get("close_after_send"), False)

        return {
            "slug": slug,
            "name": name,
            "body": body,
            "category": category,
            "aliases": aliases,
            "tags": tags,
            "sort_order": sort_order,
            "active": active,
            "send_as_note": send_as_note,
            "close_after_send": close_after_send,
            "_source": source,
            "_raw": dict(raw),
        }
    except Exception:
        return None


def _macro_key(row: Dict[str, Any]) -> Tuple[str, str]:
    category = _safe_str(row.get("category")).strip().lower()
    slug = _normalize_slug(row.get("slug"))
    return category, slug


def _macro_matches_slug(row: Dict[str, Any], slug: str) -> bool:
    target = _normalize_slug(slug)
    if not target:
        return False

    row_slug = _normalize_slug(row.get("slug"))
    if row_slug == target:
        return True

    for alias in row.get("aliases") or []:
        if _normalize_slug(alias) == target:
            return True

    return False


def _sort_macros(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = list(rows)
    out.sort(
        key=lambda row: (
            _safe_int(row.get("sort_order"), 10_000),
            _safe_str(row.get("name")).lower(),
            _safe_str(row.get("slug")).lower(),
        )
    )
    return out


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


# ============================================================
# Macro loading
# ============================================================

def _load_default_macros() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in DEFAULT_MACROS:
        if not isinstance(row, dict):
            continue
        norm = _normalize_macro_row(row, "default")
        if norm is not None:
            out.append(norm)
    return out


def _load_global_macros() -> List[Dict[str, Any]]:
    candidates: List[Any] = []

    for key in (
        "TICKET_MACROS",
        "TICKET_MACRO_DEFINITIONS",
        "DEFAULT_TICKET_MACROS",
    ):
        try:
            value = globals().get(key)
            if value:
                candidates.append(value)
        except Exception:
            continue

    out: List[Dict[str, Any]] = []

    for candidate in candidates:
        try:
            if isinstance(candidate, dict):
                for slug, payload in candidate.items():
                    if isinstance(payload, dict):
                        row = dict(payload)
                        row.setdefault("slug", slug)
                    else:
                        row = {
                            "slug": slug,
                            "body": _safe_str(payload),
                        }
                    norm = _normalize_macro_row(row, "global")
                    if norm is not None:
                        out.append(norm)
            elif isinstance(candidate, list):
                for item in candidate:
                    if isinstance(item, dict):
                        norm = _normalize_macro_row(item, "global")
                        if norm is not None:
                            out.append(norm)
        except Exception:
            continue

    return out


def _macros_table_missing_error(exc: Exception) -> bool:
    text = repr(exc or "").lower()
    return (
        MACROS_TABLE.lower() in text
        and (
            "pgrst204" in text
            or "42p01" in text
            or "does not exist" in text
            or "schema cache" in text
            or "relation" in text
            or "column" in text
        )
    )


def _fetch_db_macros_sync(guild_id: int | str) -> List[Dict[str, Any]]:
    global _MACROS_TABLE_AVAILABLE

    sb = _sb()
    if sb is None:
        return []

    try:
        res = (
            sb.table(MACROS_TABLE)
            .select("*")
            .eq("guild_id", str(guild_id))
            .execute()
        )
        rows = getattr(res, "data", None) or []
        _MACROS_TABLE_AVAILABLE = True

        out: List[Dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            norm = _normalize_macro_row(row, "db")
            if norm is not None:
                out.append(norm)
        return out

    except Exception as e:
        if _macros_table_missing_error(e):
            _MACROS_TABLE_AVAILABLE = False
            _macro_debug(f"{MACROS_TABLE} unavailable for guild={guild_id}")
            return []

        print(f"⚠️ Failed fetching ticket macros for guild={guild_id}: {repr(e)}")
        return []


async def _fetch_db_macros(guild_id: int | str) -> List[Dict[str, Any]]:
    if _MACROS_TABLE_AVAILABLE is False:
        return []

    try:
        return await asyncio.to_thread(_fetch_db_macros_sync, guild_id)
    except Exception as e:
        print(f"⚠️ Async macro fetch failed for guild={guild_id}: {repr(e)}")
        return []


def _merge_macro_layers(layers: List[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for layer in layers:
        for row in layer:
            key = _macro_key(row)

            if not _safe_bool(row.get("active"), True):
                merged.pop(key, None)
                continue

            merged[key] = dict(row)

    return _sort_macros(list(merged.values()))


async def list_ticket_macros(
    *,
    guild_id: int | str,
    category: Optional[str] = None,
    include_defaults: bool = True,
) -> List[Dict[str, Any]]:
    norm_category = _normalize_category(category)

    default_rows = _load_default_macros() if include_defaults else []
    global_rows = _load_global_macros()
    db_rows = await _fetch_db_macros(guild_id)

    merged = _merge_macro_layers([default_rows, global_rows, db_rows])

    if not norm_category:
        return merged

    filtered: List[Dict[str, Any]] = []
    for row in merged:
        row_category = _normalize_category(row.get("category"))
        if row_category is None or row_category == norm_category:
            filtered.append(row)

    return _sort_macros(filtered)


async def get_ticket_macro(
    *,
    guild_id: int | str,
    slug: str,
    category: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    all_rows = await list_ticket_macros(
        guild_id=guild_id,
        category=None,
        include_defaults=True,
    )

    wanted_slug = _normalize_slug(slug)
    wanted_category = _normalize_category(category)

    exact_category_match: Optional[Dict[str, Any]] = None
    generic_match: Optional[Dict[str, Any]] = None

    for row in all_rows:
        if not _macro_matches_slug(row, wanted_slug):
            continue

        row_category = _normalize_category(row.get("category"))
        if wanted_category and row_category == wanted_category:
            exact_category_match = row
            break

        if row_category is None and generic_match is None:
            generic_match = row

        if wanted_category is None and generic_match is None:
            generic_match = row

    return exact_category_match or generic_match


# ============================================================
# Ticket context helpers
# ============================================================

async def _resolve_member_from_user_id(
    guild: discord.Guild,
    user_id: Any,
) -> Optional[discord.Member]:
    try:
        uid = int(str(user_id or "0") or 0)
        if uid <= 0:
            return None
    except Exception:
        return None

    try:
        cached = guild.get_member(uid)
        if isinstance(cached, discord.Member):
            return cached
    except Exception:
        pass

    try:
        fetched = await guild.fetch_member(uid)
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        pass

    return None


def _vc_channel_from_globals(guild: discord.Guild) -> Optional[discord.VoiceChannel]:
    try:
        raw = globals().get("VC_VERIFY_CHANNEL_ID")
        cid = int(str(raw or "0") or 0)
        if cid <= 0:
            return None

        ch = guild.get_channel(cid)
        return ch if isinstance(ch, discord.VoiceChannel) else None
    except Exception:
        return None


async def build_macro_context(
    *,
    channel: discord.TextChannel,
    actor: Optional[discord.Member | discord.User] = None,
    ticket_row: Optional[Dict[str, Any]] = None,
    extra_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    row = ticket_row or await get_ticket_by_any_channel_id(channel.id) or {}
    guild = channel.guild

    owner: Optional[discord.Member] = None
    assignee: Optional[discord.Member] = None

    try:
        owner = await _resolve_member_from_user_id(
            guild,
            row.get("user_id") or row.get("owner_id") or row.get("requester_id"),
        )
    except Exception:
        owner = None

    try:
        assignee = await _resolve_member_from_user_id(
            guild,
            row.get("assigned_to") or row.get("claimed_by"),
        )
    except Exception:
        assignee = None

    vc_channel = _vc_channel_from_globals(guild)

    owner_id = _safe_str(row.get("user_id") or row.get("owner_id") or row.get("requester_id")).strip()
    assignee_id = _safe_str(row.get("assigned_to") or row.get("claimed_by")).strip()

    owner_mention = (
        owner.mention
        if isinstance(owner, discord.Member)
        else (f"<@{owner_id}>" if owner_id else "the user")
    )
    assignee_mention = (
        assignee.mention
        if isinstance(assignee, discord.Member)
        else (f"<@{assignee_id}>" if assignee_id else "the assigned staff member")
    )

    owner_name = _safe_str(getattr(owner, "name", row.get("username"))).strip()
    owner_display = _safe_str(getattr(owner, "display_name", row.get("username"))).strip()
    assignee_name = _safe_str(getattr(assignee, "name", "")).strip()
    assignee_display = _safe_str(getattr(assignee, "display_name", "")).strip()

    context: Dict[str, str] = {
        "guild_name": _safe_str(getattr(guild, "name", "")).strip(),
        "guild_id": _safe_str(getattr(guild, "id", "")).strip(),
        "channel_name": _safe_str(getattr(channel, "name", "")).strip(),
        "channel_id": _safe_str(getattr(channel, "id", "")).strip(),
        "channel_mention": getattr(channel, "mention", f"<#{getattr(channel, 'id', 0)}>"),
        "ticket_id": _safe_str(row.get("id")).strip(),
        "ticket_number": _safe_str(row.get("ticket_number")).strip(),
        "ticket_title": _safe_str(row.get("title")).strip(),
        "category": _safe_str(row.get("category")).strip(),
        "priority": _safe_str(row.get("priority") or "medium").strip(),
        "status": _safe_str(row.get("status") or "open").strip(),
        "user_id": owner_id,
        "user_name": owner_name,
        "user_display": owner_display,
        "user_mention": owner_mention,
        "owner_id": owner_id,
        "owner_name": owner_name,
        "owner_display": owner_display,
        "owner_mention": owner_mention,
        "assignee_id": assignee_id,
        "assignee_name": assignee_name,
        "assignee_display": assignee_display,
        "assignee_mention": assignee_mention,
        "staff_id": _safe_str(getattr(actor, "id", "")).strip() if actor else "",
        "staff_name": _safe_str(getattr(actor, "name", "")).strip() if actor else "",
        "staff_display": _safe_str(getattr(actor, "display_name", actor or "")).strip() if actor else "",
        "staff_mention": getattr(actor, "mention", "") if actor else "",
        "verify_site_url": _safe_str(globals().get("VERIFY_SITE_URL")).strip(),
        "vc_channel_id": _safe_str(getattr(vc_channel, "id", "")).strip() if vc_channel else "",
        "vc_channel_mention": getattr(vc_channel, "mention", "the VC verify channel") if vc_channel else "the VC verify channel",
    }

    if isinstance(extra_context, dict):
        for key, value in extra_context.items():
            key_clean = _normalize_slug(str(key or "")).replace("-", "_")
            if not key_clean:
                continue
            context[key_clean] = _safe_str(value)

    return context


def render_ticket_macro(
    template: str,
    context: Dict[str, Any],
) -> str:
    safe_context: Dict[str, str] = {}
    for key, value in (context or {}).items():
        key_clean = _normalize_slug(str(key or "")).replace("-", "_")
        if not key_clean:
            continue
        safe_context[key_clean] = _safe_str(value)

    def _replace(match: re.Match[str]) -> str:
        key = _safe_str(match.group(1)).strip()
        if key not in _DEFAULT_ALLOWED_PLACEHOLDERS and key not in safe_context:
            return ""
        return safe_context.get(key, "")

    rendered = _PLACEHOLDER_RE.sub(_replace, _safe_str(template))
    rendered = rendered.replace("\r\n", "\n").replace("\r", "\n")
    return rendered.strip()


# ============================================================
# Public macro helpers
# ============================================================

async def preview_ticket_macro(
    *,
    channel: discord.TextChannel,
    slug: str,
    actor: Optional[discord.Member | discord.User] = None,
    extra_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row = await get_ticket_by_any_channel_id(channel.id)
    if not row:
        return {
            "ok": False,
            "message": "Ticket row not found for this channel.",
            "macro": None,
            "content": None,
        }

    macro = await get_ticket_macro(
        guild_id=channel.guild.id,
        slug=slug,
        category=row.get("category"),
    )
    if not macro:
        return {
            "ok": False,
            "message": f"Macro `{slug}` was not found.",
            "macro": None,
            "content": None,
        }

    context = await build_macro_context(
        channel=channel,
        actor=actor,
        ticket_row=row,
        extra_context=extra_context,
    )
    content = render_ticket_macro(_safe_str(macro.get("body")), context)

    return {
        "ok": True,
        "message": "Macro preview ready.",
        "macro": macro,
        "content": content,
        "context": context,
        "ticket_row": row,
    }


async def send_ticket_macro(
    *,
    channel: discord.TextChannel,
    slug: str,
    actor: discord.Member | discord.User,
    extra_context: Optional[Dict[str, Any]] = None,
    force_as_note: Optional[bool] = None,
    allow_closed: bool = False,
) -> Dict[str, Any]:
    lock = _channel_send_lock(channel.id)

    async with lock:
        preview = await preview_ticket_macro(
            channel=channel,
            slug=slug,
            actor=actor,
            extra_context=extra_context,
        )
        if not preview.get("ok"):
            return preview

        macro = dict(preview.get("macro") or {})
        content = _clean_text(preview.get("content"), limit=4000)
        row = (
            preview.get("ticket_row")
            if isinstance(preview.get("ticket_row"), dict)
            else await get_ticket_by_any_channel_id(channel.id)
        )

        if not row:
            return {
                "ok": False,
                "message": "Ticket row not found for this channel.",
                "macro": macro,
                "content": content,
            }

        if _ticket_is_deleted(row):
            return {
                "ok": False,
                "message": "Deleted tickets cannot send macros.",
                "macro": macro,
                "content": content,
            }

        if not allow_closed and _ticket_is_closed_like(channel, row):
            return {
                "ok": False,
                "message": "Closed tickets cannot send macros. Reopen the ticket first.",
                "macro": macro,
                "content": content,
            }

        if not content:
            return {
                "ok": False,
                "message": "Rendered macro content was empty.",
                "macro": macro,
                "content": "",
            }

        send_as_note = (
            _safe_bool(force_as_note, False)
            if force_as_note is not None
            else _safe_bool(macro.get("send_as_note"), False)
        )
        close_after_send = _safe_bool(macro.get("close_after_send"), False)

        sent_message: Optional[discord.Message] = None
        note_saved = False
        moved_to_archive = False
        ticket_closed = False

        if send_as_note:
            note_saved = await repo_add_internal_note(
                channel_id=channel.id,
                author=actor,
                note=content,
                is_pinned=False,
            )
            if not note_saved:
                return {
                    "ok": False,
                    "message": "Failed to save macro as an internal note.",
                    "macro": macro,
                    "content": content,
                    "sent_message_id": None,
                    "note_saved": False,
                    "send_as_note": True,
                    "close_after_send": close_after_send,
                }
        else:
            try:
                sent_message = await channel.send(
                    content,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                        replied_user=False,
                    ),
                )
            except Exception as e:
                return {
                    "ok": False,
                    "message": f"Failed to send macro: {e}",
                    "macro": macro,
                    "content": content,
                    "sent_message_id": None,
                    "note_saved": False,
                    "send_as_note": False,
                    "close_after_send": close_after_send,
                }

        try:
            await log_ticket_event(
                guild_id=channel.guild.id,
                event_type="ticket_macro_used",
                actor_user_id=getattr(actor, "id", None),
                actor_name=_safe_str(actor),
                channel_id=channel.id,
                ticket_id=row.get("id"),
                ticket_message_id=getattr(sent_message, "id", None),
                source="ticket_macros_service",
                metadata={
                    "macro_slug": macro.get("slug"),
                    "macro_name": macro.get("name"),
                    "macro_source": macro.get("_source"),
                    "send_as_note": send_as_note,
                    "close_after_send": close_after_send,
                    "message_id": getattr(sent_message, "id", None),
                },
                ticket_row=row,
            )
        except Exception:
            pass

        if close_after_send:
            if service_mark_ticket_closed is None:
                return {
                    "ok": False,
                    "message": "Macro was sent, but close_after_send is configured and the close service is unavailable.",
                    "macro": macro,
                    "content": content,
                    "sent_message_id": getattr(sent_message, "id", None),
                    "note_saved": note_saved,
                    "send_as_note": send_as_note,
                    "close_after_send": True,
                    "ticket_closed": False,
                    "moved_to_archive": False,
                }

            try:
                ticket_closed = await service_mark_ticket_closed(
                    channel=channel,
                    closed_by=actor,
                    reason=f"Macro close_after_send: {macro.get('slug')}",
                )
            except Exception as e:
                return {
                    "ok": False,
                    "message": f"Macro was sent, but closing the ticket failed: {e}",
                    "macro": macro,
                    "content": content,
                    "sent_message_id": getattr(sent_message, "id", None),
                    "note_saved": note_saved,
                    "send_as_note": send_as_note,
                    "close_after_send": True,
                    "ticket_closed": False,
                    "moved_to_archive": False,
                }

            if ticket_closed:
                moved_to_archive = await _move_ticket_to_archive_if_configured(channel)

                try:
                    await log_ticket_closed(
                        guild_id=channel.guild.id,
                        actor_user_id=getattr(actor, "id", None),
                        actor_name=_safe_str(actor),
                        channel_id=channel.id,
                        reason=f"Macro close_after_send: {macro.get('slug')}",
                        source="ticket_macros_service",
                        metadata={
                            "macro_slug": macro.get("slug"),
                            "macro_name": macro.get("name"),
                            "macro_source": macro.get("_source"),
                            "close_after_send": True,
                            "moved_to_archive": moved_to_archive,
                            "channel_name_after_close": getattr(channel, "name", None),
                        },
                        ticket_row=row,
                    )
                except Exception:
                    pass

        result_message = (
            "Macro sent."
            if not send_as_note
            else "Macro saved as internal note."
        )
        if close_after_send and ticket_closed:
            result_message += " Ticket was then closed."

        return {
            "ok": True,
            "message": result_message,
            "macro": macro,
            "content": content,
            "sent_message_id": getattr(sent_message, "id", None),
            "note_saved": note_saved,
            "send_as_note": send_as_note,
            "close_after_send": close_after_send,
            "ticket_closed": ticket_closed,
            "moved_to_archive": moved_to_archive,
        }


async def list_available_macros_for_ticket(
    *,
    channel: discord.TextChannel,
) -> List[Dict[str, Any]]:
    row = await get_ticket_by_any_channel_id(channel.id)
    category = row.get("category") if isinstance(row, dict) else None

    macros = await list_ticket_macros(
        guild_id=channel.guild.id,
        category=category,
        include_defaults=True,
    )
    return macros


async def format_available_macros_for_ticket(
    *,
    channel: discord.TextChannel,
    limit: int = 25,
) -> str:
    macros = await list_available_macros_for_ticket(channel=channel)
    if not macros:
        return "No macros are available for this ticket."

    lines: List[str] = []
    for index, row in enumerate(macros[: max(1, limit)], start=1):
        name = _clean_text(row.get("name"), limit=80) or _safe_str(row.get("slug"))
        slug = _safe_str(row.get("slug"))
        category = _safe_str(row.get("category")).strip() or "all"
        source = _safe_str(row.get("_source")).strip() or "unknown"
        aliases = row.get("aliases") or []
        alias_text = f" | aliases: {', '.join(aliases[:4])}" if aliases else ""
        note_flag = " | note" if _safe_bool(row.get("send_as_note"), False) else ""
        close_flag = " | closes" if _safe_bool(row.get("close_after_send"), False) else ""
        lines.append(
            f"**{index}.** `{slug}` — {name} | category: `{category}` | source: `{source}`{note_flag}{close_flag}{alias_text}"
        )

    return "\n".join(lines)


async def build_macro_dashboard_snapshot(
    *,
    guild_id: int | str,
) -> Dict[str, Any]:
    macros = await list_ticket_macros(
        guild_id=guild_id,
        category=None,
        include_defaults=True,
    )

    categories: Dict[str, int] = {}
    sources: Dict[str, int] = {}

    for row in macros:
        category = _safe_str(row.get("category")).strip() or "all"
        source = _safe_str(row.get("_source")).strip() or "unknown"

        categories[category] = int(categories.get(category, 0)) + 1
        sources[source] = int(sources.get(source, 0)) + 1

    return {
        "ok": True,
        "guild_id": _safe_str(guild_id),
        "macro_count": len(macros),
        "categories": categories,
        "sources": sources,
        "macros": macros,
    }


__all__ = [
    "MACROS_TABLE",
    "DEFAULT_MACROS",
    "list_ticket_macros",
    "get_ticket_macro",
    "build_macro_context",
    "render_ticket_macro",
    "preview_ticket_macro",
    "send_ticket_macro",
    "list_available_macros_for_ticket",
    "format_available_macros_for_ticket",
    "build_macro_dashboard_snapshot",
]
