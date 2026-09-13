from __future__ import annotations

"""Durable hostile-actor reputation and re-entry enforcement.

This runtime is the shared bridge between AntiNuke containment, member re-entry,
SpamGuard's bot exclusions, and hard-proof alt identity evidence.

The policy is deliberately narrow:
- only a confirmed AntiNuke containment or a hard identity link to an already
  confirmed hostile actor creates an active hostile disposition;
- usernames, bot names, profile shape, and heuristic alt similarity never do;
- the disposition is guild-scoped, keyed by Discord user ID, durable in
  Supabase when available, and mirrored to local disk for outage continuity;
- ordinary trusted/legitimate bots remain outside human SpamGuard heuristics.
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import discord

from .globals import get_supabase

REPUTATION_TABLE = "guild_security_actor_reputation"
_REPUTATION_VERSION = 1
_INSTALL_FLAG = "_dank_hostile_actor_runtime_installed"
_PATCH_FLAG = "_dank_hostile_actor_runtime_patched"
_MEMORY: dict[tuple[int, int], dict[str, Any]] = {}
_NEGATIVE_CACHE: dict[tuple[int, int], float] = {}
_NEGATIVE_TTL_SECONDS = 60.0
_FILE_LOCK = threading.RLock()
_ACTOR_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}
_TABLE_AVAILABLE: Optional[bool] = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_text(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _key(guild_id: Any, user_id: Any) -> tuple[int, int]:
    return (_safe_int(guild_id, 0), _safe_int(user_id, 0))


def _lock_for(key: tuple[int, int]) -> asyncio.Lock:
    lock = _ACTOR_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ACTOR_LOCKS[key] = lock
    return lock


def _state_path() -> Path:
    raw = _safe_text(
        os.getenv("DANK_SECURITY_REPUTATION_FILE"),
        "data/security_actor_reputation.json",
    )
    return Path(raw or "data/security_actor_reputation.json")


def _empty_document() -> dict[str, Any]:
    return {"version": _REPUTATION_VERSION, "actors": {}}


def _load_document_unlocked() -> dict[str, Any]:
    path = _state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_document()
    if not isinstance(payload, dict):
        return _empty_document()
    actors = payload.get("actors")
    if not isinstance(actors, dict):
        actors = {}
    return {"version": _REPUTATION_VERSION, "actors": dict(actors)}


def _record_key(guild_id: int, user_id: int) -> str:
    return f"{int(guild_id)}:{int(user_id)}"


def _normalize_record(
    row: Mapping[str, Any] | None,
    *,
    guild_id: int,
    user_id: int,
) -> Optional[dict[str, Any]]:
    if not isinstance(row, Mapping):
        return None
    gid = _safe_int(row.get("guild_id"), guild_id)
    uid = _safe_int(row.get("user_id"), user_id)
    if gid <= 0 or uid <= 0:
        return None
    return {
        "guild_id": gid,
        "user_id": uid,
        "active": bool(row.get("active", True)),
        "classification": _safe_text(
            row.get("classification"),
            "confirmed_destructive_actor",
        ),
        "source": _safe_text(row.get("source"), "antinuke"),
        "is_bot": bool(row.get("is_bot", False)),
        "incident_count": max(1, _safe_int(row.get("incident_count"), 1)),
        "first_seen_at": _safe_text(row.get("first_seen_at"), _utcnow_iso()),
        "last_seen_at": _safe_text(row.get("last_seen_at"), _utcnow_iso()),
        "last_reason": _safe_text(row.get("last_reason")) or None,
        "related_user_id": (
            _safe_int(row.get("related_user_id"), 0) or None
        ),
        "cleared_at": _safe_text(row.get("cleared_at")) or None,
        "cleared_by": _safe_text(row.get("cleared_by")) or None,
        "clear_reason": _safe_text(row.get("clear_reason")) or None,
    }


def _write_local_record(record: Mapping[str, Any]) -> bool:
    gid = _safe_int(record.get("guild_id"), 0)
    uid = _safe_int(record.get("user_id"), 0)
    if gid <= 0 or uid <= 0:
        return False
    path = _state_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        with _FILE_LOCK:
            payload = _load_document_unlocked()
            payload["actors"][_record_key(gid, uid)] = dict(record)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        return True
    except Exception as exc:
        try:
            print(
                "⚠️ security_reputation local persistence failed "
                f"guild={gid} user={uid} error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return False


def _read_local_record(guild_id: int, user_id: int) -> Optional[dict[str, Any]]:
    gid, uid = _key(guild_id, user_id)
    if gid <= 0 or uid <= 0:
        return None
    with _FILE_LOCK:
        payload = _load_document_unlocked()
        raw = payload.get("actors", {}).get(_record_key(gid, uid))
    return _normalize_record(raw, guild_id=gid, user_id=uid)


def _local_active_records(guild_id: int) -> list[dict[str, Any]]:
    gid = _safe_int(guild_id, 0)
    if gid <= 0:
        return []
    with _FILE_LOCK:
        payload = _load_document_unlocked()
        rows = list((payload.get("actors") or {}).values())
    out: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        uid = _safe_int(raw.get("user_id"), 0)
        row = _normalize_record(raw, guild_id=gid, user_id=uid)
        if row and row["guild_id"] == gid and row["active"]:
            out.append(row)
    return out


def _is_missing_table_error(exc: BaseException) -> bool:
    text = repr(exc).lower()
    return (
        REPUTATION_TABLE.lower() in text
        and (
            "does not exist" in text
            or "schema cache" in text
            or "relation" in text
            or "42p01" in text
            or "pgrst204" in text
        )
    )


def _fetch_db_record_sync(guild_id: int, user_id: int) -> tuple[str, Optional[dict[str, Any]]]:
    global _TABLE_AVAILABLE
    sb = get_supabase()
    if sb is None:
        return "unavailable", None
    try:
        result = (
            sb.table(REPUTATION_TABLE)
            .select("*")
            .eq("guild_id", int(guild_id))
            .eq("user_id", int(user_id))
            .limit(1)
            .execute()
        )
        _TABLE_AVAILABLE = True
        rows = getattr(result, "data", None) or []
        return "ok", dict(rows[0]) if rows else None
    except Exception as exc:
        if _is_missing_table_error(exc):
            _TABLE_AVAILABLE = False
            return "missing_table", None
        return f"error:{type(exc).__name__}", None


def _fetch_db_active_sync(guild_id: int) -> tuple[str, list[dict[str, Any]]]:
    global _TABLE_AVAILABLE
    sb = get_supabase()
    if sb is None:
        return "unavailable", []
    try:
        result = (
            sb.table(REPUTATION_TABLE)
            .select("*")
            .eq("guild_id", int(guild_id))
            .eq("active", True)
            .limit(500)
            .execute()
        )
        _TABLE_AVAILABLE = True
        rows = getattr(result, "data", None) or []
        return "ok", [dict(row) for row in rows if isinstance(row, Mapping)]
    except Exception as exc:
        if _is_missing_table_error(exc):
            _TABLE_AVAILABLE = False
            return "missing_table", []
        return f"error:{type(exc).__name__}", []


def _upsert_db_record_sync(record: Mapping[str, Any]) -> bool:
    global _TABLE_AVAILABLE
    sb = get_supabase()
    if sb is None:
        return False
    payload = {
        "guild_id": int(record["guild_id"]),
        "user_id": int(record["user_id"]),
        "active": bool(record["active"]),
        "classification": _safe_text(record.get("classification")),
        "source": _safe_text(record.get("source")),
        "is_bot": bool(record.get("is_bot", False)),
        "incident_count": max(1, _safe_int(record.get("incident_count"), 1)),
        "first_seen_at": record.get("first_seen_at"),
        "last_seen_at": record.get("last_seen_at"),
        "last_reason": record.get("last_reason"),
        "related_user_id": record.get("related_user_id"),
        "cleared_at": record.get("cleared_at"),
        "cleared_by": record.get("cleared_by"),
        "clear_reason": record.get("clear_reason"),
    }
    try:
        (
            sb.table(REPUTATION_TABLE)
            .upsert(payload, on_conflict="guild_id,user_id")
            .execute()
        )
        _TABLE_AVAILABLE = True
        return True
    except Exception as exc:
        if _is_missing_table_error(exc):
            _TABLE_AVAILABLE = False
        else:
            try:
                print(
                    "⚠️ security_reputation DB upsert failed "
                    f"guild={record['guild_id']} user={record['user_id']} "
                    f"error={type(exc).__name__}: {exc}"
                )
            except Exception:
                pass
        return False


async def get_actor_reputation(
    guild_id: int,
    user_id: int,
    *,
    refresh: bool = False,
) -> Optional[dict[str, Any]]:
    gid, uid = _key(guild_id, user_id)
    if gid <= 0 or uid <= 0:
        return None
    cache_key = (gid, uid)

    if not refresh:
        cached = _MEMORY.get(cache_key)
        if isinstance(cached, Mapping):
            return dict(cached)
        negative_at = _NEGATIVE_CACHE.get(cache_key)
        if negative_at is not None and (time.monotonic() - negative_at) < _NEGATIVE_TTL_SECONDS:
            return None

    local = _read_local_record(gid, uid)
    if not refresh and local is not None:
        _MEMORY[cache_key] = dict(local)
        return dict(local)

    status, raw = await asyncio.to_thread(_fetch_db_record_sync, gid, uid)
    if raw is not None:
        normalized = _normalize_record(raw, guild_id=gid, user_id=uid)
        if normalized is not None:
            _MEMORY[cache_key] = dict(normalized)
            _NEGATIVE_CACHE.pop(cache_key, None)
            _write_local_record(normalized)
            return dict(normalized)

    if local is not None:
        # A local confirmed disposition is intentionally fail-safe when the DB is
        # absent/unavailable or an earlier DB write failed.
        _MEMORY[cache_key] = dict(local)
        return dict(local)

    if status == "ok":
        _NEGATIVE_CACHE[cache_key] = time.monotonic()
    return None


async def list_active_reputations(guild_id: int) -> list[dict[str, Any]]:
    gid = _safe_int(guild_id, 0)
    if gid <= 0:
        return []

    merged: dict[int, dict[str, Any]] = {
        row["user_id"]: row for row in _local_active_records(gid)
    }
    status, rows = await asyncio.to_thread(_fetch_db_active_sync, gid)
    if status == "ok":
        for raw in rows:
            uid = _safe_int(raw.get("user_id"), 0)
            normalized = _normalize_record(raw, guild_id=gid, user_id=uid)
            if normalized is None:
                continue
            _MEMORY[(gid, uid)] = dict(normalized)
            _write_local_record(normalized)
            if normalized["active"]:
                merged[uid] = normalized
            else:
                merged.pop(uid, None)

    return list(merged.values())


async def mark_confirmed_hostile(
    guild_id: int,
    user_id: int,
    *,
    classification: str = "confirmed_destructive_actor",
    source: str = "antinuke",
    reason: str = "",
    is_bot: bool = False,
    related_user_id: Optional[int] = None,
) -> dict[str, Any]:
    gid, uid = _key(guild_id, user_id)
    if gid <= 0 or uid <= 0:
        raise ValueError("guild_id and user_id must be positive Discord IDs")

    cache_key = (gid, uid)
    async with _lock_for(cache_key):
        previous = await get_actor_reputation(gid, uid, refresh=True)
        now = _utcnow_iso()
        record = {
            "guild_id": gid,
            "user_id": uid,
            "active": True,
            "classification": _safe_text(
                classification,
                "confirmed_destructive_actor",
            ),
            "source": _safe_text(source, "antinuke"),
            "is_bot": bool(is_bot or (previous or {}).get("is_bot", False)),
            "incident_count": max(
                1,
                _safe_int((previous or {}).get("incident_count"), 0) + 1,
            ),
            "first_seen_at": _safe_text(
                (previous or {}).get("first_seen_at"),
                now,
            ),
            "last_seen_at": now,
            "last_reason": _safe_text(reason) or None,
            "related_user_id": (
                _safe_int(related_user_id, 0)
                or _safe_int((previous or {}).get("related_user_id"), 0)
                or None
            ),
            "cleared_at": None,
            "cleared_by": None,
            "clear_reason": None,
        }
        _MEMORY[cache_key] = dict(record)
        _NEGATIVE_CACHE.pop(cache_key, None)
        _write_local_record(record)
        await asyncio.to_thread(_upsert_db_record_sync, record)
        return dict(record)


async def clear_hostile_reputation(
    guild_id: int,
    user_id: int,
    *,
    cleared_by: Optional[int] = None,
    reason: str = "",
) -> Optional[dict[str, Any]]:
    gid, uid = _key(guild_id, user_id)
    if gid <= 0 or uid <= 0:
        return None

    cache_key = (gid, uid)
    async with _lock_for(cache_key):
        previous = await get_actor_reputation(gid, uid, refresh=True)
        if previous is None:
            return None
        record = dict(previous)
        record["active"] = False
        record["last_seen_at"] = _utcnow_iso()
        record["cleared_at"] = _utcnow_iso()
        record["cleared_by"] = str(_safe_int(cleared_by, 0)) if _safe_int(cleared_by, 0) > 0 else None
        record["clear_reason"] = _safe_text(reason) or None
        _MEMORY[cache_key] = dict(record)
        _NEGATIVE_CACHE.pop(cache_key, None)
        _write_local_record(record)
        await asyncio.to_thread(_upsert_db_record_sync, record)
        return dict(record)


def _actor_id(actor: Any) -> int:
    if isinstance(actor, int):
        return _safe_int(actor, 0)
    return _safe_int(getattr(actor, "id", 0), 0)


def _is_owner(guild: discord.Guild, user_id: int) -> bool:
    return int(user_id) > 0 and int(user_id) == _safe_int(getattr(guild, "owner_id", 0), 0)


def _is_dank_bot(anti_nuke: Any, user_id: int) -> bool:
    try:
        return bool(
            getattr(anti_nuke.bot, "user", None) is not None
            and int(user_id) == int(anti_nuke.bot.user.id)
        )
    except Exception:
        return False


async def _ban_identity(
    guild: discord.Guild,
    user_id: int,
    *,
    member: Any = None,
    reason: str,
) -> tuple[bool, str]:
    if user_id <= 0 or _is_owner(guild, user_id):
        return False, "guild owner cannot be banned by Discord bots"

    target = member
    if target is None:
        try:
            target = guild.get_member(int(user_id))
        except Exception:
            target = None
    if target is None:
        target = discord.Object(id=int(user_id))

    try:
        await guild.ban(target, reason=reason)
        return True, "banned hostile identity"
    except Exception as exc:
        return False, f"ban failed: {type(exc).__name__}"


async def _confirmed_hostile_link(
    member: discord.Member,
    raidguard: Any,
) -> Optional[dict[str, Any]]:
    if bool(getattr(member, "bot", False)):
        return None

    gid = int(member.guild.id)
    uid = int(member.id)
    try:
        hard = await asyncio.to_thread(
            raidguard._load_hard_identity_context,  # noqa: SLF001
            gid,
            uid,
        )
    except Exception:
        return None

    suppressed = {
        _safe_int(value, 0)
        for value in (hard.get("manual_not_linked_ids") or set())
        if _safe_int(value, 0) > 0
    }
    candidates: list[tuple[int, str]] = []
    for row in list(hard.get("proof_matches") or []):
        other = _safe_int(row.get("user_id"), 0)
        if other > 0 and other != uid and other not in suppressed:
            candidates.append((other, "verified identity fingerprint"))
    for row in list(hard.get("manual_confirmed") or []):
        other = _safe_int(row.get("user_id"), 0)
        if other > 0 and other != uid and other not in suppressed:
            candidates.append((other, "staff-confirmed duplicate identity"))

    seen: set[int] = set()
    for other_id, evidence in candidates:
        if other_id in seen:
            continue
        seen.add(other_id)
        reputation = await get_actor_reputation(gid, other_id, refresh=True)
        if reputation and reputation.get("active"):
            return {
                "related_user_id": other_id,
                "evidence": evidence,
                "reputation": reputation,
            }
    return None


async def _enforce_reputation_member(
    member: discord.Member,
    reputation: Mapping[str, Any],
    *,
    anti_nuke: Any,
    source: str,
) -> bool:
    guild = member.guild
    uid = int(member.id)
    if _is_owner(guild, uid) or _is_dank_bot(anti_nuke, uid):
        return False

    try:
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    except Exception as exc:
        print(
            "🚨 hostile actor enforcement could not load AntiNuke settings "
            f"guild={guild.id} user={uid} error={type(exc).__name__}: {exc}"
        )
        return False

    if not bool(settings.get("antinuke_enabled")):
        print(
            "🚨 known hostile identity present while AntiNuke is disabled "
            f"guild={guild.id} user={uid} source={source}"
        )
        return False

    classification = _safe_text(
        reputation.get("classification"),
        "confirmed_destructive_actor",
    )
    related = _safe_int(reputation.get("related_user_id"), 0)
    target_label = f"{member} (`{uid}`)"
    details = (
        f"Persistent security reputation: {classification}. "
        "This decision is keyed by Discord user ID, not username."
    )
    if related > 0:
        details += f" Hard identity evidence links this account to hostile ID {related}."

    if str(settings.get("antinuke_mode") or "").lower() != "contain":
        await anti_nuke._post_incident(  # noqa: SLF001
            guild,
            title="🚨 Known Hostile Identity Re-entered",
            actor=member,
            action_label=f"Known hostile re-entry ({source})",
            target_label=target_label,
            response_label="Alert-only mode: no automatic removal was attempted.",
            details=details,
        )
        return True

    banned, ban_result = await _ban_identity(
        guild,
        uid,
        member=member,
        reason=(
            "Dank Shield security reputation: known hostile identity re-entry "
            f"({classification})"
        ),
    )
    response = ban_result
    if not banned:
        try:
            removed, blocked = await anti_nuke._contain_actor(  # noqa: SLF001
                guild,
                member,
                reason="Dank Shield security reputation re-entry containment",
            )
        except Exception as exc:
            removed, blocked = [], [f"fallback failed: {type(exc).__name__}"]
        if removed:
            response += " • fallback: " + ", ".join(removed)
        if blocked:
            response += " • blockers: " + ", ".join(blocked)

    await anti_nuke._post_incident(  # noqa: SLF001
        guild,
        title="🛑 Known Hostile Identity Blocked",
        actor=member,
        action_label=f"Known hostile re-entry ({source})",
        target_label=target_label,
        response_label=response,
        details=details,
    )
    return True


def _patch_antinuke(anti_nuke: Any) -> None:
    if bool(getattr(anti_nuke, _PATCH_FLAG, False)):
        return

    original_contain = anti_nuke._contain_actor  # noqa: SLF001
    original_health = anti_nuke.antinuke_permission_health

    async def durable_contain(
        guild: discord.Guild,
        actor: Any,
        *,
        reason: str,
    ) -> tuple[list[str], list[str]]:
        actor_id = _actor_id(actor)
        if actor_id <= 0 or _is_owner(guild, actor_id):
            return await original_contain(guild, actor, reason=reason)

        member = actor if isinstance(actor, discord.Member) else None
        if member is None:
            try:
                member = guild.get_member(actor_id)
            except Exception:
                member = None

        try:
            await mark_confirmed_hostile(
                int(guild.id),
                actor_id,
                classification="confirmed_destructive_actor",
                source="antinuke",
                reason=reason,
                is_bot=bool(getattr(member or actor, "bot", False)),
            )
        except Exception as exc:
            print(
                "🚨 AntiNuke hostile reputation write failed before containment "
                f"guild={guild.id} user={actor_id} error={type(exc).__name__}: {exc}"
            )

        banned, _ = await _ban_identity(
            guild,
            actor_id,
            member=member,
            reason=f"{reason} • durable AntiNuke containment",
        )
        if banned:
            return ["banned member from server"], []

        # Preserve canonical role-strip/kick fallback if Discord denies the ban.
        return await original_contain(guild, actor, reason=reason)

    def reputation_aware_health(
        guild: discord.Guild,
        settings: Optional[Mapping[str, Any]] = None,
    ) -> list[str]:
        missing = list(original_health(guild, settings))
        clean = anti_nuke.normalize_antinuke_settings(settings or {})
        if (
            clean.get("antinuke_enabled")
            and clean.get("antinuke_mode") == "contain"
        ):
            me = getattr(guild, "me", None)
            permissions = getattr(me, "guild_permissions", None)
            if permissions is not None and not bool(
                getattr(permissions, "ban_members", False)
                or getattr(permissions, "administrator", False)
            ):
                if "Ban Members" not in missing:
                    missing.append("Ban Members")
        return missing

    anti_nuke._contain_actor = durable_contain  # noqa: SLF001
    anti_nuke.antinuke_permission_health = reputation_aware_health
    setattr(anti_nuke, _PATCH_FLAG, True)


def _patch_guardian(guardian: Any, anti_nuke: Any) -> None:
    marker = "_dank_hostile_bot_add_patched"
    if bool(getattr(guardian, marker, False)):
        return

    original = guardian._handle_bot_add  # noqa: SLF001

    async def wrapped(guild: discord.Guild, entry: Any, actor: Any) -> None:
        target = getattr(entry, "target", None)
        target_id = _actor_id(target)
        if target_id <= 0:
            return await original(guild, entry, actor)

        reputation = await get_actor_reputation(
            int(guild.id),
            target_id,
            refresh=True,
        )
        if not reputation or not reputation.get("active"):
            return await original(guild, entry, actor)

        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if not settings.get("antinuke_enabled"):
            print(
                "🚨 known hostile bot was added while AntiNuke is disabled "
                f"guild={guild.id} bot={target_id} actor={_actor_id(actor)}"
            )
            return

        response_parts: list[str] = []
        if str(settings.get("antinuke_mode") or "").lower() == "contain":
            banned, result = await _ban_identity(
                guild,
                target_id,
                member=target,
                reason="Dank Shield AntiNuke: previously confirmed hostile bot re-added",
            )
            response_parts.append(result)

            actor_id = _actor_id(actor)
            if (
                actor_id > 0
                and not _is_owner(guild, actor_id)
                and not _is_dank_bot(anti_nuke, actor_id)
            ):
                removed, blocked = await anti_nuke._contain_actor(  # noqa: SLF001
                    guild,
                    actor,
                    reason=(
                        "Dank Shield AntiNuke: re-added a previously confirmed "
                        "hostile bot"
                    ),
                )
                if removed:
                    response_parts.append("inviter: " + ", ".join(removed))
                if blocked:
                    response_parts.append("inviter blockers: " + ", ".join(blocked))
            if not banned and not response_parts:
                response_parts.append("known hostile bot removal failed")
        else:
            response_parts.append(
                "alert-only mode: known hostile bot was not automatically removed"
            )

        await anti_nuke._post_incident(  # noqa: SLF001
            guild,
            title="🛑 Known Hostile Bot Re-add Detected",
            actor=actor,
            action_label="Previously confirmed hostile bot added again",
            target_label=f"{target} (`{target_id}`)",
            response_label=" • ".join(response_parts),
            details=(
                "The target Discord ID already had an active guild-scoped hostile "
                "security disposition. Re-entry bypassed normal first-seen heuristics."
            ),
        )

    guardian._handle_bot_add = wrapped  # noqa: SLF001
    setattr(guardian, marker, True)


def _patch_spam_guard(spam_guard: Any, anti_nuke: Any) -> None:
    marker = "_dank_hostile_reputation_spam_patched"
    if bool(getattr(spam_guard, marker, False)):
        return

    original_message = spam_guard.handle_incoming_spam_message
    original_shield = spam_guard.record_invite_shield_block

    async def wrapped_message(message: discord.Message) -> bool:
        guild = getattr(message, "guild", None)
        member = getattr(message, "author", None)
        if guild is not None and isinstance(member, discord.Member):
            reputation = await get_actor_reputation(
                int(guild.id),
                int(member.id),
                refresh=False,
            )
            if reputation and reputation.get("active"):
                try:
                    await message.delete()
                except Exception:
                    pass
                await _enforce_reputation_member(
                    member,
                    reputation,
                    anti_nuke=anti_nuke,
                    source="SpamGuard prefilter",
                )
                return True
        return await original_message(message)

    async def wrapped_shield(
        message: discord.Message,
        codes: list[str],
        *,
        source: str = "invite-shield",
    ) -> bool:
        guild = getattr(message, "guild", None)
        member = getattr(message, "author", None)
        if guild is not None and isinstance(member, discord.Member):
            reputation = await get_actor_reputation(
                int(guild.id),
                int(member.id),
                refresh=False,
            )
            if reputation and reputation.get("active"):
                await _enforce_reputation_member(
                    member,
                    reputation,
                    anti_nuke=anti_nuke,
                    source=f"{source} reputation prefilter",
                )
                return True
        return await original_shield(message, codes, source=source)

    spam_guard.handle_incoming_spam_message = wrapped_message
    spam_guard.record_invite_shield_block = wrapped_shield
    setattr(spam_guard, marker, True)


async def _on_member_join(member: discord.Member) -> None:
    from . import anti_nuke
    from . import raidguard

    gid = int(member.guild.id)
    uid = int(member.id)
    if _is_owner(member.guild, uid) or _is_dank_bot(anti_nuke, uid):
        return

    reputation = await get_actor_reputation(gid, uid, refresh=True)
    if reputation is None or not reputation.get("active"):
        link = await _confirmed_hostile_link(member, raidguard)
        if link is not None:
            related_id = int(link["related_user_id"])
            evidence = str(link["evidence"])
            reputation = await mark_confirmed_hostile(
                gid,
                uid,
                classification="confirmed_hostile_identity_link",
                source="member_identity",
                reason=(
                    f"{evidence} links this account to confirmed hostile "
                    f"Discord ID {related_id}"
                ),
                is_bot=False,
                related_user_id=related_id,
            )

    if reputation and reputation.get("active"):
        await _enforce_reputation_member(
            member,
            reputation,
            anti_nuke=anti_nuke,
            source="member join",
        )


async def _prewarm_and_reconcile(bot: discord.Client) -> None:
    from . import anti_nuke

    for guild in list(getattr(bot, "guilds", []) or []):
        try:
            rows = await list_active_reputations(int(guild.id))
        except Exception as exc:
            print(
                "⚠️ hostile reputation prewarm failed "
                f"guild={getattr(guild, 'id', 'unknown')} "
                f"error={type(exc).__name__}: {exc}"
            )
            continue

        for row in rows:
            user_id = _safe_int(row.get("user_id"), 0)
            if user_id <= 0:
                continue
            try:
                member = guild.get_member(user_id)
            except Exception:
                member = None
            if not isinstance(member, discord.Member):
                continue
            await _enforce_reputation_member(
                member,
                row,
                anti_nuke=anti_nuke,
                source="startup reconciliation",
            )


def install_hostile_actor_runtime(bot: discord.Client) -> bool:
    """Install one authoritative hostile-identity bridge.

    Installation happens after the AntiNuke incident runtime so this wrapper sits
    outside the existing config/outage and owner-compromise policy wrappers.
    """
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False

    from . import anti_nuke
    from . import anti_nuke_guardian_runtime as guardian
    from . import spam_guard

    _patch_antinuke(anti_nuke)
    _patch_guardian(guardian, anti_nuke)
    _patch_spam_guard(spam_guard, anti_nuke)

    adder = getattr(bot, "add_listener", None)
    if not callable(adder):
        return False

    adder(_on_member_join, "on_member_join")

    async def _on_ready_hostile_reconcile() -> None:
        await _prewarm_and_reconcile(bot)

    adder(_on_ready_hostile_reconcile, "on_ready")
    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🛡️ Hostile actor reputation active: durable exact-ID containment, "
        "known-hostile bot re-entry interception, SpamGuard prefilter bridge, "
        "hard-proof linked-alt inheritance, and startup reconciliation enabled"
    )
    return True


__all__ = [
    "REPUTATION_TABLE",
    "clear_hostile_reputation",
    "get_actor_reputation",
    "install_hostile_actor_runtime",
    "list_active_reputations",
    "mark_confirmed_hostile",
]
