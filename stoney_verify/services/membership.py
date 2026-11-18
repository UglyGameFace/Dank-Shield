from __future__ import annotations

"""Dank Shield Pro membership: entitlement checks and covered-guild links.

Phase 1 plumbing for the approved tier map (2026-11-18). Billing runs through
Discord's built-in app subscriptions, so Discord entitlements are the source
of truth for "does this account hold Pro". This module answers three
questions for callers:

- ``membership_configured()`` — has the operator set at least one Pro SKU id?
  When False, every caller must behave exactly as the pre-membership build:
  no gates and no upsell surfaces.
- ``user_has_active_pro(client, user_id)`` — does this account hold an active
  entitlement for a configured SKU? Results are cached in-process for a short
  TTL. A Discord API failure falls back to a cached positive so a paying user
  is not hard-blocked mid-session; with no cached positive it reports
  not-entitled and logs (fail safe for revenue, fail open for UX).
- ``guild_pro_status(guild_id, client=...)`` — one of "unconfigured", "pro"
  or "free" for a guild, resolved through the covered-guild link table: a Pro
  account links up to ``MAX_LINKED_GUILDS`` guilds, and each guild has at
  most one owning account.

Link rows live in ``public.dank_membership_guild_links`` (see
postgres/migrations/202611180001_membership.sql) on the same Discloud
PostgreSQL as the other dank_* ledgers, reached through the shared
``verification_reminder_postgres._connect`` helper, with sync cores wrapped
in ``asyncio.to_thread`` exactly like the reminder enforcement ledger.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from stoney_verify.verification_reminder_postgres import _connect

logger = logging.getLogger(__name__)

PRO_MONTHLY_SKU_ENV = "DANK_PRO_MONTHLY_SKU_ID"
PRO_YEARLY_SKU_ENV = "DANK_PRO_YEARLY_SKU_ID"
MAX_LINKED_GUILDS = 3
LINKS_TABLE = "public.dank_membership_guild_links"
ENTITLEMENT_CACHE_TTL_SECONDS = 300.0

STATUS_UNCONFIGURED = "unconfigured"
STATUS_PRO = "pro"
STATUS_FREE = "free"

LINK_LINKED = "linked"
LINK_ALREADY_LINKED = "already_linked"
LINK_CAP_REACHED = "cap_reached"
LINK_GUILD_OWNED = "guild_owned"
LINK_UNAVAILABLE = "unavailable"

# Discord entitlement type 8 is an application (app) subscription.
_APP_SUBSCRIPTION_ENTITLEMENT_TYPE = 8


def _parse_sku_id(raw: Any) -> Optional[int]:
    try:
        value = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def pro_sku_ids() -> tuple[int, ...]:
    """Configured Pro SKU ids (monthly first, then yearly), junk dropped."""

    out: list[int] = []
    for env_name in (PRO_MONTHLY_SKU_ENV, PRO_YEARLY_SKU_ENV):
        parsed = _parse_sku_id(os.getenv(env_name, ""))
        if parsed is not None and parsed not in out:
            out.append(parsed)
    return tuple(out)


def membership_configured() -> bool:
    """True when at least one Pro SKU id is set in the environment."""

    return bool(pro_sku_ids())


# ---------------------------------------------------------------------------
# Entitlements (Discord app subscriptions)
# ---------------------------------------------------------------------------

_ENTITLEMENT_CACHE: dict[int, tuple[float, bool]] = {}


def _entitlement_active(payload: Any, sku_ids: tuple[int, ...], *, now: Optional[datetime] = None) -> bool:
    """One entitlement payload counts only for a live app subscription."""

    if not isinstance(payload, Mapping):
        return False
    if _parse_sku_id(payload.get("sku_id")) not in sku_ids:
        return False
    if payload.get("deleted"):
        return False
    entitlement_type = payload.get("type")
    if entitlement_type is not None and entitlement_type != _APP_SUBSCRIPTION_ENTITLEMENT_TYPE:
        return False
    ends_at = payload.get("ends_at")
    if ends_at:
        try:
            parsed = datetime.fromisoformat(str(ends_at).replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed <= (now or datetime.now(timezone.utc)):
            return False
    return True


async def _fetch_entitlements(client: Any, user_id: int, sku_ids: tuple[int, ...]) -> Any:
    """List the user's entitlements through the bot's own Discord HTTP client.

    Uses ``HTTPClient.get_entitlements`` (discord.py) rather than a hand-rolled
    REST call so auth, rate limits and retries stay with the library session
    the bot already runs.
    """

    http = getattr(client, "http", None)
    getter = getattr(http, "get_entitlements", None)
    if getter is None:
        raise RuntimeError("Discord client cannot list entitlements")
    application_id = getattr(client, "application_id", None)
    if not application_id:
        application_id = getattr(getattr(client, "user", None), "id", None)
    if not application_id:
        raise RuntimeError("Discord application id is unavailable")
    return await getter(
        int(application_id),
        user_id=int(user_id),
        sku_ids=list(sku_ids),
        exclude_ended=True,
        exclude_deleted=True,
        limit=100,
    )


async def user_has_active_pro(
    client: Any,
    user_id: int,
    *,
    sku_ids: Optional[tuple[int, ...]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """True when the account holds an active Pro entitlement right now."""

    uid = int(user_id or 0)
    if uid <= 0 or client is None:
        return False
    skus = tuple(sku_ids) if sku_ids is not None else pro_sku_ids()
    if not skus:
        return False
    cached = _ENTITLEMENT_CACHE.get(uid)
    moment = time.monotonic()
    if cached is not None and moment - cached[0] < ENTITLEMENT_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        payloads = await _fetch_entitlements(client, uid, skus)
    except Exception:
        logger.warning("membership entitlement lookup failed for user %s", uid, exc_info=True)
        if cached is not None and cached[1]:
            return True
        return False
    active = any(_entitlement_active(payload, skus, now=now) for payload in (payloads or ()))
    _ENTITLEMENT_CACHE[uid] = (moment, active)
    return active


# ---------------------------------------------------------------------------
# Covered-guild links (PostgreSQL)
# ---------------------------------------------------------------------------


def links_ready_sync() -> bool:
    """Prove the link table exists with the privileges the service needs."""

    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select user_id, guild_id, linked_at "
                    "from public.dank_membership_guild_links limit 0"
                )
                for privilege in ("SELECT", "INSERT", "DELETE"):
                    cursor.execute(
                        "select has_table_privilege(current_user, %s, %s)",
                        (LINKS_TABLE, privilege),
                    )
                    row = cursor.fetchone()
                    if not row or row[0] is not True:
                        return False
        return True
    except Exception:
        return False


def owner_of_guild_sync(guild_id: int) -> Optional[int]:
    gid = int(guild_id or 0)
    if gid <= 0:
        return None
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select user_id from public.dank_membership_guild_links where guild_id = %s",
                (gid,),
            )
            row = cursor.fetchone()
    return int(row[0]) if row else None


def links_for_user_sync(user_id: int) -> tuple[int, ...]:
    uid = int(user_id or 0)
    if uid <= 0:
        return ()
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select guild_id from public.dank_membership_guild_links "
                "where user_id = %s order by linked_at, guild_id",
                (uid,),
            )
            rows = cursor.fetchall()
    return tuple(int(row[0]) for row in rows)


def link_guild_sync(user_id: int, guild_id: int) -> str:
    """Link one guild to a Pro account under the cap and one-owner rules.

    Returns LINK_LINKED, LINK_ALREADY_LINKED, LINK_CAP_REACHED,
    LINK_GUILD_OWNED or LINK_UNAVAILABLE (bad ids or storage failure).
    """

    uid = int(user_id or 0)
    gid = int(guild_id or 0)
    if uid <= 0 or gid <= 0:
        return LINK_UNAVAILABLE
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select user_id from public.dank_membership_guild_links where guild_id = %s",
                    (gid,),
                )
                row = cursor.fetchone()
                if row:
                    return LINK_ALREADY_LINKED if int(row[0]) == uid else LINK_GUILD_OWNED
                cursor.execute(
                    "select count(*) from public.dank_membership_guild_links where user_id = %s",
                    (uid,),
                )
                count_row = cursor.fetchone()
                if count_row and int(count_row[0]) >= MAX_LINKED_GUILDS:
                    return LINK_CAP_REACHED
                cursor.execute(
                    "insert into public.dank_membership_guild_links (user_id, guild_id) values (%s, %s)",
                    (uid, gid),
                )
        return LINK_LINKED
    except Exception:
        logger.warning("membership link failed for user %s guild %s", uid, gid, exc_info=True)
        return LINK_UNAVAILABLE


def unlink_guild_sync(user_id: int, guild_id: int) -> bool:
    uid = int(user_id or 0)
    gid = int(guild_id or 0)
    if uid <= 0 or gid <= 0:
        return False
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "delete from public.dank_membership_guild_links where user_id = %s and guild_id = %s",
                    (uid, gid),
                )
                return int(getattr(cursor, "rowcount", 0) or 0) > 0
    except Exception:
        logger.warning("membership unlink failed for user %s guild %s", uid, gid, exc_info=True)
        return False


async def owner_of_guild(guild_id: int) -> Optional[int]:
    try:
        return await asyncio.to_thread(owner_of_guild_sync, guild_id)
    except Exception:
        logger.warning("membership owner lookup failed for guild %s", guild_id, exc_info=True)
        return None


async def links_for_user(user_id: int) -> tuple[int, ...]:
    try:
        return await asyncio.to_thread(links_for_user_sync, user_id)
    except Exception:
        logger.warning("membership link list failed for user %s", user_id, exc_info=True)
        return ()


async def link_guild(user_id: int, guild_id: int) -> str:
    return await asyncio.to_thread(link_guild_sync, user_id, guild_id)


async def unlink_guild(user_id: int, guild_id: int) -> bool:
    return await asyncio.to_thread(unlink_guild_sync, user_id, guild_id)


# ---------------------------------------------------------------------------
# Guild status + guard resolver
# ---------------------------------------------------------------------------


async def guild_pro_status(guild_id: int, *, client: Any = None) -> str:
    """Resolve one guild to "unconfigured", "pro" or "free"."""

    if not membership_configured():
        return STATUS_UNCONFIGURED
    gid = int(guild_id or 0)
    if gid <= 0:
        return STATUS_FREE
    owner = await owner_of_guild(gid)
    if owner is None:
        return STATUS_FREE
    if client is None:
        logger.warning("membership status for guild %s needs a Discord client to verify the owner", gid)
        return STATUS_FREE
    return STATUS_PRO if await user_has_active_pro(client, owner) else STATUS_FREE


def pro_status_resolver(status: str) -> Callable[[], str]:
    """Adapt an already-resolved status string to the guard's resolver shape.

    The guard is synchronous while status resolution is not, so call sites
    resolve first and inject the outcome with this zero-argument callable.
    """

    resolved = str(status or "")
    return lambda: resolved


__all__ = [
    "ENTITLEMENT_CACHE_TTL_SECONDS",
    "LINKS_TABLE",
    "LINK_ALREADY_LINKED",
    "LINK_CAP_REACHED",
    "LINK_GUILD_OWNED",
    "LINK_LINKED",
    "LINK_UNAVAILABLE",
    "MAX_LINKED_GUILDS",
    "PRO_MONTHLY_SKU_ENV",
    "PRO_YEARLY_SKU_ENV",
    "STATUS_FREE",
    "STATUS_PRO",
    "STATUS_UNCONFIGURED",
    "guild_pro_status",
    "link_guild",
    "link_guild_sync",
    "links_for_user",
    "links_for_user_sync",
    "links_ready_sync",
    "membership_configured",
    "owner_of_guild",
    "owner_of_guild_sync",
    "pro_sku_ids",
    "pro_status_resolver",
    "unlink_guild",
    "unlink_guild_sync",
    "user_has_active_pro",
]
