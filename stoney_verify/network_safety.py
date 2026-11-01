from __future__ import annotations

"""Owner-consented cross-server *advisory* history, not a moderation engine.

The guild-scoped AntiNuke reputation and Member Connection Intelligence remain
authoritative for their own purposes. No existing record is published here
automatically. Backend service-role reads never expose raw rows to members.
"""

import asyncio
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

import discord

from .globals import get_supabase

CONSENT_TABLE = "network_safety_consent"
EVENT_TABLE = "network_safety_events"
ALERT_TABLE = "network_safety_alert_receipts"
CATEGORIES = ("harassment", "scam", "spam", "raid", "threats", "other")
_SNOWFLAKE = re.compile(r"^[0-9]{15,22}$")
_EVENT_LIMIT = 150
_DISPLAY_LIMIT = 40


def _id(value: Any) -> str:
    value = str(value or "").strip()
    if not _SNOWFLAKE.fullmatch(value):
        raise ValueError("A valid Discord account or server ID is required")
    return value


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _rows(response: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (getattr(response, "data", None) or [])
            if isinstance(row, Mapping)]


def get_network_consent(guild_id: int) -> dict[str, Any] | None:
    """None means not configured OR unavailable. Both must fail closed."""
    try:
        rows = _rows(get_supabase().table(CONSENT_TABLE)
                     .select("*").eq("guild_id", _id(guild_id))
                     .limit(1).execute())
        return rows[0] if rows else None
    except Exception:
        return None


