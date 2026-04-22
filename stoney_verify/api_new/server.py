from __future__ import annotations

import hmac
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web

import discord

from ..globals import (
    BOT_API_ALLOW_INSECURE,
    BOT_API_BIND_HOST,
    BOT_API_PORT,
    BOT_API_REQUIRE_AUTH,
    BOT_API_SHARED_SECRET,
    TICKET_CATEGORY_ID,
    bot,
)
from ..tickets import find_ticket_owner_retry
from ..tickets_new.service import (
    assign_ticket,
    create_ticket_channel,
    find_open_ticket_for_owner,
    list_claimed_tickets,
    list_open_ticket_queue,
    list_tickets_claimed_by_staff,
    list_unclaimed_tickets,
    mark_ticket_closed,
    reopen_ticket,
    reopen_ticket_channel,
)

try:
    from ..tickets_new.service import unclaim_ticket, transfer_ticket
except Exception:
    unclaim_ticket = None  # type: ignore
    transfer_ticket = None  # type: ignore

try:
    from ..tickets_new.repository import get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id
except Exception:
    async def repo_get_ticket_by_any_channel_id(channel_id: int | str):  # type: ignore
        return None

from ..tickets_new.transcript_service import (
    delete_ticket_with_optional_transcript,
    post_transcript_to_channel,
)
from ..tickets_new.sync_service import (
    sync_active_ticket_channels_for_guild,
    sync_one_ticket_channel,
)
from ..events_new.members import (
    run_full_member_sync_for_guild,
    run_departed_reconciliation_for_guild,
    run_role_member_sync,
)

_API_RUNNER: Optional[web.AppRunner] = None
_API_SITE: Optional[web.TCPSite] = None


def _json_error(msg: str, code: int = 400, **extra: Any):
    payload: Dict[str, Any] = {"ok": False, "error": msg}
    payload.update(extra)
    return web.json_response(payload, status=code)


def _json_ok(**extra: Any):
    payload: Dict[str, Any] = {"ok": True}
    payload.update(extra)
    return web.json_response(payload)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _safe_str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("status")).strip().lower()
        aliases = {
            "active": "open",
            "reopened": "open",
        }
        raw = aliases.get(raw, raw)
        if raw in {"open", "claimed", "closed", "deleted"}:
            return raw
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


def _ticket_active_category_id() -> int:
    try:
        value = int(TICKET_CATEGORY_ID or 0)
        if value > 0:
            return value
    except Exception:
        pass
    return 0


def _looks_like_archive_category_name(name: str) -> bool:
    text = _safe_str(name).strip().lower()
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


def _resolve_category_by_id(
    guild: discord.Guild,
    category_id: int,
) -> Optional[discord.CategoryChannel]:
    try:
        if category_id <= 0:
            return None
        channel = guild.get_channel(int(category_id))
        if isinstance(channel, discord.CategoryChannel):
            return channel
    except Exception:
        pass
    return None


def _resolve_archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_archive_category_id()
    if explicit_id > 0:
        explicit = _resolve_category_by_id(guild, explicit_id)
        if explicit is not None:
            return explicit

    try:
        for category in guild.categories:
            if _looks_like_archive_category_name(category.name):
                return category
    except Exception:
        pass

    return None


def _resolve_active_ticket_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_active_category_id()
    if explicit_id > 0:
        explicit = _resolve_category_by_id(guild, explicit_id)
        if explicit is not None:
            return explicit
    return None


def _channel_is_in_category(
    channel: discord.TextChannel,
    category: Optional[discord.CategoryChannel],
) -> bool:
    try:
        if category is None:
            return False
        return int(getattr(channel.category, "id", 0) or 0) == int(category.id)
    except Exception:
        return False


async def _move_ticket_to_archive_if_configured(channel: discord.TextChannel) -> bool:
    archive_category = _resolve_archive_category(channel.guild)
    if archive_category is None:
        return False

    if _channel_is_in_category(channel, archive_category):
        return True

    try:
        await channel.edit(
            category=archive_category,
            sync_permissions=False,
            reason="Ticket closed -> move to archive category",
        )
        return True
    except Exception:
        return False


async def _move_ticket_to_active_if_configured(channel: discord.TextChannel) -> bool:
    active_category = _resolve_active_ticket_category(channel.guild)
    if active_category is None:
        return False

    if _channel_is_in_category(channel, active_category):
        return True

    try:
        await channel.edit(
            category=active_category,
            sync_permissions=False,
            reason="Ticket reopened -> move back to active ticket category",
        )
        return True
    except Exception:
        return False


def _channel_looks_closed(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("closed-")
    except Exception:
        return False


def _channel_looks_open(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("ticket-")
    except Exception:
        return False


def _queue_row_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("id"),
        "guild_id": _safe_str(row.get("guild_id") or ""),
        "channel_id": _safe_str(row.get("channel_id") or row.get("discord_thread_id") or ""),
        "channel_name": _safe_str(row.get("channel_name") or ""),
        "ticket_number": row.get("ticket_number"),
        "title": _safe_str(row.get("title") or ""),
        "category": _safe_str(row.get("category") or ""),
        "status": _safe_str(row.get("status") or row.get("ticket_status") or ""),
        "priority": _safe_str(row.get("priority") or ""),
        "user_id": _safe_str(row.get("user_id") or row.get("owner_id") or row.get("requester_id") or ""),
        "username": _safe_str(row.get("username") or row.get("owner_name") or row.get("requester_name") or ""),
        "claimed_by": _safe_str(row.get("claimed_by") or row.get("assigned_to") or ""),
        "claimed_by_id": row.get("claimed_by_id"),
        "assigned_to": _safe_str(row.get("assigned_to") or ""),
        "is_unclaimed": bool(row.get("is_unclaimed", False)),
        "is_claimed": bool(row.get("is_claimed", False)),
        "is_ghost": bool(row.get("is_ghost", False)),
        "source": _safe_str(row.get("source") or ""),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "closed_at": row.get("closed_at"),
        "deleted_at": row.get("deleted_at"),
        "transcript_url": row.get("transcript_url"),
    }


def _channel_to_payload(channel: discord.TextChannel) -> Dict[str, Any]:
    return {
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "guild_id": str(channel.guild.id),
        "mention": channel.mention,
        "category_id": str(channel.category.id) if channel.category else None,
        "category_name": channel.category.name if channel.category else None,
    }


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    try:
        row = await repo_get_ticket_by_any_channel_id(channel.id)
        if isinstance(row, dict):
            return row
    except Exception:
        pass
    return None


async def _owner_for_ticket(
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> Optional[discord.Member]:
    owner_id = 0
    try:
        owner_id = _safe_int((row or {}).get("owner_id") or (row or {}).get("user_id"), 0)
    except Exception:
        owner_id = 0

    if owner_id > 0:
        try:
            member = channel.guild.get_member(owner_id)
            if member:
                return member
        except Exception:
            pass

        try:
            fetched = await channel.guild.fetch_member(owner_id)
            if isinstance(fetched, discord.Member):
                return fetched
        except Exception:
            pass

    try:
        owner = await find_ticket_owner_retry(channel)
        if isinstance(owner, discord.Member):
            return owner
    except Exception:
        pass

    return None


def _ticket_state_payload(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    archive_category = _resolve_archive_category(channel.guild)
    active_category = _resolve_active_ticket_category(channel.guild)

    in_archive = _channel_is_in_category(channel, archive_category)
    in_active = _channel_is_in_category(channel, active_category)

    if in_archive and archive_category is not None:
        lifecycle_location = f"archived:{archive_category.name}"
    elif in_active and active_category is not None:
        lifecycle_location = f"active:{active_category.name}"
    elif channel.category is not None:
        lifecycle_location = f"category:{channel.category.name}"
    else:
        lifecycle_location = "uncategorized"

    payload: Dict[str, Any] = {
        "channel": _channel_to_payload(channel),
        "db_status": _ticket_status(row),
        "channel_looks_closed": _channel_looks_closed(channel),
        "channel_looks_open": _channel_looks_open(channel),
        "lifecycle_location": lifecycle_location,
        "in_archive_category": in_archive,
        "in_active_category": in_active,
        "archive_category_id": str(archive_category.id) if archive_category else None,
        "archive_category_name": archive_category.name if archive_category else None,
        "active_category_id": str(active_category.id) if active_category else None,
        "active_category_name": active_category.name if active_category else None,
    }

    if isinstance(row, dict):
        payload["ticket"] = _queue_row_payload(row)

    return payload


def _api_bind_host() -> str:
    host = _safe_str(BOT_API_BIND_HOST).strip()
    return host or "127.0.0.1"


def _api_bind_port() -> int:
    port = _safe_int(BOT_API_PORT, 8081)
    if port <= 0 or port > 65535:
        return 8081
    return port


def _shared_secret() -> str:
    return _safe_str(BOT_API_SHARED_SECRET).strip()


def _has_shared_secret() -> bool:
    return bool(_shared_secret())


def _is_local_only_host(host: str) -> bool:
    normalized = _safe_str(host).strip().lower()
    return normalized in {"127.0.0.1", "localhost", "::1"}


def _allow_insecure_mode() -> bool:
    return bool(BOT_API_ALLOW_INSECURE)


def _should_require_api_auth() -> bool:
    return bool(BOT_API_REQUIRE_AUTH and _has_shared_secret())


def _extract_bearer_token(value: str) -> str:
    raw = _safe_str(value).strip()
    if not raw:
        return ""

    lower = raw.lower()
    if not lower.startswith("bearer "):
        return ""

    return raw[7:].strip()


def _auth_candidates(request: web.Request) -> List[str]:
    out: List[str] = []

    bearer = _extract_bearer_token(request.headers.get("Authorization", ""))
    if bearer:
        out.append(bearer)

    for header_name in ("X-API-Key", "X-Stoney-Internal-Auth"):
        candidate = _safe_str(request.headers.get(header_name, "")).strip()
        if candidate:
            out.append(candidate)

    return out


def _request_has_valid_auth(request: web.Request) -> bool:
    secret = _shared_secret()
    if not secret:
        return False

    for candidate in _auth_candidates(request):
        if candidate and hmac.compare_digest(candidate, secret):
            return True

    return False


def _validate_api_startup_config() -> None:
    bind_host = _api_bind_host()
    bind_port = _api_bind_port()
    require_auth = bool(BOT_API_REQUIRE_AUTH)
    allow_insecure = _allow_insecure_mode()
    secret_present = _has_shared_secret()

    if require_auth:
        if secret_present:
            print(
                f"🔐 Structured Bot API security mode: SECURE "
                f"(auth required, host={bind_host}, port={bind_port})"
            )
            return

        if allow_insecure and _is_local_only_host(bind_host):
            print(
                f"⚠️ Structured Bot API security mode: INSECURE LOCAL DEV "
                f"(missing shared secret, host={bind_host}, port={bind_port})"
            )
            return

        raise RuntimeError(
            "Structured Bot API refused to start: BOT_API_REQUIRE_AUTH=true but "
            "BOT_API_SHARED_SECRET is missing. Set BOT_API_SHARED_SECRET or only use "
            "BOT_API_ALLOW_INSECURE=true on localhost for local development."
        )

    if not allow_insecure:
        raise RuntimeError(
            "Structured Bot API refused to start: BOT_API_REQUIRE_AUTH=false requires "
            "BOT_API_ALLOW_INSECURE=true. Refusing to run an unauthenticated API without "
            "an explicit insecure override."
        )

    if not _is_local_only_host(bind_host):
        raise RuntimeError(
            "Structured Bot API refused to start: insecure mode is only allowed on "
            "localhost-safe bind hosts (127.0.0.1, localhost, or ::1)."
        )

    print(
        f"⚠️ Structured Bot API security mode: INSECURE LOCAL DEV "
        f"(auth disabled, host={bind_host}, port={bind_port})"
    )


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    if request.path == "/health":
        return await handler(request)

    if not _should_require_api_auth():
        return await handler(request)

    if _request_has_valid_auth(request):
        return await handler(request)

    return _json_error("Unauthorized", 401)


async def _request_data(request: web.Request) -> Dict[str, Any]:
    try:
        if request.can_read_body:
            data = await request.json()
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


async def _merged_request_data(request: web.Request) -> Dict[str, Any]:
    data = await _request_data(request)
    merged: Dict[str, Any] = {}
    try:
        merged.update(dict(request.query))
    except Exception:
        pass
    if isinstance(data, dict):
        merged.update(data)
    return merged


async def _get_guild_or_error(guild_id: Any) -> tuple[Optional[discord.Guild], Optional[web.Response]]:
    if not guild_id:
        return None, _json_error("guild_id required")

    gid = _safe_int(guild_id, 0)
    if gid <= 0:
        return None, _json_error("guild_id must be a valid integer string")

    guild = bot.get_guild(gid)
    if guild is None:
        try:
            await bot.fetch_guild(gid)
            guild = bot.get_guild(gid)
        except Exception:
            guild = None

    if guild is None:
        return None, _json_error("Guild not found", 404)

    return guild, None


async def _get_member_from_guild(
    guild: discord.Guild,
    user_id: Any,
) -> tuple[Optional[discord.Member], Optional[web.Response]]:
    if not user_id:
        return None, _json_error("user_id required")

    uid = _safe_int(user_id, 0)
    if uid <= 0:
        return None, _json_error("user_id must be a valid integer string")

    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except Exception:
            member = None

    if member is None:
        return None, _json_error("User not found in guild", 404)

    return member, None


async def _get_text_channel(
    channel_id: Any,
) -> tuple[Optional[discord.TextChannel], Optional[web.Response]]:
    if not channel_id:
        return None, _json_error("channel_id required")

    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return None, _json_error("channel_id must be a valid integer string")

    channel = bot.get_channel(cid)
    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except Exception:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        return None, _json_error("Channel not found or is not a text channel", 404)

    return channel, None


async def _close_ticket_via_service(
    *,
    channel: discord.TextChannel,
    closed_by: Optional[discord.Member],
    reason: Optional[str],
) -> bool:
    try:
        return bool(
            await mark_ticket_closed(
                channel=channel,
                closed_by=closed_by,
                reason=reason,
            )
        )
    except TypeError:
        pass
    except Exception:
        raise

    try:
        return bool(
            await mark_ticket_closed(
                channel_id=channel.id,
                closed_by=closed_by.id if closed_by else None,
                reason=reason,
            )
        )
    except TypeError:
        pass
    except Exception:
        raise

    try:
        return bool(
            await mark_ticket_closed(
                channel_id=channel.id,
                reason=reason,
            )
        )
    except Exception:
        return False


async def _reopen_ticket_via_service(
    *,
    channel: discord.TextChannel,
    actor: Optional[discord.Member],
    reason: Optional[str],
) -> bool:
    owner = await _owner_for_ticket(channel, await _ticket_row_for_channel(channel))

    try:
        return bool(
            await reopen_ticket_channel(
                channel=channel,
                owner=owner,
                actor=actor,
                reason=reason,
            )
        )
    except TypeError:
        pass
    except Exception:
        raise

    try:
        return bool(
            await reopen_ticket(
                channel_id=channel.id,
                actor=actor,
                reason=reason,
            )
        )
    except TypeError:
        pass
    except Exception:
        raise

    try:
        return bool(
            await reopen_ticket(
                channel_id=channel.id,
                reopened_by=actor.id if actor else None,
                reason=reason,
            )
        )
    except TypeError:
        pass
    except Exception:
        raise

    try:
        return bool(
            await reopen_ticket(
                channel_id=channel.id,
                reason=reason,
            )
        )
    except Exception:
        return False


async def _assign_ticket_via_service(
    *,
    channel: discord.TextChannel,
    staff: discord.Member,
) -> bool:
    try:
        return bool(await assign_ticket(channel_id=channel.id, staff_member=staff))
    except TypeError:
        pass
    except Exception:
        raise

    try:
        return bool(await assign_ticket(channel=channel, staff_member=staff))
    except Exception:
        return False


async def _unclaim_ticket_via_service(
    *,
    channel: discord.TextChannel,
) -> bool:
    if unclaim_ticket is None:
        return False

    try:
        return bool(await unclaim_ticket(channel_id=channel.id))
    except TypeError:
        pass
    except Exception:
        raise

    try:
        return bool(await unclaim_ticket(channel=channel))
    except Exception:
        return False


async def _transfer_ticket_via_service(
    *,
    channel: discord.TextChannel,
    to_staff: discord.Member,
) -> bool:
    if transfer_ticket is None:
        return False

    try:
        return bool(await transfer_ticket(channel_id=channel.id, to_staff_member=to_staff))
    except TypeError:
        pass
    except Exception:
        raise

    try:
        return bool(await transfer_ticket(channel=channel, to_staff_member=to_staff))
    except Exception:
        return False


async def _post_transcript_for_close(
    *,
    channel: discord.TextChannel,
    actor: Optional[discord.Member],
    reason: str,
) -> Tuple[bool, Optional[str]]:
    try:
        _posted, transcript_url = await post_transcript_to_channel(
            ticket_channel=channel,
            deleted_by=actor,
            reason=reason,
        )
        return True, transcript_url
    except Exception:
        return False, None


async def create_ticket(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    member, err = await _get_member_from_guild(guild, data.get("user_id"))
    if err:
        return err
    assert member is not None

    category = _safe_str(data.get("category") or "support").strip() or "support"
    is_ghost = _safe_bool(data.get("ghost"), False)
    opening_message = data.get("opening_message")
    priority = _safe_str(data.get("priority") or "medium").strip().lower() or "medium"

    parent_category_id = None
    if data.get("parent_category_id") is not None:
        parsed_parent = _safe_int(data.get("parent_category_id"), 0)
        parent_category_id = parsed_parent if parsed_parent > 0 else None

    staff_role_ids = None
    if isinstance(data.get("staff_role_ids"), list):
        parsed: List[int] = []
        for rid in data["staff_role_ids"]:
            role_id = _safe_int(rid, 0)
            if role_id > 0:
                parsed.append(role_id)
        staff_role_ids = parsed or None

    allow_duplicate = _safe_bool(data.get("allow_duplicate"), False)
    normalized_category = "ghost" if is_ghost else category

    if not allow_duplicate:
        existing = await find_open_ticket_for_owner(
            guild_id=guild.id,
            owner_id=member.id,
            category=normalized_category,
        )
        if existing:
            return _json_ok(
                created=False,
                duplicate=True,
                existing_ticket=_queue_row_payload(existing),
            )

    channel = await create_ticket_channel(
        guild=guild,
        owner=member,
        category=category,
        source="dashboard",
        is_ghost=is_ghost,
        parent_category_id=parent_category_id,
        staff_role_ids=staff_role_ids,
        opening_message=opening_message,
        priority=priority,
    )

    if channel is None:
        return _json_error("Failed to create ticket", 500)

    row = await _ticket_row_for_channel(channel)

    return _json_ok(
        created=True,
        duplicate=False,
        ticket=_channel_to_payload(channel),
        state=_ticket_state_payload(channel=channel, row=row),
    )


async def close_ticket(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    closed_by = None
    staff_id = data.get("staff_id")
    if staff_id:
        closed_by, _ = await _get_member_from_guild(channel.guild, staff_id)

    row_before = await _ticket_row_for_channel(channel)
    status_before = _ticket_status(row_before)

    if status_before == "deleted":
        return _json_error("Deleted tickets cannot be closed", 409)

    reason = _safe_str(data.get("reason")).strip() or None
    post_transcript = _safe_bool(data.get("post_transcript"), False)

    if status_before == "closed" and _channel_looks_closed(channel):
        moved_to_archive = await _move_ticket_to_archive_if_configured(channel)
        row_after = await _ticket_row_for_channel(channel)
        return _json_ok(
            closed=True,
            already_closed=True,
            moved_to_archive=moved_to_archive,
            channel_id=str(channel.id),
            closed_by=str(closed_by.id) if closed_by else None,
            state=_ticket_state_payload(channel=channel, row=row_after),
        )

    ok = await _close_ticket_via_service(
        channel=channel,
        closed_by=closed_by,
        reason=reason,
    )

    if not ok:
        return _json_error("Failed to mark ticket closed", 500)

    moved_to_archive = await _move_ticket_to_archive_if_configured(channel)

    transcript_ok = False
    transcript_url: Optional[str] = None
    if post_transcript:
        transcript_ok, transcript_url = await _post_transcript_for_close(
            channel=channel,
            actor=closed_by,
            reason=reason or "API ticket close",
        )

    row_after = await _ticket_row_for_channel(channel)

    return _json_ok(
        closed=True,
        already_closed=False,
        moved_to_archive=moved_to_archive,
        transcript_posted=transcript_ok,
        transcript_url=transcript_url,
        channel_id=str(channel.id),
        closed_by=str(closed_by.id) if closed_by else None,
        state=_ticket_state_payload(channel=channel, row=row_after),
    )


async def reopen_ticket_endpoint(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    actor = None
    actor_id = data.get("actor_id") or data.get("staff_id")
    if actor_id:
        actor, _ = await _get_member_from_guild(channel.guild, actor_id)

    row_before = await _ticket_row_for_channel(channel)
    status_before = _ticket_status(row_before)

    if status_before == "deleted":
        return _json_error("Deleted tickets cannot be reopened", 409)

    if status_before in {"open", "claimed"} and _channel_looks_open(channel):
        moved_to_active = await _move_ticket_to_active_if_configured(channel)
        row_after = await _ticket_row_for_channel(channel)
        return _json_ok(
            reopened=True,
            already_open=True,
            moved_to_active=moved_to_active,
            channel_id=str(channel.id),
            actor_id=str(actor.id) if actor else None,
            state=_ticket_state_payload(channel=channel, row=row_after),
        )

    ok = await _reopen_ticket_via_service(
        channel=channel,
        actor=actor,
        reason=_safe_str(data.get("reason")).strip() or None,
    )
    if not ok:
        return _json_error("Failed to reopen ticket", 500)

    moved_to_active = await _move_ticket_to_active_if_configured(channel)
    row_after = await _ticket_row_for_channel(channel)

    return _json_ok(
        reopened=True,
        already_open=False,
        moved_to_active=moved_to_active,
        channel_id=str(channel.id),
        actor_id=str(actor.id) if actor else None,
        state=_ticket_state_payload(channel=channel, row=row_after),
    )


async def assign_ticket_endpoint(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    channel_id = data.get("channel_id")
    staff_id = data.get("staff_id")

    if not channel_id:
        return _json_error("channel_id required")
    if not staff_id:
        return _json_error("staff_id required")

    channel, err = await _get_text_channel(channel_id)
    if err:
        return err
    assert channel is not None

    row = await _ticket_row_for_channel(channel)
    status = _ticket_status(row)
    if status in {"closed", "deleted"}:
        return _json_error("Only open tickets can be assigned", 409)

    staff, err = await _get_member_from_guild(channel.guild, staff_id)
    if err:
        return err
    assert staff is not None

    ok = await _assign_ticket_via_service(channel=channel, staff=staff)
    if not ok:
        return _json_error("Failed to assign ticket", 500)

    row_after = await _ticket_row_for_channel(channel)

    return _json_ok(
        assigned=True,
        channel_id=str(channel.id),
        staff_id=str(staff.id),
        staff_name=str(staff),
        state=_ticket_state_payload(channel=channel, row=row_after),
    )


async def unclaim_ticket_endpoint(request: web.Request):
    if unclaim_ticket is None:
        return _json_error("Unclaim ticket is not available in this build", 501)

    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    row = await _ticket_row_for_channel(channel)
    status = _ticket_status(row)
    if status in {"closed", "deleted"}:
        return _json_error("Only open tickets can be unclaimed", 409)

    ok = await _unclaim_ticket_via_service(channel=channel)
    if not ok:
        return _json_error("Failed to unclaim ticket", 500)

    row_after = await _ticket_row_for_channel(channel)

    return _json_ok(
        unclaimed=True,
        channel_id=str(channel.id),
        state=_ticket_state_payload(channel=channel, row=row_after),
    )


async def transfer_ticket_endpoint(request: web.Request):
    if transfer_ticket is None:
        return _json_error("Transfer ticket is not available in this build", 501)

    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    row = await _ticket_row_for_channel(channel)
    status = _ticket_status(row)
    if status in {"closed", "deleted"}:
        return _json_error("Only open tickets can be transferred", 409)

    target_staff_id = data.get("to_staff_id") or data.get("staff_id")
    if not target_staff_id:
        return _json_error("to_staff_id required")

    to_staff, err = await _get_member_from_guild(channel.guild, target_staff_id)
    if err:
        return err
    assert to_staff is not None

    ok = await _transfer_ticket_via_service(
        channel=channel,
        to_staff=to_staff,
    )
    if not ok:
        return _json_error("Failed to transfer ticket", 500)

    row_after = await _ticket_row_for_channel(channel)

    return _json_ok(
        transferred=True,
        channel_id=str(channel.id),
        to_staff_id=str(to_staff.id),
        to_staff_name=str(to_staff),
        state=_ticket_state_payload(channel=channel, row=row_after),
    )


async def delete_ticket(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    row = await _ticket_row_for_channel(channel)
    status = _ticket_status(row)

    if status == "deleted":
        return _json_ok(
            deleted=True,
            already_deleted=True,
            channel_id=str(channel.id),
        )

    if status != "closed" and not _channel_looks_closed(channel):
        return _json_error(
            "Ticket must be closed before deletion",
            409,
            state=_ticket_state_payload(channel=channel, row=row),
        )

    ghost = _safe_bool(data.get("ghost"), False)
    force_transcript = _safe_bool(data.get("force_transcript"), False)
    reason = _safe_str(data.get("reason") or "Deleted from dashboard").strip() or "Deleted from dashboard"

    deleted_by = None
    staff_id = data.get("staff_id")
    if staff_id:
        deleted_by, _ = await _get_member_from_guild(channel.guild, staff_id)

    result = await delete_ticket_with_optional_transcript(
        channel=channel,
        deleted_by=deleted_by,
        is_ghost=ghost,
        force_transcript_for_ghost=force_transcript,
        reason=reason,
    )

    if isinstance(result, dict):
        payload = dict(result)
        payload.setdefault("ok", bool(result.get("ok", False)))
        return web.json_response(payload)

    return _json_ok(
        deleted=bool(result),
        channel_id=str(channel.id),
    )


async def sync_active_tickets(request: web.Request):
    data = await _request_data(request)

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    include_closed_visible_channels = _safe_bool(
        data.get("include_closed_visible_channels"),
        True,
    )
    dry_run = _safe_bool(data.get("dry_run"), False)

    summary = await sync_active_ticket_channels_for_guild(
        guild,
        source="dashboard_ticket_sync",
        include_closed_visible_channels=include_closed_visible_channels,
        dry_run=dry_run,
    )

    return _json_ok(summary=summary)


async def sync_one_ticket(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    channel, err = await _get_text_channel(data.get("channel_id"))
    if err:
        return err
    assert channel is not None

    dry_run = _safe_bool(data.get("dry_run"), False)

    summary = await sync_one_ticket_channel(
        channel,
        source="dashboard_ticket_sync_one",
        dry_run=dry_run,
    )

    return _json_ok(summary=summary)


async def get_ticket_queue(request: web.Request):
    data = await _merged_request_data(request)

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    rows = await list_open_ticket_queue(guild_id=guild.id)
    payload_rows = [_queue_row_payload(row) for row in rows]

    return _json_ok(
        queue=payload_rows,
        total=len(payload_rows),
        unclaimed=sum(1 for row in payload_rows if bool(row.get("is_unclaimed"))),
        claimed=sum(1 for row in payload_rows if bool(row.get("is_claimed"))),
    )


async def get_unclaimed_tickets(request: web.Request):
    data = await _merged_request_data(request)

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    rows = await list_unclaimed_tickets(guild_id=guild.id)
    payload_rows = [_queue_row_payload(row) for row in rows]

    return _json_ok(
        tickets=payload_rows,
        total=len(payload_rows),
    )


async def get_claimed_tickets(request: web.Request):
    data = await _merged_request_data(request)

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    rows = await list_claimed_tickets(guild_id=guild.id)
    payload_rows = [_queue_row_payload(row) for row in rows]

    return _json_ok(
        tickets=payload_rows,
        total=len(payload_rows),
    )


async def get_my_claimed_tickets(request: web.Request):
    data = await _merged_request_data(request)

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    staff_id = data.get("staff_id")
    if not staff_id:
        return _json_error("staff_id required")

    staff_member, err = await _get_member_from_guild(guild, staff_id)
    if err:
        return err
    assert staff_member is not None

    rows = await list_tickets_claimed_by_staff(
        guild_id=guild.id,
        staff_id=staff_member.id,
    )
    payload_rows = [_queue_row_payload(row) for row in rows]

    return _json_ok(
        tickets=payload_rows,
        total=len(payload_rows),
        staff_id=str(staff_member.id),
        staff_name=str(staff_member),
    )


async def force_member_sync(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    summary = await run_full_member_sync_for_guild(guild)
    return _json_ok(summary=summary)


async def reconcile_departed(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    summary = await run_departed_reconciliation_for_guild(guild)
    return _json_ok(summary=summary)


async def role_member_sync(request: web.Request):
    data = await _request_data(request)
    if request.can_read_body and not isinstance(data, dict):
        return _json_error("Invalid JSON body")

    guild, err = await _get_guild_or_error(data.get("guild_id"))
    if err:
        return err
    assert guild is not None

    role_id = data.get("role_id")
    if not role_id:
        return _json_error("role_id required")

    rid = _safe_int(role_id, 0)
    if rid <= 0:
        return _json_error("role_id must be a valid integer string")

    role = guild.get_role(rid)
    if role is None:
        return _json_error("Role not found", 404)

    summary = await run_role_member_sync(role)
    return _json_ok(summary=summary)


async def health(request: web.Request):
    guild_count = 0
    try:
        guild_count = len(getattr(bot, "guilds", []) or [])
    except Exception:
        guild_count = 0

    return _json_ok(
        status="online",
        guild_count=guild_count,
        api="structured_bot_api",
        auth_required=_should_require_api_auth(),
        bind_host=_api_bind_host(),
        bind_port=_api_bind_port(),
    )


async def start_api(bot_instance: discord.Client):
    global _API_RUNNER, _API_SITE

    if _API_RUNNER is not None:
        print("⚠️ New structured Bot API already running; skipping duplicate start.")
        return

    _validate_api_startup_config()

    app = web.Application(middlewares=[_auth_middleware])

    app.router.add_get("/health", health)

    app.router.add_post("/ticket/create", create_ticket)
    app.router.add_post("/ticket/close", close_ticket)
    app.router.add_post("/ticket/delete", delete_ticket)
    app.router.add_post("/ticket/reopen", reopen_ticket_endpoint)
    app.router.add_post("/ticket/assign", assign_ticket_endpoint)

    if unclaim_ticket is not None:
        app.router.add_post("/ticket/unclaim", unclaim_ticket_endpoint)

    if transfer_ticket is not None:
        app.router.add_post("/ticket/transfer", transfer_ticket_endpoint)

    app.router.add_get("/tickets/queue", get_ticket_queue)
    app.router.add_post("/tickets/queue", get_ticket_queue)

    app.router.add_get("/tickets/unclaimed", get_unclaimed_tickets)
    app.router.add_post("/tickets/unclaimed", get_unclaimed_tickets)

    app.router.add_get("/tickets/claimed", get_claimed_tickets)
    app.router.add_post("/tickets/claimed", get_claimed_tickets)

    app.router.add_get("/tickets/my-claimed", get_my_claimed_tickets)
    app.router.add_post("/tickets/my-claimed", get_my_claimed_tickets)

    app.router.add_post("/tickets/sync-active", sync_active_tickets)
    app.router.add_post("/tickets/sync-one", sync_one_ticket)

    app.router.add_post("/members/sync", force_member_sync)
    app.router.add_post("/members/reconcile", reconcile_departed)
    app.router.add_post("/members/role-sync", role_member_sync)

    runner = web.AppRunner(app)
    await runner.setup()

    bind_host = _api_bind_host()
    bind_port = _api_bind_port()

    site = web.TCPSite(runner, bind_host, bind_port)
    await site.start()

    _API_RUNNER = runner
    _API_SITE = site

    print(
        f"🌐 New structured Bot API started on {bind_host}:{bind_port} "
        f"(auth_required={_should_require_api_auth()})"
    )