def set_network_consent(
    guild_id: int, owner_id: int, guild_label: str, *,
    publish: bool | None = None, receive: bool | None = None,
) -> dict[str, Any]:
    """Only call after checking the physical Discord guild owner in the UI."""
    gid, owner = _id(guild_id), _id(owner_id)
    old = get_network_consent(guild_id)
    # Missing data and failed reads are indistinguishable: writing false for
    # the other flag is safe; it never silently grants sharing.
    payload = {
        "guild_id": gid, "owner_id": owner,
        "guild_label": _text(guild_label, 80) or "Participating server",
        "publish_enabled": bool(old and old.get("publish_enabled") and old.get("owner_id") == owner),
        "receive_enabled": bool(old and old.get("receive_enabled") and old.get("owner_id") == owner),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if publish is not None:
        payload["publish_enabled"] = bool(publish)
    if receive is not None:
        payload["receive_enabled"] = bool(receive)
    db = get_supabase()
    if db is None:
        raise RuntimeError("Safety network database unavailable")
    db.table(CONSENT_TABLE).upsert(payload, on_conflict="guild_id").execute()
    confirmed = get_network_consent(guild_id)
    if not confirmed or any(confirmed.get(k) != payload[k] for k in ("owner_id", "publish_enabled", "receive_enabled")):
        raise RuntimeError("Network preference could not be verified; sharing state is unknown")
    return confirmed


def record_network_event(
    guild_id: int, owner_id: int, subject_user_id: int, *,
    event_key: str, category: str, reason: str,
    action: str = "incident", parent_id: str | None = None,
) -> str:
    """Append one owner-attested event. Never label this as audited proof."""
    gid, owner, subject = _id(guild_id), _id(owner_id), _id(subject_user_id)
    category = str(category or "").lower()
    action = str(action or "").lower()
    note = _text(reason, 500)
    if category not in CATEGORIES or action not in ("incident", "correction", "withdrawal"):
        raise ValueError("Choose a supported category and action")
    if len(note) < 8:
        raise ValueError("Provide a specific reason (at least eight characters)")
    if not str(event_key or "").isdigit():
        raise ValueError("Invalid incident submission identifier")
    parent = None
    if action != "incident":
        try:
            parent = str(uuid.UUID(str(parent_id)))
        except Exception as exc:
            raise ValueError("Choose an original incident to correct or withdraw") from exc
    elif parent_id is not None:
        raise ValueError("A new incident must not refer to an older report")
    consent = get_network_consent(guild_id)
    if not consent or str(consent.get("owner_id")) != owner:
        raise RuntimeError("Server owner must configure Safety Network first")
    db = get_supabase()
    if db is None:
        raise RuntimeError("Safety network database unavailable")
    if action != "incident":
        roots = _rows(db.table(EVENT_TABLE).select("id,action")
                      .eq("id", parent).eq("reporting_guild_id", gid)
                      .eq("subject_user_id", subject).limit(1).execute())
        if not roots or roots[0].get("action") != "incident":
            raise PermissionError("Only this server's original report can be changed")
    payload = {
        "event_key": str(event_key), "reporting_guild_id": gid,
        "subject_user_id": subject, "author_id": owner,
        "action": action, "category": category, "reason": note,
        "parent_event_id": parent,
        "evidence_level": "owner_attested",
    }
    result = _rows(db.table(EVENT_TABLE).insert(payload).execute())
    if not result or not result[0].get("id"):
        raise RuntimeError("Incident was not acknowledged by the database")
    return str(result[0]["id"])


def _load_subject_rows(subject_user_id: int) -> list[dict[str, Any]]:
    db = get_supabase()
    if db is None:
        raise RuntimeError("Safety network database unavailable")
    rows = _rows(db.table(EVENT_TABLE)
                 .select("id,reporting_guild_id,subject_user_id,action,category,reason,parent_event_id,evidence_level,occurred_at,created_at")
                 .eq("subject_user_id", _id(subject_user_id))
                 .order("created_at", desc=True).limit(_EVENT_LIMIT).execute())
    # PostgREST may silently truncate; don't present incomplete records as a
    # comprehensive safety history.
    if len(rows) >= _EVENT_LIMIT:
        raise RuntimeError("Safety report history exceeds safe query limit")
    return rows


def _source_consents(guild_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not guild_ids:
        return {}
    db = get_supabase()
    if db is None:
        raise RuntimeError("Safety network database unavailable")
    rows = _rows(db.table(CONSENT_TABLE)
                 .select("guild_id,guild_label,owner_id,publish_enabled")
                 .in_("guild_id", sorted(guild_ids)).limit(len(guild_ids)).execute())
    return {str(row["guild_id"]): row for row in rows if row.get("publish_enabled") is True}


def _reconcile_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Updates are separate append-only events; withdrawal hides active warnings."""
    roots: dict[str, dict[str, Any]] = {}
    updates: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: (str(r.get("created_at") or ""), str(r.get("id") or ""))):
        if row.get("action") == "incident" and not row.get("parent_event_id"):
            root = dict(row)
            root["active"] = True
            root["last_changed"] = str(row.get("created_at") or "")
            roots[str(row["id"])] = root
        else:
            updates.append(row)
    for row in updates:
        root = roots.get(str(row.get("parent_event_id") or ""))
        if root is None or root.get("reporting_guild_id") != row.get("reporting_guild_id"):
            continue
        if root.get("subject_user_id") != row.get("subject_user_id"):
            continue
        if str(row.get("created_at") or "") < str(root.get("last_changed") or ""):
            continue
        root["last_changed"] = str(row.get("created_at") or "")
        if row.get("action") == "withdrawal":
            root["active"] = False
        elif row.get("action") == "correction":
            root["active"] = True
            root["category"] = row.get("category")
            root["reason"] = row.get("reason")
            root["evidence_level"] = row.get("evidence_level")
    return sorted(
        (row for row in roots.values() if row.get("active")),
        key=lambda row: str(row.get("last_changed") or ""), reverse=True,
    )


def active_network_reports(
    receiving_guild: discord.Guild, subject_user_id: int, bot: Any,
) -> list[dict[str, Any]]:
    """Only exact-ID records from currently opted-in servers still in this bot."""
    receiver = get_network_consent(int(receiving_guild.id))
    if not receiver or receiver.get("receive_enabled") is not True:
        return []
    if str(receiver.get("owner_id")) != str(getattr(receiving_guild, "owner_id", "")):
        return []  # owner transfer requires renewed consent

    rows = _load_subject_rows(subject_user_id)
    gid = str(receiving_guild.id)
    candidates = {str(row["reporting_guild_id"]) for row in rows
                  if str(row.get("reporting_guild_id", "")) != gid}
    sources = _source_consents(candidates)
    valid: dict[str, dict[str, Any]] = {}
    for source_id, consent in sources.items():
        try:
            source_guild = bot.get_guild(int(source_id))
            if source_guild is None or str(source_guild.owner_id) != str(consent.get("owner_id")):
                continue
            valid[source_id] = consent
        except Exception:
            continue
    matched = [row for row in rows if str(row.get("reporting_guild_id")) in valid]
    active = _reconcile_events(matched)
    for row in active:
        source_id = str(row["reporting_guild_id"])
        row["source_label"] = _text(valid[source_id].get("guild_label"), 80) or "Participating server"
    return active[:_DISPLAY_LIMIT]


def _digest(rows: list[dict[str, Any]]) -> str:
    payload = [(str(row.get("id")), str(row.get("last_changed")),
                str(row.get("category")), str(row.get("reason")))
               for row in rows]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def claim_network_alert(guild_id: int, user_id: int, digest: str) -> bool:
    db = get_supabase()
    if db is None:
        return False
    result = db.rpc("claim_network_safety_alert", {
        "p_guild": _id(guild_id), "p_user": _id(user_id),
        "p_digest": digest, "p_token": str(uuid.uuid4()),
    }).execute()
    return getattr(result, "data", None) is True


def advisory_text(subject_id: int, reports: list[dict[str, Any]]) -> str:
    """Plain-language, bounded private owner notice; neutral about guilt."""
    lines = [
        "🛡️ **Dank Shield Safety Network advisory**",
        f"A member just joined: Discord account `{subject_id}`.",
        f"Up to {len(reports)} active owner-attested report(s) from participating servers.",
        "**These are reports, not proven wrongdoing. Review before taking action.**",
    ]
    for row in reports[:4]:
        label = discord.utils.escape_mentions(discord.utils.escape_markdown(
            _text(row.get("source_label"), 70)))
        reason = discord.utils.escape_mentions(discord.utils.escape_markdown(
            _text(row.get("reason"), 160)))
        category = _text(row.get("category"), 30)
        when = _text(row.get("occurred_at"), 32)[:10]
        lines.append(f"• **{label}** | {category} | {when}: {reason}")
    if len(reports) > 4:
        lines.append(f"Plus {len(reports)-4} additional active reports. Use /dank safety-network to review.")
    lines.append("Advisory only. No automatic ban, kick, restriction or verification denial.")
    return "\n".join(lines)[:1900]


async def alert_on_member_join(member: discord.Member, bot: Any) -> None:
    """Read-only until a qualified advisory has a private owner destination."""
    guild = getattr(member, "guild", None)
    if guild is None or getattr(member, "id", None) is None:
        return
    try:
        receiver = await asyncio.to_thread(get_network_consent, int(guild.id))
        if not receiver or receiver.get("receive_enabled") is not True:
            return
        if str(receiver.get("owner_id")) != str(getattr(guild, "owner_id", "")):
            return
        owner = getattr(guild, "owner", None) or bot.get_user(int(guild.owner_id))
        if owner is None:
            owner = await bot.fetch_user(int(guild.owner_id))
        reports = await asyncio.wait_for(
            asyncio.to_thread(active_network_reports, guild, int(member.id), bot),
            timeout=6.0,
        )
        if not reports:
            return
        digest = _digest(reports)
        claimed = await asyncio.to_thread(claim_network_alert, int(guild.id), int(member.id), digest)
        if not claimed:
            return
        await owner.send(
            advisory_text(int(member.id), reports),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        # Reporting/DM access is advisory only. An outage never interrupts
        # verification, member onboarding, or guild security.
        print(f"⚠️ safety_network advisory unavailable guild={getattr(guild,'id',0)} error={type(exc).__name__}")


async def reports_for_owner(guild: discord.Guild, subject_id: int, bot: Any) -> list[dict[str, Any]]:
    """Interactive lookup uses the same receiver consent and owner checks."""
    return await asyncio.to_thread(active_network_reports, guild, subject_id, bot)
