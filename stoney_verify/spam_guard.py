from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import discord
from discord import app_commands
from discord.ext import tasks

from .globals import *  # noqa: F401,F403

# ============================================================
# Spam / hacked-account guard with compact multi-page control UI
# ============================================================

GUILD_SECURITY_SETTINGS_TABLE = "guild_security_settings"
QUARANTINE_CASES_TABLE = "guild_security_quarantine_cases"

SPAM_PANEL_FOOTER_BASE = "stoney_verify:spam_guard_panel:v11"
SPAM_PANEL_FOOTER_PREFIX = "stoney_verify:spam_guard_panel:"
SPAM_PANEL_PAGES = ("overview", "detection", "enforcement", "access")

SPAM_INCIDENT_FOOTER_PREFIX = "stoney_verify:spam_guard_incident"
SPAM_INCIDENT_RESTORE_CUSTOM_ID = "spamguard:incident:restore"

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)/([A-Za-z0-9-]+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
INCIDENT_FOOTER_RE = re.compile(
    r"stoney_verify:spam_guard_incident\|case=(?P<case>[^|]+)\|guild=(?P<guild>\d+)\|user=(?P<user>\d+)",
    re.IGNORECASE,
)

VALID_MODES = {"log_only", "delete_only", "timeout", "quarantine", "kick", "ban"}

# Settings cache
SETTINGS_CACHE_TTL_SECONDS = 180

# Production cleanup / anti-leak tuning
STALE_WINDOW_TTL_SECONDS = 900
STALE_LOCK_TTL_SECONDS = 900
STALE_INVITE_CACHE_TTL_SECONDS = 1800
RESTORED_CASE_RETENTION_SECONDS = 7 * 24 * 60 * 60

# Rapid URL flood protections for likely compromised accounts.
UNVERIFIED_SINGLE_MESSAGE_URL_TRIGGER = 2
UNVERIFIED_URL_MESSAGE_THRESHOLD = 2
UNVERIFIED_URL_CHANNEL_THRESHOLD = 2

EVIDENCE_ANY_INVITE = "invite_url"
EVIDENCE_BLOCKED_INVITE = "blocked_invite"
EVIDENCE_NON_INVITE_URL = "non_invite_url"
EVIDENCE_EVERYONE_PING = "everyone_ping"

_SETTINGS_TABLE_AVAILABLE: Optional[bool] = None
_CASES_TABLE_AVAILABLE: Optional[bool] = None
_SPAM_GUARD_COMMANDS_REGISTERED = False
_SPAM_GUARD_VIEWS_REGISTERED = False

_RUNTIME_SETTINGS: Dict[int, Dict[str, Any]] = {}
_MESSAGE_WINDOWS: Dict[Tuple[int, int], Dict[str, Any]] = {}
_LOCKS: Dict[str, asyncio.Lock] = {}
_LOCK_LAST_USED: Dict[str, float] = {}
_GUILD_INVITE_CACHE: Dict[int, Dict[str, Any]] = {}
_QUARANTINE_CASES: Dict[str, Dict[str, Any]] = {}


def _lock(key: str) -> asyncio.Lock:
    clean = str(key or "").strip() or "default"
    found = _LOCKS.get(clean)
    if found is None:
        found = asyncio.Lock()
        _LOCKS[clean] = found
    _LOCK_LAST_USED[clean] = time.monotonic()
    return found


def _touch_lock(key: str) -> None:
    clean = str(key or "").strip() or "default"
    _LOCK_LAST_USED[clean] = time.monotonic()


def _now_utc() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


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
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _debug(msg: str) -> None:
    try:
        print(f"🛡️ spam_guard {msg}")
    except Exception:
        pass


def _sb():
    try:
        return get_supabase()
    except Exception:
        return None


def _is_table_missing_error(exc: Exception, table_name: str) -> bool:
    text = repr(exc or "").lower()
    return (
        table_name.lower() in text
        and (
            "does not exist" in text
            or "schema cache" in text
            or "relation" in text
            or "42p01" in text
            or "pgrst204" in text
        )
    )


def _is_staffish(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator:
            return True
        if member.guild_permissions.manage_guild:
            return True
        if member.guild_permissions.manage_channels:
            return True
        if member.guild_permissions.manage_messages:
            return True

        staff_role_id = int(STAFF_ROLE_ID or 0)
        if staff_role_id > 0:
            return any(int(r.id) == staff_role_id for r in (member.roles or []))
    except Exception:
        pass
    return False


def _is_verifiedish(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False

        verified_ids = [
            int(VERIFIED_ROLE_ID or 0),
            int(RESIDENT_ROLE_ID or 0),
            int(STONER_ROLE_ID or 0),
            int(DRUNKEN_ROLE_ID or 0),
        ]
        wanted = {rid for rid in verified_ids if rid > 0}
        if not wanted:
            return False

        return any(int(r.id) in wanted for r in (member.roles or []))
    except Exception:
        return False


def _normalize_id_list(values: Any, *, limit: int = 100) -> List[str]:
    out: List[str] = []
    try:
        if isinstance(values, list):
            source = list(values)
        elif values is None:
            source = []
        else:
            source = [values]

        for raw in source:
            text = _safe_str(raw)
            if not text or not text.isdigit():
                continue
            if text not in out:
                out.append(text)
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def _normalize_code_list(values: Any, *, limit: int = 100) -> List[str]:
    out: List[str] = []
    try:
        if isinstance(values, list):
            source = list(values)
        elif values is None:
            source = []
        else:
            source = [values]

        for raw in source:
            text = _safe_str(raw).lower()
            if not text:
                continue
            if text not in out:
                out.append(text)
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


def _parse_csvish_ids(text: str, *, limit: int = 100) -> List[str]:
    out: List[str] = []
    for part in re.split(r"[\s,;\n]+", _safe_str(text)):
        piece = part.strip().strip("<@#&!>").strip()
        if not piece or not piece.isdigit():
            continue
        if piece not in out:
            out.append(piece)
        if len(out) >= limit:
            break
    return out


def _parse_csvish_codes(text: str, *, limit: int = 100) -> List[str]:
    out: List[str] = []
    for part in re.split(r"[\s,;\n]+", _safe_str(text)):
        code = part.strip().strip("/").strip()
        code = code.replace("https://discord.gg/", "").replace("http://discord.gg/", "")
        code = code.replace("https://discord.com/invite/", "").replace("http://discord.com/invite/", "")
        code = code.replace("https://discordapp.com/invite/", "").replace("http://discordapp.com/invite/", "")
        code = code.strip().lower()
        if not code:
            continue
        if code not in out:
            out.append(code)
        if len(out) >= limit:
            break
    return out


def _format_role_list(guild: discord.Guild, ids: List[str], *, max_items: int = 6) -> str:
    items: List[str] = []
    for rid in ids[:max_items]:
        try:
            role = guild.get_role(int(rid))
            items.append(role.mention if isinstance(role, discord.Role) else f"`{rid}`")
        except Exception:
            items.append(f"`{rid}`")
    if not items:
        return "—"
    extra = f" +{len(ids) - max_items} more" if len(ids) > max_items else ""
    return ", ".join(items) + extra


def _format_channel_list(guild: discord.Guild, ids: List[str], *, max_items: int = 6) -> str:
    items: List[str] = []
    for cid in ids[:max_items]:
        try:
            ch = guild.get_channel(int(cid))
            items.append(ch.mention if isinstance(ch, discord.abc.GuildChannel) else f"`{cid}`")
        except Exception:
            items.append(f"`{cid}`")
    if not items:
        return "—"
    extra = f" +{len(ids) - max_items} more" if len(ids) > max_items else ""
    return ", ".join(items) + extra


def _format_user_list(guild: discord.Guild, ids: List[str], *, max_items: int = 6) -> str:
    items: List[str] = []
    for uid in ids[:max_items]:
        try:
            member = guild.get_member(int(uid))
            items.append(member.mention if isinstance(member, discord.Member) else f"`{uid}`")
        except Exception:
            items.append(f"`{uid}`")
    if not items:
        return "—"
    extra = f" +{len(ids) - max_items} more" if len(ids) > max_items else ""
    return ", ".join(items) + extra


def _normalize_mode(value: Any, default: str = "timeout") -> str:
    text = _safe_str(value).lower()
    return text if text in VALID_MODES else default


def _member_has_any_role(member: Optional[discord.Member], role_ids: List[str]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        wanted = {int(x) for x in role_ids if str(x).isdigit()}
        if not wanted:
            return False
        return any(int(r.id) in wanted for r in (member.roles or []))
    except Exception:
        return False


def _normalize_message_content(content: str) -> str:
    text = _safe_str(content).lower()
    text = INVITE_RE.sub("<invite>", text)
    text = URL_RE.sub("<url>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:250]


def _extract_invite_codes(content: str) -> List[str]:
    try:
        return list(dict.fromkeys([code.strip().lower() for code in INVITE_RE.findall(content or "") if code.strip()]))
    except Exception:
        return []


def _extract_urls(content: str) -> List[str]:
    try:
        raw = [u.strip() for u in URL_RE.findall(content or "") if u.strip()]
        return list(dict.fromkeys(raw))
    except Exception:
        return []


def _is_discord_invite_url(url: str) -> bool:
    try:
        return bool(INVITE_RE.search(url or ""))
    except Exception:
        return False


def _extract_non_invite_urls(content: str) -> List[str]:
    urls = _extract_urls(content)
    return [u for u in urls if not _is_discord_invite_url(u)]


def _message_mentions_everyone(message: discord.Message) -> bool:
    try:
        return bool(message.mention_everyone)
    except Exception:
        return False


def _panel_footer(page: str) -> str:
    clean_page = page if page in SPAM_PANEL_PAGES else "overview"
    return f"{SPAM_PANEL_FOOTER_BASE} • page={clean_page}"


def _page_title(page: str) -> str:
    mapping = {
        "overview": "Overview",
        "detection": "Detection Rules",
        "enforcement": "Response Actions",
        "access": "Allow Lists + Exemptions",
    }
    return mapping.get(page, "Overview")


def _bool_chip(value: bool) -> str:
    return "✅ On" if value else "❌ Off"


def _compact_count(label: str, items: List[Any]) -> str:
    return f"**{label}:** `{len(items)}`"


def _quarantine_role_text(guild: discord.Guild, settings: Dict[str, Any]) -> str:
    qrid = _safe_str(settings.get("quarantine_role_id"))
    if not qrid.isdigit():
        return "—"
    role = guild.get_role(int(qrid))
    return role.mention if isinstance(role, discord.Role) else f"`{qrid}`"


def _cache_runtime_settings(
    guild_id: int,
    settings: Dict[str, Any],
    *,
    source: str,
    persisted: bool,
) -> Dict[str, Any]:
    gid = int(guild_id)
    payload = dict(_normalize_settings(gid, settings))
    payload["__meta_loaded_at"] = float(time.monotonic())
    payload["__meta_source"] = str(source or "runtime")
    payload["__meta_persisted"] = bool(persisted)
    _RUNTIME_SETTINGS[gid] = payload
    return dict(payload)


def _cached_runtime_settings(guild_id: int) -> Optional[Dict[str, Any]]:
    payload = _RUNTIME_SETTINGS.get(int(guild_id))
    if isinstance(payload, dict):
        return dict(payload)
    return None


def _cache_age_seconds(payload: Dict[str, Any]) -> float:
    try:
        loaded_at = float(payload.get("__meta_loaded_at", 0.0) or 0.0)
        if loaded_at <= 0.0:
            return 999999.0
        return max(0.0, time.monotonic() - loaded_at)
    except Exception:
        return 999999.0


def _cache_is_fresh(payload: Dict[str, Any]) -> bool:
    return _cache_age_seconds(payload) <= float(SETTINGS_CACHE_TTL_SECONDS)


def _cache_persisted(payload: Dict[str, Any]) -> bool:
    try:
        return bool(payload.get("__meta_persisted"))
    except Exception:
        return False


def _build_persistence_label(
    guild_id: int,
    persisted_hint: Optional[bool] = None,
) -> str:
    if persisted_hint is True:
        return "DB-backed"
    if persisted_hint is False:
        return "Runtime only (resets on restart)"

    cached = _cached_runtime_settings(guild_id)
    if isinstance(cached, dict) and _cache_persisted(cached):
        return "DB-backed"

    return "Runtime only (resets on restart)"


def _fast_settings_for_ui(guild_id: int) -> Dict[str, Any]:
    gid = int(guild_id)
    cached = _cached_runtime_settings(gid)
    if isinstance(cached, dict):
        return _normalize_settings(gid, cached)
    return _default_settings(gid)


def _validated_int(value: str, *, label: str, min_value: int, max_value: int) -> int:
    text = _safe_str(value)
    if not text or not re.fullmatch(r"\d+", text):
        raise ValueError(f"{label} must be a whole number.")
    parsed = int(text)
    if parsed < min_value or parsed > max_value:
        raise ValueError(f"{label} must be between {min_value} and {max_value}.")
    return parsed


def _row_identity(row: Dict[str, Any]) -> Tuple[int, int]:
    return (
        _safe_int(row.get("channel_id"), 0),
        _safe_int(row.get("message_id"), 0),
    )


def _row_evidence(row: Dict[str, Any]) -> Set[str]:
    raw = row.get("evidence")
    if isinstance(raw, list):
        return {str(x) for x in raw if str(x).strip()}
    if isinstance(raw, set):
        return {str(x) for x in raw if str(x).strip()}
    return set()


def _row_has_evidence(row: Dict[str, Any], tag: str) -> bool:
    return tag in _row_evidence(row)


def _build_message_evidence(
    *,
    invite_count: int,
    blocked_invite_count: int,
    non_invite_url_count: int,
    mentions_everyone: bool,
) -> List[str]:
    evidence: List[str] = []
    if invite_count > 0:
        evidence.append(EVIDENCE_ANY_INVITE)
    if blocked_invite_count > 0:
        evidence.append(EVIDENCE_BLOCKED_INVITE)
    if non_invite_url_count > 0:
        evidence.append(EVIDENCE_NON_INVITE_URL)
    if mentions_everyone:
        evidence.append(EVIDENCE_EVERYONE_PING)
    return evidence


def _select_cleanup_refs(
    *,
    recent_messages: List[Dict[str, Any]],
    delete_limit: int,
    current_norm: str,
    fired_invite_rule: bool,
    fired_duplicate_rule: bool,
    fired_everyone_rule: bool,
    fired_url_rule: bool,
    fired_channel_flood_rule: bool,
) -> List[Dict[str, Any]]:
    if delete_limit <= 0:
        return []

    ordered = sorted(
        list(recent_messages),
        key=lambda x: float(x.get("ts", 0.0) or 0.0),
        reverse=True,
    )

    selected: List[Dict[str, Any]] = []
    seen: Set[Tuple[int, int]] = set()

    def add_rows(rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            ident = _row_identity(row)
            if ident == (0, 0) or ident in seen:
                continue
            seen.add(ident)
            selected.append(row)
            if len(selected) >= delete_limit:
                return

    if fired_invite_rule:
        add_rows([r for r in ordered if _row_has_evidence(r, EVIDENCE_BLOCKED_INVITE)])

    if fired_invite_rule and len(selected) < delete_limit:
        add_rows(
            [
                r
                for r in ordered
                if _row_has_evidence(r, EVIDENCE_ANY_INVITE) or int(r.get("invite_count", 0) or 0) > 0
            ]
        )

    if len(selected) < delete_limit and fired_url_rule:
        add_rows([r for r in ordered if _row_has_evidence(r, EVIDENCE_NON_INVITE_URL)])

    if len(selected) < delete_limit and fired_everyone_rule:
        add_rows([r for r in ordered if _row_has_evidence(r, EVIDENCE_EVERYONE_PING)])

    if len(selected) < delete_limit and fired_duplicate_rule and current_norm:
        add_rows([r for r in ordered if str(r.get("norm") or "") == current_norm])

    if len(selected) < delete_limit and fired_channel_flood_rule and not selected and ordered:
        add_rows([ordered[0]])

    return selected[:delete_limit]


def _build_quarantine_case_id(guild_id: int, user_id: int) -> str:
    return f"sgq_{guild_id}_{user_id}_{int(time.time() * 1000)}_{secrets.token_hex(3)}"


def _incident_footer(case_id: str, guild_id: int, user_id: int) -> str:
    return f"{SPAM_INCIDENT_FOOTER_PREFIX}|case={case_id}|guild={guild_id}|user={user_id}"


def _parse_incident_footer(text: str) -> Optional[Dict[str, str]]:
    match = INCIDENT_FOOTER_RE.search(_safe_str(text))
    if not match:
        return None
    return {
        "case_id": _safe_str(match.group("case")),
        "guild_id": _safe_str(match.group("guild")),
        "user_id": _safe_str(match.group("user")),
    }


async def _reply_ephemeral(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        try:
            await interaction.followup.send(content, ephemeral=True)
        except Exception:
            pass


async def _ensure_staff_panel_access(interaction: discord.Interaction) -> bool:
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if not isinstance(member, discord.Member):
        await _reply_ephemeral(interaction, "This can only be used inside the server.")
        return False
    if not _is_staffish(member):
        await _reply_ephemeral(interaction, "You do not have permission to use this panel.")
        return False
    return True


def _can_manage_role(me: Optional[discord.Member], role: Optional[discord.Role]) -> bool:
    try:
        if me is None or role is None:
            return False
        if role.is_default():
            return False
        if role.managed:
            return False
        if me.guild_permissions.administrator:
            return True
        return me.top_role > role
    except Exception:
        return False


def _manageable_roles_for_quarantine(
    *,
    member: discord.Member,
    quarantine_role_id: int,
) -> List[discord.Role]:
    me = member.guild.me
    if me is None:
        return []

    removable: List[discord.Role] = []
    for role in list(member.roles):
        try:
            if role.is_default():
                continue
            if int(role.id) == int(quarantine_role_id):
                continue
            if not _can_manage_role(me, role):
                continue
            removable.append(role)
        except Exception:
            continue

    removable.sort(key=lambda r: r.position, reverse=True)
    return removable


def _normalize_case(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None

    return {
        "case_id": _safe_str(row.get("case_id")),
        "guild_id": _safe_str(row.get("guild_id")),
        "user_id": _safe_str(row.get("user_id")),
        "user_name": _safe_str(row.get("user_name")),
        "active": _safe_bool(row.get("active"), True),
        "created_at": _safe_str(row.get("created_at")),
        "restored_at": _safe_str(row.get("restored_at")),
        "restored_by": _safe_str(row.get("restored_by")),
        "restored_by_name": _safe_str(row.get("restored_by_name")),
        "quarantine_role_id": _safe_str(row.get("quarantine_role_id")),
        "stripped_role_ids": _normalize_id_list(row.get("stripped_role_ids")),
        "timeout_applied": _safe_bool(row.get("timeout_applied"), False),
        "timeout_minutes": _safe_int(row.get("timeout_minutes"), 0),
        "modlog_message_id": _safe_str(row.get("modlog_message_id")),
        "notes": _safe_str(row.get("notes")),
    }


def _case_payload_for_db(case: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_case(case) or {}
    return {
        "case_id": normalized.get("case_id"),
        "guild_id": normalized.get("guild_id"),
        "user_id": normalized.get("user_id"),
        "user_name": normalized.get("user_name"),
        "active": bool(normalized.get("active")),
        "created_at": normalized.get("created_at") or _now_utc().isoformat(),
        "restored_at": normalized.get("restored_at") or None,
        "restored_by": normalized.get("restored_by") or None,
        "restored_by_name": normalized.get("restored_by_name") or None,
        "quarantine_role_id": normalized.get("quarantine_role_id") or None,
        "stripped_role_ids": list(normalized.get("stripped_role_ids") or []),
        "timeout_applied": bool(normalized.get("timeout_applied")),
        "timeout_minutes": int(normalized.get("timeout_minutes") or 0),
        "modlog_message_id": normalized.get("modlog_message_id") or None,
        "notes": normalized.get("notes") or None,
    }


def _fetch_case_sync(case_id: str) -> Optional[Dict[str, Any]]:
    global _CASES_TABLE_AVAILABLE

    sb = _sb()
    if sb is None:
        return None

    try:
        res = (
            sb.table(QUARANTINE_CASES_TABLE)
            .select("*")
            .eq("case_id", str(case_id))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        _CASES_TABLE_AVAILABLE = True
        if rows:
            return dict(rows[0])
        return None
    except Exception as e:
        if _is_table_missing_error(e, QUARANTINE_CASES_TABLE):
            _CASES_TABLE_AVAILABLE = False
            return None
        raise


def _upsert_case_sync(payload: Dict[str, Any]) -> bool:
    global _CASES_TABLE_AVAILABLE

    sb = _sb()
    if sb is None:
        return False

    try:
        sb.table(QUARANTINE_CASES_TABLE).upsert(
            payload,
            on_conflict="case_id",
        ).execute()
        _CASES_TABLE_AVAILABLE = True
        return True
    except Exception as e:
        if _is_table_missing_error(e, QUARANTINE_CASES_TABLE):
            _CASES_TABLE_AVAILABLE = False
            return False

        _CASES_TABLE_AVAILABLE = False
        _debug(f"case upsert runtime fallback error={repr(e)}")
        return False


async def get_quarantine_case(case_id: str) -> Optional[Dict[str, Any]]:
    clean = _safe_str(case_id)
    if not clean:
        return None

    runtime = _QUARANTINE_CASES.get(clean)
    if isinstance(runtime, dict):
        return _normalize_case(runtime)

    if _CASES_TABLE_AVAILABLE is False:
        return None

    try:
        row = await asyncio.to_thread(_fetch_case_sync, clean)
        normalized = _normalize_case(row)
        if normalized:
            _QUARANTINE_CASES[clean] = dict(normalized)
        return normalized
    except Exception as e:
        _debug(f"case fetch failed case={clean} error={repr(e)}")
        return None


async def save_quarantine_case(case: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
    normalized = _normalize_case(case)
    if not normalized or not normalized.get("case_id"):
        return None, False

    case_id = str(normalized["case_id"])
    _QUARANTINE_CASES[case_id] = dict(normalized)

    persisted = False
    if _CASES_TABLE_AVAILABLE is not False:
        try:
            persisted = await asyncio.to_thread(_upsert_case_sync, _case_payload_for_db(normalized))
        except Exception as e:
            _debug(f"case save failed case={case_id} error={repr(e)}")
            persisted = False

    return normalized, persisted


def _default_settings(guild_id: int) -> Dict[str, Any]:
    return {
        "guild_id": str(guild_id),
        "enabled": False,
        "mode": "timeout",
        "apply_to_verified_users": True,
        "block_external_invites_only": True,
        "allow_server_invites": True,
        "window_seconds": 12,
        "message_threshold": 5,
        "duplicate_threshold": 3,
        "invite_threshold": 2,
        "multi_invite_immediate": 2,
        "delete_history": 8,
        "timeout_minutes": 30,
        "cooldown_seconds": 20,
        "quarantine_role_id": "",
        "exempt_role_ids": [],
        "invite_allowed_role_ids": [],
        "allowed_channel_ids": [],
        "exempt_user_ids": [],
        "allowed_invite_codes": [],
    }


def _normalize_settings(guild_id: int, row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = _default_settings(guild_id)
    if not isinstance(row, dict):
        return base

    base["enabled"] = _safe_bool(row.get("spam_blocker_enabled", row.get("enabled")), base["enabled"])
    base["mode"] = _normalize_mode(row.get("spam_mode", row.get("mode")), base["mode"])
    base["apply_to_verified_users"] = _safe_bool(
        row.get("spam_apply_to_verified_users", row.get("apply_to_verified_users")),
        base["apply_to_verified_users"],
    )
    base["block_external_invites_only"] = _safe_bool(
        row.get("spam_block_external_invites_only", row.get("block_external_invites_only")),
        base["block_external_invites_only"],
    )
    base["allow_server_invites"] = _safe_bool(
        row.get("spam_allow_server_invites", row.get("allow_server_invites")),
        base["allow_server_invites"],
    )
    base["window_seconds"] = max(
        5,
        min(60, _safe_int(row.get("spam_window_seconds", row.get("window_seconds")), base["window_seconds"])),
    )
    base["message_threshold"] = max(
        3,
        min(20, _safe_int(row.get("spam_message_threshold", row.get("message_threshold")), base["message_threshold"])),
    )
    base["duplicate_threshold"] = max(
        2,
        min(12, _safe_int(row.get("spam_duplicate_threshold", row.get("duplicate_threshold")), base["duplicate_threshold"])),
    )
    base["invite_threshold"] = max(
        1,
        min(12, _safe_int(row.get("spam_invite_threshold", row.get("invite_threshold")), base["invite_threshold"])),
    )
    base["multi_invite_immediate"] = max(
        2,
        min(8, _safe_int(row.get("spam_multi_invite_immediate", row.get("multi_invite_immediate")), base["multi_invite_immediate"])),
    )
    base["delete_history"] = max(
        1,
        min(30, _safe_int(row.get("spam_delete_history", row.get("delete_history")), base["delete_history"])),
    )
    base["timeout_minutes"] = max(
        1,
        min(1440, _safe_int(row.get("spam_timeout_minutes", row.get("timeout_minutes")), base["timeout_minutes"])),
    )
    base["cooldown_seconds"] = max(
        5,
        min(300, _safe_int(row.get("spam_cooldown_seconds", row.get("cooldown_seconds")), base["cooldown_seconds"])),
    )
    base["quarantine_role_id"] = _safe_str(row.get("spam_quarantine_role_id", row.get("quarantine_role_id")))

    base["exempt_role_ids"] = _normalize_id_list(row.get("spam_exempt_role_ids", row.get("exempt_role_ids")))
    base["invite_allowed_role_ids"] = _normalize_id_list(
        row.get("spam_invite_allowed_role_ids", row.get("invite_allowed_role_ids"))
    )
    base["allowed_channel_ids"] = _normalize_id_list(row.get("spam_allowed_channel_ids", row.get("allowed_channel_ids")))
    base["exempt_user_ids"] = _normalize_id_list(row.get("spam_exempt_user_ids", row.get("exempt_user_ids")))

    raw_codes = row.get("spam_allowed_invite_codes", row.get("allowed_invite_codes"))
    if isinstance(raw_codes, list):
        base["allowed_invite_codes"] = _normalize_code_list(raw_codes)
    else:
        base["allowed_invite_codes"] = _parse_csvish_codes(str(raw_codes or ""))

    return base


def _settings_payload_for_db(settings: Dict[str, Any], *, updated_by: Optional[discord.Member] = None) -> Dict[str, Any]:
    return {
        "guild_id": str(settings["guild_id"]),
        "spam_blocker_enabled": bool(settings["enabled"]),
        "spam_mode": str(settings["mode"]),
        "spam_apply_to_verified_users": bool(settings["apply_to_verified_users"]),
        "spam_block_external_invites_only": bool(settings["block_external_invites_only"]),
        "spam_allow_server_invites": bool(settings["allow_server_invites"]),
        "spam_window_seconds": int(settings["window_seconds"]),
        "spam_message_threshold": int(settings["message_threshold"]),
        "spam_duplicate_threshold": int(settings["duplicate_threshold"]),
        "spam_invite_threshold": int(settings["invite_threshold"]),
        "spam_multi_invite_immediate": int(settings["multi_invite_immediate"]),
        "spam_delete_history": int(settings["delete_history"]),
        "spam_timeout_minutes": int(settings["timeout_minutes"]),
        "spam_cooldown_seconds": int(settings["cooldown_seconds"]),
        "spam_quarantine_role_id": _safe_str(settings.get("quarantine_role_id")),
        "spam_exempt_role_ids": list(settings.get("exempt_role_ids") or []),
        "spam_invite_allowed_role_ids": list(settings.get("invite_allowed_role_ids") or []),
        "spam_allowed_channel_ids": list(settings.get("allowed_channel_ids") or []),
        "spam_exempt_user_ids": list(settings.get("exempt_user_ids") or []),
        "spam_allowed_invite_codes": list(settings.get("allowed_invite_codes") or []),
        "updated_at": _now_utc().isoformat(),
        "updated_by": str(getattr(updated_by, "id", "")) if isinstance(updated_by, discord.Member) else None,
        "updated_by_name": str(getattr(updated_by, "display_name", "")) if isinstance(updated_by, discord.Member) else None,
    }


def _fetch_settings_sync(guild_id: int) -> Tuple[str, Optional[Dict[str, Any]]]:
    global _SETTINGS_TABLE_AVAILABLE

    sb = _sb()
    if sb is None:
        return "unavailable", None

    try:
        res = (
            sb.table(GUILD_SECURITY_SETTINGS_TABLE)
            .select("*")
            .eq("guild_id", str(guild_id))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        _SETTINGS_TABLE_AVAILABLE = True

        if rows:
            return "ok", dict(rows[0])

        return "ok", {}
    except Exception as e:
        if _is_table_missing_error(e, GUILD_SECURITY_SETTINGS_TABLE):
            _SETTINGS_TABLE_AVAILABLE = False
            return "missing_table", None

        _debug(f"settings fetch transient failure guild={guild_id} error={repr(e)}")
        return "unavailable", None


def _upsert_settings_sync(payload: Dict[str, Any]) -> bool:
    global _SETTINGS_TABLE_AVAILABLE

    sb = _sb()
    if sb is None:
        return False

    try:
        sb.table(GUILD_SECURITY_SETTINGS_TABLE).upsert(
            payload,
            on_conflict="guild_id",
        ).execute()
        _SETTINGS_TABLE_AVAILABLE = True
        return True
    except Exception as e:
        if _is_table_missing_error(e, GUILD_SECURITY_SETTINGS_TABLE):
            _SETTINGS_TABLE_AVAILABLE = False
            return False

        _debug(f"settings upsert transient failure guild={payload.get('guild_id')} error={repr(e)}")
        return False


async def get_spam_settings(guild_id: int) -> Dict[str, Any]:
    gid = int(guild_id)

    cached = _cached_runtime_settings(gid)
    if isinstance(cached, dict) and _cache_is_fresh(cached):
        return _normalize_settings(gid, cached)

    if _SETTINGS_TABLE_AVAILABLE is False:
        if isinstance(cached, dict):
            return _normalize_settings(gid, cached)
        return _default_settings(gid)

    try:
        status, row = await asyncio.to_thread(_fetch_settings_sync, gid)

        if status == "ok":
            normalized = _normalize_settings(gid, row or {})
            _cache_runtime_settings(
                gid,
                normalized,
                source="db" if row else "db-empty",
                persisted=bool(row),
            )
            return normalized

        if status == "missing_table":
            if isinstance(cached, dict):
                return _normalize_settings(gid, cached)
            return _default_settings(gid)

        if isinstance(cached, dict):
            return _normalize_settings(gid, cached)

        return _default_settings(gid)
    except Exception as e:
        _debug(f"settings fetch failed guild={gid} error={repr(e)}")

        if isinstance(cached, dict):
            return _normalize_settings(gid, cached)

        return _default_settings(gid)


async def save_spam_settings(
    guild_id: int,
    patch: Dict[str, Any],
    *,
    updated_by: Optional[discord.Member] = None,
) -> Tuple[Dict[str, Any], bool]:
    gid = int(guild_id)

    current = await get_spam_settings(gid)
    merged = dict(current)
    merged.update(dict(patch or {}))
    normalized = _normalize_settings(gid, merged)

    persisted = False
    if _SETTINGS_TABLE_AVAILABLE is not False:
        try:
            persisted = await asyncio.to_thread(
                _upsert_settings_sync,
                _settings_payload_for_db(normalized, updated_by=updated_by),
            )
        except Exception as e:
            _debug(f"settings save failed guild={gid} error={repr(e)}")
            persisted = False

    _cache_runtime_settings(
        gid,
        normalized,
        source="db" if persisted else "runtime",
        persisted=persisted,
    )

    return normalized, persisted


def _build_panel_embed(
    guild: discord.Guild,
    settings: Dict[str, Any],
    *,
    page: str,
    persisted_hint: Optional[bool] = None,
) -> discord.Embed:
    clean_page = page if page in SPAM_PANEL_PAGES else "overview"
    enabled = bool(settings["enabled"])
    mode = _safe_str(settings["mode"])
    persistence = _build_persistence_label(guild.id, persisted_hint)

    title = f"🛡️ Spam Guard • {_page_title(clean_page)}"
    embed = discord.Embed(
        title=title,
        color=discord.Color.green() if enabled else discord.Color.orange(),
        timestamp=_now_utc(),
    )

    if clean_page == "overview":
        embed.description = (
            "This is the main control center for hacked-account and invite spam protection.\n"
            "Use the section menu below to move between pages."
        )
        embed.add_field(
            name="Status",
            value=(
                f"**Guard enabled:** {_bool_chip(bool(settings['enabled']))}\n"
                f"**Current response mode:** `{mode}`\n"
                f"**Saving:** `{persistence}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="What the main toggles mean",
            value=(
                "**External Only** = block invite links from other servers only.\n"
                "**Allow Own Invites** = allow invite links for this server.\n"
                "**Watch Verified** = still protect already-verified members if their account gets hacked."
            ),
            inline=False,
        )
        embed.add_field(
            name="Quick rule summary",
            value=(
                f"window=`{int(settings['window_seconds'])}s` • "
                f"messages=`{int(settings['message_threshold'])}` • "
                f"duplicates=`{int(settings['duplicate_threshold'])}`\n"
                f"invite_msgs=`{int(settings['invite_threshold'])}` • "
                f"multi_invite=`{int(settings['multi_invite_immediate'])}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Quick list summary",
            value=(
                f"{_compact_count('Invite-allowed roles', list(settings.get('invite_allowed_role_ids') or []))}\n"
                f"{_compact_count('Fully exempt roles', list(settings.get('exempt_role_ids') or []))}\n"
                f"{_compact_count('Allowed channels', list(settings.get('allowed_channel_ids') or []))}\n"
                f"{_compact_count('Exempt users', list(settings.get('exempt_user_ids') or []))}\n"
                f"{_compact_count('Allowed invite codes', list(settings.get('allowed_invite_codes') or []))}"
            ),
            inline=False,
        )

    elif clean_page == "detection":
        embed.description = (
            "This page controls what counts as suspicious behavior.\n"
            "Lower numbers = more sensitive. Higher numbers = less sensitive."
        )
        embed.add_field(
            name="Burst rules",
            value=(
                f"**Window:** `{int(settings['window_seconds'])}s`\n"
                "Time range used for checking spam bursts.\n\n"
                f"**Message threshold:** `{int(settings['message_threshold'])}`\n"
                "How many messages in that time window can trigger a spam hit.\n\n"
                f"**Duplicate threshold:** `{int(settings['duplicate_threshold'])}`\n"
                "How many repeated copies of the same message can trigger a hit."
            ),
            inline=False,
        )
        embed.add_field(
            name="Invite rules",
            value=(
                f"**Invite-message threshold:** `{int(settings['invite_threshold'])}`\n"
                "How many messages containing invite links can contribute to a trigger.\n\n"
                f"**Immediate multi-invite trigger:** `{int(settings['multi_invite_immediate'])}`\n"
                "How many invite links in one message instantly trigger a hit."
            ),
            inline=False,
        )
        embed.add_field(
            name="Built-in URL flood protection",
            value=(
                "For non-verified users, rapid posting of normal URLs across channels can also trigger protection.\n"
                "This helps catch likely compromised accounts dropping phishing links."
            ),
            inline=False,
        )
        embed.add_field(
            name="Important invite note",
            value=(
                "Allowed invite codes and this-server invites are allowed individually.\n"
                "But rapid invite flooding still counts as spam and can now trigger protection."
            ),
            inline=False,
        )

    elif clean_page == "enforcement":
        embed.description = (
            "This page controls what the bot does after it detects spam.\n"
            "Pick one mode, then tune cleanup and timeout settings."
        )
        embed.add_field(
            name="Current response",
            value=(
                f"**Mode:** `{mode}`\n"
                f"**Max matching messages to delete:** `{int(settings['delete_history'])}`\n"
                f"**Repeat-action cooldown:** `{int(settings['cooldown_seconds'])}s`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Mode guide",
            value=(
                "`log_only` = record it only\n"
                "`delete_only` = remove matched spam messages only\n"
                "`timeout` = remove matched spam and timeout the user\n"
                "`quarantine` = remove matched spam, strip manageable roles, add quarantine role, and allow one-click restore\n"
                "`kick` = remove matched spam and kick the user\n"
                "`ban` = remove matched spam and ban the user"
            ),
            inline=False,
        )
        embed.add_field(
            name="Cleanup note",
            value=(
                "The delete setting is a cap, not a quota.\n"
                "The bot only removes messages that match the trigger it fired on.\n"
                "Generic cross-channel flood uses a minimal fallback instead of broad wiping."
            ),
            inline=False,
        )
        embed.add_field(
            name="Action details",
            value=(
                f"**Timeout length:** `{int(settings['timeout_minutes'])}m`\n"
                f"**Quarantine role:** {_quarantine_role_text(guild, settings)}\n"
                "Use **Actions + Codes** below to edit these."
            ),
            inline=False,
        )

    else:
        embed.description = (
            "This page controls who is allowed to bypass parts of the guard.\n"
            "Keep this small and intentional."
        )
        embed.add_field(
            name="Invite-allowed roles",
            value=(
                _format_role_list(guild, list(settings.get("invite_allowed_role_ids") or []))
                + "\nThese roles may post invite links without being blocked by invite rules."
            ),
            inline=False,
        )
        embed.add_field(
            name="Fully exempt roles",
            value=(
                _format_role_list(guild, list(settings.get("exempt_role_ids") or []))
                + "\nThese roles bypass the guard completely."
            ),
            inline=False,
        )
        embed.add_field(
            name="Allowed channels / exempt users",
            value=(
                f"**Allowed channels:** {_format_channel_list(guild, list(settings.get('allowed_channel_ids') or []))}\n"
                "Invite-link rules do not fire there.\n\n"
                f"**Exempt users:** {_format_user_list(guild, list(settings.get('exempt_user_ids') or []))}\n"
                "These users bypass the guard completely."
            ),
            inline=False,
        )
        embed.add_field(
            name="Allowed invite codes",
            value=(
                (", ".join(f"`{x}`" for x in list(settings.get("allowed_invite_codes") or [])[:12]) or "—")
                + "\nThese invite codes are always allowed."
            ),
            inline=False,
        )

    embed.set_footer(text=_panel_footer(clean_page))
    return embed


async def _find_existing_panel(channel: discord.TextChannel) -> Optional[discord.Message]:
    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
        async for msg in channel.history(limit=80):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            if not msg.embeds:
                continue
            for emb in (msg.embeds or []):
                footer = _safe_str(getattr(getattr(emb, "footer", None), "text", ""))
                if SPAM_PANEL_FOOTER_PREFIX in footer or SPAM_PANEL_FOOTER_BASE in footer:
                    return msg
    except Exception:
        pass
    return None


async def _rerender_panel_message(
    *,
    guild: discord.Guild,
    channel: discord.TextChannel,
    message_id: int,
    page: str,
    persisted_hint: Optional[bool] = None,
) -> None:
    settings = await get_spam_settings(guild.id)
    embed = _build_panel_embed(
        guild,
        settings,
        page=page,
        persisted_hint=persisted_hint,
    )
    view = SpamGuardPanelView.build(page=page, settings=settings)

    try:
        msg = await channel.fetch_message(int(message_id))
        await msg.edit(embed=embed, view=view)
    except Exception as e:
        _debug(f"rerender failed guild={guild.id} page={page} message={message_id} error={repr(e)}")


async def _save_patch_and_rerender(
    interaction: discord.Interaction,
    *,
    patch: Dict[str, Any],
    page: str,
    success_text: str,
) -> None:
    if not await _ensure_staff_panel_access(interaction):
        return

    guild = interaction.guild
    channel = interaction.channel
    message = interaction.message

    if guild is None or not isinstance(channel, discord.TextChannel) or message is None:
        return await _reply_ephemeral(interaction, "Invalid context.")

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception as e:
        _debug(f"save defer failed guild={getattr(guild, 'id', 0)} page={page} error={repr(e)}")

    _, persisted = await save_spam_settings(
        guild.id,
        patch,
        updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
    )

    await _rerender_panel_message(
        guild=guild,
        channel=channel,
        message_id=message.id,
        page=page,
        persisted_hint=persisted,
    )

    try:
        await interaction.followup.send(
            f"✅ {success_text}\nPersistence: `{_build_persistence_label(guild.id, persisted)}`",
            ephemeral=True,
        )
    except Exception:
        pass


async def _switch_panel_page(interaction: discord.Interaction, *, page: str) -> None:
    if not await _ensure_staff_panel_access(interaction):
        return

    guild = interaction.guild
    channel = interaction.channel
    message = interaction.message

    if guild is None or not isinstance(channel, discord.TextChannel) or message is None:
        return await _reply_ephemeral(interaction, "Invalid context.")

    clean_page = page if page in SPAM_PANEL_PAGES else "overview"

    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
    except Exception as e:
        _debug(f"panel switch defer failed guild={guild.id} page={clean_page} error={repr(e)}")

    try:
        settings = await get_spam_settings(guild.id)
        embed = _build_panel_embed(
            guild,
            settings,
            page=clean_page,
            persisted_hint=None,
        )
        view = SpamGuardPanelView.build(page=clean_page, settings=settings)

        await message.edit(embed=embed, view=view)
        _debug(f"panel switch success guild={guild.id} page={clean_page} message={message.id}")
        return
    except Exception as e:
        _debug(f"panel switch failed guild={guild.id} page={clean_page} error={repr(e)}")

    try:
        await interaction.followup.send(
            "❌ Failed to switch spam guard section.",
            ephemeral=True,
        )
    except Exception:
        pass


async def _fetch_guild_invite_codes(guild: discord.Guild) -> Set[str]:
    gid = int(guild.id)
    now_mono = time.monotonic()

    cached = _GUILD_INVITE_CACHE.get(gid) or {}
    expires_at = float(cached.get("expires_at", 0.0) or 0.0)
    if expires_at > now_mono and isinstance(cached.get("codes"), set):
        return set(cached["codes"])

    codes: Set[str] = set()
    try:
        invites = await guild.invites()
        for inv in invites:
            code = _safe_str(getattr(inv, "code", "")).lower()
            if code:
                codes.add(code)
    except Exception:
        pass

    _GUILD_INVITE_CACHE[gid] = {
        "expires_at": now_mono + 300.0,
        "codes": set(codes),
    }
    return codes


def _state_for_user(guild_id: int, user_id: int) -> Dict[str, Any]:
    key = (int(guild_id), int(user_id))
    found = _MESSAGE_WINDOWS.get(key)
    if found is None:
        found = {
            "messages": deque(maxlen=40),
            "last_action_at": 0.0,
        }
        _MESSAGE_WINDOWS[key] = found
    return found


def _cleanup_state(state: Dict[str, Any], *, now_mono: float, keep_seconds: int) -> None:
    cutoff = float(now_mono) - float(keep_seconds)

    messages = state.get("messages")
    if not isinstance(messages, deque):
        messages = deque(maxlen=40)
        state["messages"] = messages

    while messages and float(messages[0].get("ts", 0.0) or 0.0) < cutoff:
        messages.popleft()


@tasks.loop(minutes=5)
async def cleanup_stale_memory() -> None:
    now_mono = time.monotonic()

    windows_removed = 0
    locks_removed = 0
    invite_cache_removed = 0
    restored_cases_removed = 0

    for key, state in list(_MESSAGE_WINDOWS.items()):
        try:
            _cleanup_state(state, now_mono=now_mono, keep_seconds=STALE_WINDOW_TTL_SECONDS)
            messages = state.get("messages")
            last_action_at = float(state.get("last_action_at", 0.0) or 0.0)
            is_empty = not messages if isinstance(messages, deque) else True
            if is_empty and (now_mono - last_action_at) > STALE_WINDOW_TTL_SECONDS:
                _MESSAGE_WINDOWS.pop(key, None)
                windows_removed += 1
        except Exception:
            continue

    for key, lock in list(_LOCKS.items()):
        try:
            last_used = float(_LOCK_LAST_USED.get(key, 0.0) or 0.0)
            if lock.locked():
                continue
            if (now_mono - last_used) > STALE_LOCK_TTL_SECONDS:
                _LOCKS.pop(key, None)
                _LOCK_LAST_USED.pop(key, None)
                locks_removed += 1
        except Exception:
            continue

    for gid, payload in list(_GUILD_INVITE_CACHE.items()):
        try:
            expires_at = float(payload.get("expires_at", 0.0) or 0.0)
            if expires_at <= 0.0 or (now_mono - expires_at) > STALE_INVITE_CACHE_TTL_SECONDS:
                _GUILD_INVITE_CACHE.pop(gid, None)
                invite_cache_removed += 1
        except Exception:
            continue

    for case_id, case in list(_QUARANTINE_CASES.items()):
        try:
            if bool(case.get("active")):
                continue
            restored_at = _safe_str(case.get("restored_at"))
            if not restored_at:
                continue
            restored_ts = datetime.fromisoformat(restored_at.replace("Z", "+00:00")).timestamp()
            if (time.time() - restored_ts) > RESTORED_CASE_RETENTION_SECONDS:
                _QUARANTINE_CASES.pop(case_id, None)
                restored_cases_removed += 1
        except Exception:
            continue

    if any((windows_removed, locks_removed, invite_cache_removed, restored_cases_removed)):
        _debug(
            "memory cleanup removed "
            f"windows={windows_removed} "
            f"locks={locks_removed} "
            f"invite_cache={invite_cache_removed} "
            f"restored_cases={restored_cases_removed}"
        )


@cleanup_stale_memory.before_loop
async def _before_cleanup_stale_memory() -> None:
    await bot.wait_until_ready()


async def _delete_recent_messages(
    *,
    guild: discord.Guild,
    refs: List[Dict[str, Any]],
    reason: str,
) -> int:
    deleted = 0
    grouped: Dict[int, List[int]] = {}
    seen: Set[Tuple[int, int]] = set()

    for row in refs:
        channel_id = _safe_int(row.get("channel_id"), 0)
        message_id = _safe_int(row.get("message_id"), 0)
        if channel_id <= 0 or message_id <= 0:
            continue

        key = (channel_id, message_id)
        if key in seen:
            continue
        seen.add(key)
        grouped.setdefault(channel_id, []).append(message_id)

    for channel_id, message_ids in grouped.items():
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None

        if not isinstance(channel, discord.TextChannel):
            continue

        try:
            unique_ids = list(dict.fromkeys(message_ids))
            partials = [channel.get_partial_message(mid) for mid in unique_ids]

            if len(partials) == 1:
                await partials[0].delete(reason=reason)
                deleted += 1
                continue

            await channel.delete_messages(partials, reason=reason)
            deleted += len(partials)
            continue
        except Exception:
            pass

        for mid in list(dict.fromkeys(message_ids)):
            try:
                await channel.get_partial_message(mid).delete(reason=reason)
                deleted += 1
            except Exception:
                continue

    return deleted


async def _get_modlog_text_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from .modlog import _get_modlog_channel  # type: ignore

        maybe = _get_modlog_channel(guild)
        if asyncio.iscoroutine(maybe):
            maybe = await maybe
        if isinstance(maybe, discord.TextChannel):
            return maybe
    except Exception:
        pass

    try:
        if MODLOG_CHANNEL_ID:
            ch = guild.get_channel(int(MODLOG_CHANNEL_ID))
            if isinstance(ch, discord.TextChannel):
                return ch
    except Exception:
        pass

    return None


async def _fetch_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(int(user_id))
    if isinstance(member, discord.Member):
        return member
    try:
        fetched = await guild.fetch_member(int(user_id))
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        pass
    return None


async def _create_quarantine_case(
    *,
    guild: discord.Guild,
    member: discord.Member,
    settings: Dict[str, Any],
    timeout_applied: bool,
) -> Optional[Dict[str, Any]]:
    qrid = _safe_str(settings.get("quarantine_role_id"))
    if not qrid.isdigit():
        return None

    case = {
        "case_id": _build_quarantine_case_id(guild.id, member.id),
        "guild_id": str(guild.id),
        "user_id": str(member.id),
        "user_name": str(member),
        "active": True,
        "created_at": _now_utc().isoformat(),
        "restored_at": "",
        "restored_by": "",
        "restored_by_name": "",
        "quarantine_role_id": qrid,
        "stripped_role_ids": [],
        "timeout_applied": bool(timeout_applied),
        "timeout_minutes": int(settings.get("timeout_minutes", 0) or 0),
        "modlog_message_id": "",
        "notes": "Spam guard quarantine case",
    }
    normalized, _ = await save_quarantine_case(case)
    return normalized


async def _apply_quarantine(
    *,
    guild: discord.Guild,
    member: discord.Member,
    settings: Dict[str, Any],
    reason: str,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    me = guild.me
    if me is None:
        return "delete-only", None

    qrid = _safe_str(settings.get("quarantine_role_id"))
    if not qrid.isdigit():
        return "delete-only", None

    quarantine_role = guild.get_role(int(qrid))
    if not isinstance(quarantine_role, discord.Role):
        return "delete-only", None

    if not _can_manage_role(me, quarantine_role):
        return "delete-only", None

    stripped_roles = _manageable_roles_for_quarantine(member=member, quarantine_role_id=int(qrid))
    stripped_role_ids = [str(role.id) for role in stripped_roles]

    try:
        if stripped_roles:
            await member.remove_roles(*stripped_roles, reason=reason)
    except Exception:
        stripped_roles = []
        stripped_role_ids = []

    try:
        if quarantine_role not in member.roles:
            await member.add_roles(quarantine_role, reason=reason)
    except Exception:
        return "delete-only", None

    timeout_applied = False
    try:
        if me.guild_permissions.moderate_members:
            until = _now_utc() + timedelta(minutes=max(1, int(settings["timeout_minutes"])))
            await member.timeout(until, reason=reason)
            timeout_applied = True
    except Exception:
        timeout_applied = False

    case = await _create_quarantine_case(
        guild=guild,
        member=member,
        settings=settings,
        timeout_applied=timeout_applied,
    )
    if case is not None:
        case["stripped_role_ids"] = stripped_role_ids
        case["timeout_applied"] = timeout_applied
        case["timeout_minutes"] = int(settings.get("timeout_minutes", 0) or 0)
        case, _ = await save_quarantine_case(case)

    return f"quarantine:{quarantine_role.id}", case


async def _apply_mode_action(
    *,
    guild: discord.Guild,
    member: discord.Member,
    settings: Dict[str, Any],
    reason: str,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    mode = _normalize_mode(settings.get("mode"), "timeout")
    me = guild.me

    if me is None:
        return "no-action", None

    try:
        if me.top_role <= member.top_role and not me.guild_permissions.administrator:
            return "no-action", None
    except Exception:
        return "no-action", None

    if mode == "log_only":
        return "log-only", None

    if mode == "delete_only":
        return "delete-only", None

    if mode == "timeout":
        try:
            if me.guild_permissions.moderate_members:
                until = _now_utc() + timedelta(minutes=max(1, int(settings["timeout_minutes"])))
                await member.timeout(until, reason=reason)
                return f"timeout:{int(settings['timeout_minutes'])}m", None
        except Exception:
            pass
        return "delete-only", None

    if mode == "quarantine":
        return await _apply_quarantine(
            guild=guild,
            member=member,
            settings=settings,
            reason=reason,
        )

    if mode == "kick":
        try:
            if me.guild_permissions.kick_members:
                await guild.kick(member, reason=reason)
                return "kick", None
        except Exception:
            pass
        return "delete-only", None

    if mode == "ban":
        try:
            if me.guild_permissions.ban_members:
                await guild.ban(member, reason=reason)
                return "ban", None
        except Exception:
            pass
        return "delete-only", None

    return "no-action", None


async def _restore_quarantine_case(
    *,
    guild: discord.Guild,
    case: Dict[str, Any],
    actor: discord.Member,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    normalized = _normalize_case(case)
    if not normalized:
        return False, "Invalid quarantine case.", None

    if not bool(normalized.get("active")):
        return False, "This quarantine case is already restored.", normalized

    member = await _fetch_member(guild, _safe_int(normalized.get("user_id"), 0))
    if member is None:
        normalized["active"] = False
        normalized["restored_at"] = _now_utc().isoformat()
        normalized["restored_by"] = str(actor.id)
        normalized["restored_by_name"] = actor.display_name
        normalized["notes"] = "User left before restore could be applied."
        normalized, _ = await save_quarantine_case(normalized)
        return False, "User is no longer in the server. Case was closed.", normalized

    me = guild.me
    if me is None:
        return False, "Bot member context unavailable.", normalized

    restored_roles: List[discord.Role] = []

    quarantine_role = None
    qrid = _safe_str(normalized.get("quarantine_role_id"))
    if qrid.isdigit():
        quarantine_role = guild.get_role(int(qrid))

    if isinstance(quarantine_role, discord.Role):
        try:
            if quarantine_role in member.roles and _can_manage_role(me, quarantine_role):
                await member.remove_roles(quarantine_role, reason=f"Spam guard restore by {actor}")
        except Exception:
            pass

    for rid in list(normalized.get("stripped_role_ids") or []):
        if not str(rid).isdigit():
            continue
        role = guild.get_role(int(rid))
        if not isinstance(role, discord.Role):
            continue
        if role in member.roles:
            continue
        if not _can_manage_role(me, role):
            continue
        restored_roles.append(role)

    try:
        if restored_roles:
            await member.add_roles(*restored_roles, reason=f"Spam guard restore by {actor}")
    except Exception:
        pass

    if bool(normalized.get("timeout_applied")) and me.guild_permissions.moderate_members:
        try:
            await member.timeout(None, reason=f"Spam guard restore by {actor}")
        except Exception:
            pass

    normalized["active"] = False
    normalized["restored_at"] = _now_utc().isoformat()
    normalized["restored_by"] = str(actor.id)
    normalized["restored_by_name"] = actor.display_name
    normalized["notes"] = f"Restored roles={len(restored_roles)}"
    normalized, _ = await save_quarantine_case(normalized)

    summary = (
        f"Removed quarantine role, restored `{len(restored_roles)}` role(s)"
        + (", and cleared timeout." if bool(case.get("timeout_applied")) else ".")
    )
    return True, summary, normalized


def _build_restored_incident_embed(
    source_embed: discord.Embed,
    *,
    actor: discord.Member,
    summary: str,
) -> discord.Embed:
    new_embed = discord.Embed.from_dict(source_embed.to_dict())
    new_embed.color = discord.Color.green()

    replaced = False
    for idx, field in enumerate(list(new_embed.fields)):
        if _safe_str(field.name).lower() == "restore status":
            new_embed.set_field_at(
                idx,
                name="Restore Status",
                value=f"✅ Restored by {actor.mention}\n{summary}",
                inline=False,
            )
            replaced = True
            break

    if not replaced:
        new_embed.add_field(
            name="Restore Status",
            value=f"✅ Restored by {actor.mention}\n{summary}",
            inline=False,
        )

    return new_embed


class SpamIncidentRestoreButton(discord.ui.Button):
    def __init__(self, *, disabled_state: bool = False):
        super().__init__(
            label="Restore Member",
            style=discord.ButtonStyle.success,
            custom_id=SPAM_INCIDENT_RESTORE_CUSTOM_ID,
            disabled=disabled_state,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _ensure_staff_panel_access(interaction):
            return

        guild = interaction.guild
        message = interaction.message
        if guild is None or message is None or not message.embeds:
            return await _reply_ephemeral(interaction, "Invalid restore context.")

        footer_text = _safe_str(getattr(getattr(message.embeds[0], "footer", None), "text", ""))
        parsed = _parse_incident_footer(footer_text)
        if not parsed:
            return await _reply_ephemeral(interaction, "Could not find quarantine case information.")

        case = await get_quarantine_case(parsed["case_id"])
        if not case:
            return await _reply_ephemeral(interaction, "Quarantine case not found.")

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        success, summary, updated_case = await _restore_quarantine_case(
            guild=guild,
            case=case,
            actor=interaction.user if isinstance(interaction.user, discord.Member) else guild.me,  # type: ignore[arg-type]
        )

        if updated_case and message.embeds:
            try:
                embed = message.embeds[0]
                if success and isinstance(interaction.user, discord.Member):
                    embed = _build_restored_incident_embed(embed, actor=interaction.user, summary=summary)
                view = SpamIncidentRestoreView(restored=bool(updated_case and not updated_case.get("active")))
                await message.edit(embed=embed, view=view)
            except Exception:
                pass

        if success:
            return await interaction.followup.send(f"✅ {summary}", ephemeral=True)

        return await interaction.followup.send(f"ℹ️ {summary}", ephemeral=True)


class SpamIncidentRestoreView(discord.ui.View):
    def __init__(self, *, restored: bool = False):
        super().__init__(timeout=None)
        self.add_item(SpamIncidentRestoreButton(disabled_state=restored))


async def _send_modlog_embed(
    guild: discord.Guild,
    embed: discord.Embed,
    *,
    view: Optional[discord.ui.View] = None,
) -> Optional[discord.Message]:
    channel = await _get_modlog_text_channel(guild)
    if not isinstance(channel, discord.TextChannel):
        return None
    try:
        return await channel.send(embed=embed, view=view)
    except Exception:
        return None


async def _log_trigger(
    *,
    guild: discord.Guild,
    member: discord.Member,
    settings: Dict[str, Any],
    reasons: List[str],
    deleted_count: int,
    action_taken: str,
    recent_count: int,
    duplicate_count: int,
    blocked_invite_count: int,
    invite_message_count: int,
    total_invite_count: int,
    url_message_count: int,
    channel_count: int,
    cleanup_refs: List[Dict[str, Any]],
    quarantine_case: Optional[Dict[str, Any]],
) -> None:
    evidence_summary: Set[str] = set()
    for row in cleanup_refs:
        evidence_summary.update(_row_evidence(row))

    embed = discord.Embed(
        title="🛡️ Spam Guard Triggered",
        description="Probable hacked-account or spam burst was blocked.",
        color=discord.Color.red(),
        timestamp=_now_utc(),
    )
    embed.add_field(
        name="User",
        value=f"{member.mention} (`{member}` • `{member.id}`)",
        inline=False,
    )
    embed.add_field(
        name="Configured Mode",
        value=f"`{_normalize_mode(settings.get('mode'))}`",
        inline=True,
    )
    embed.add_field(
        name="Action Taken",
        value=f"`{action_taken}`",
        inline=True,
    )
    embed.add_field(
        name="Deleted",
        value=f"`{deleted_count}`",
        inline=True,
    )
    embed.add_field(
        name="Burst Stats",
        value=(
            f"messages=`{recent_count}` • "
            f"duplicates=`{duplicate_count}` • "
            f"invite_msgs=`{invite_message_count}` • "
            f"invite_links=`{total_invite_count}` • "
            f"blocked_invites=`{blocked_invite_count}` • "
            f"url_msgs=`{url_message_count}` • "
            f"channels=`{channel_count}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Cleanup Evidence",
        value=", ".join(f"`{x}`" for x in sorted(evidence_summary)) if evidence_summary else "`minimal_fallback`",
        inline=False,
    )

    if quarantine_case:
        stripped = list(quarantine_case.get("stripped_role_ids") or [])
        embed.add_field(
            name="Quarantine Snapshot",
            value=(
                f"**Quarantine role:** `{quarantine_case.get('quarantine_role_id')}`\n"
                f"**Stripped roles stored:** `{len(stripped)}`\n"
                f"**Timeout stored:** `{bool(quarantine_case.get('timeout_applied'))}`"
            ),
            inline=False,
        )

    if reasons:
        embed.add_field(
            name="Trigger Reasons",
            value="\n".join(f"• {r}" for r in reasons[:8]),
            inline=False,
        )

    view: Optional[discord.ui.View] = None
    if quarantine_case:
        footer = _incident_footer(
            _safe_str(quarantine_case.get("case_id")),
            guild.id,
            member.id,
        )
        embed.set_footer(text=footer)
        view = SpamIncidentRestoreView(restored=False)

    sent = await _send_modlog_embed(guild, embed, view=view)

    if quarantine_case and sent is not None:
        quarantine_case["modlog_message_id"] = str(sent.id)
        await save_quarantine_case(quarantine_case)


async def handle_incoming_spam_message(message: discord.Message) -> bool:
    try:
        if message.guild is None:
            return False
        if not isinstance(message.author, discord.Member):
            return False
        if getattr(message.author, "bot", False):
            return False
        if not isinstance(message.channel, discord.TextChannel):
            return False

        guild = message.guild
        member = message.author

        if _is_staffish(member):
            return False

        settings = await get_spam_settings(guild.id)
        if not bool(settings.get("enabled")):
            return False

        member_is_verified = _is_verifiedish(member)
        if not bool(settings.get("apply_to_verified_users", True)) and member_is_verified:
            return False

        exempt_user_ids = list(settings.get("exempt_user_ids") or [])
        exempt_role_ids = list(settings.get("exempt_role_ids") or [])
        invite_allowed_role_ids = list(settings.get("invite_allowed_role_ids") or [])
        allowed_channel_ids = list(settings.get("allowed_channel_ids") or [])
        allowed_invite_codes = {str(x).lower() for x in list(settings.get("allowed_invite_codes") or [])}

        if str(member.id) in exempt_user_ids:
            return False

        if _member_has_any_role(member, exempt_role_ids):
            return False

        invite_role_bypass = _member_has_any_role(member, invite_allowed_role_ids)
        channel_bypass = str(message.channel.id) in allowed_channel_ids

        invite_codes = _extract_invite_codes(message.content or "")
        non_invite_urls = _extract_non_invite_urls(message.content or "")
        guild_invite_codes: Set[str] = set()

        if bool(settings.get("allow_server_invites", True)) and invite_codes:
            guild_invite_codes = await _fetch_guild_invite_codes(guild)

        blocked_invite_codes: List[str] = []
        allowed_invite_count = 0
        for code in invite_codes:
            allowed = False
            if code in allowed_invite_codes:
                allowed = True
            if bool(settings.get("allow_server_invites", True)) and code in guild_invite_codes:
                allowed = True

            if allowed:
                allowed_invite_count += 1
            else:
                blocked_invite_codes.append(code)

        now_mono = time.monotonic()
        state_key = f"spam:{guild.id}:{member.id}"

        async with _lock(state_key):
            _touch_lock(state_key)

            state = _state_for_user(guild.id, member.id)
            _cleanup_state(
                state,
                now_mono=now_mono,
                keep_seconds=max(15, int(settings["window_seconds"]) * 4),
            )

            messages: Deque[Dict[str, Any]] = state["messages"]

            mentions_everyone = _message_mentions_everyone(message)
            content_norm = _normalize_message_content(message.content or "")
            message_evidence = _build_message_evidence(
                invite_count=len(invite_codes),
                blocked_invite_count=len(blocked_invite_codes),
                non_invite_url_count=len(non_invite_urls),
                mentions_everyone=mentions_everyone,
            )

            messages.append(
                {
                    "ts": now_mono,
                    "channel_id": int(message.channel.id),
                    "message_id": int(message.id),
                    "norm": content_norm,
                    "blocked_invites": len(blocked_invite_codes),
                    "allowed_invites": int(allowed_invite_count),
                    "invite_count": len(invite_codes),
                    "non_invite_url_count": len(non_invite_urls),
                    "mention_everyone": mentions_everyone,
                    "evidence": list(message_evidence),
                }
            )

            recent_cutoff = now_mono - float(settings["window_seconds"])
            recent_messages = [
                row for row in list(messages)
                if float(row.get("ts", 0.0) or 0.0) >= recent_cutoff
            ]

            recent_count = len(recent_messages)
            duplicate_count = 0
            if content_norm:
                duplicate_count = sum(
                    1 for row in recent_messages
                    if str(row.get("norm") or "") == content_norm
                )

            blocked_invite_recent_count = sum(
                1 for row in recent_messages
                if int(row.get("blocked_invites", 0) or 0) > 0
            )
            total_blocked_invites = sum(int(row.get("blocked_invites", 0) or 0) for row in recent_messages)

            invite_message_recent_count = sum(
                1 for row in recent_messages
                if int(row.get("invite_count", 0) or 0) > 0
            )
            total_invites_recent = sum(int(row.get("invite_count", 0) or 0) for row in recent_messages)
            invite_channel_count = len(
                {
                    int(row.get("channel_id", 0) or 0)
                    for row in recent_messages
                    if int(row.get("channel_id", 0) or 0) > 0 and int(row.get("invite_count", 0) or 0) > 0
                }
            )

            url_message_recent_count = sum(
                1 for row in recent_messages
                if int(row.get("non_invite_url_count", 0) or 0) > 0
            )
            total_non_invite_urls = sum(int(row.get("non_invite_url_count", 0) or 0) for row in recent_messages)
            url_channel_count = len(
                {
                    int(row.get("channel_id", 0) or 0)
                    for row in recent_messages
                    if int(row.get("channel_id", 0) or 0) > 0 and int(row.get("non_invite_url_count", 0) or 0) > 0
                }
            )

            channel_count = len(
                {
                    int(row.get("channel_id", 0) or 0)
                    for row in recent_messages
                    if int(row.get("channel_id", 0) or 0) > 0
                }
            )
            mention_everyone_count = sum(1 for row in recent_messages if bool(row.get("mention_everyone")))

            should_fire = False
            reasons: List[str] = []

            fired_invite_rule = False
            fired_duplicate_rule = False
            fired_everyone_rule = False
            fired_url_rule = False
            fired_channel_flood_rule = False

            if not invite_role_bypass and not channel_bypass:
                if len(blocked_invite_codes) >= int(settings["multi_invite_immediate"]):
                    should_fire = True
                    fired_invite_rule = True
                    reasons.append(f"single message contained `{len(blocked_invite_codes)}` blocked invite links")

                if blocked_invite_recent_count >= int(settings["invite_threshold"]):
                    should_fire = True
                    fired_invite_rule = True
                    reasons.append(
                        f"`{blocked_invite_recent_count}` blocked-invite messages inside `{int(settings['window_seconds'])}s`"
                    )

                if recent_count >= int(settings["message_threshold"]) and len(blocked_invite_codes) > 0:
                    should_fire = True
                    fired_invite_rule = True
                    reasons.append(
                        f"`{recent_count}` messages inside `{int(settings['window_seconds'])}s` and current message contained a blocked invite"
                    )

                if len(invite_codes) >= int(settings["multi_invite_immediate"]):
                    should_fire = True
                    fired_invite_rule = True
                    if len(blocked_invite_codes) > 0:
                        reasons.append(
                            f"single message contained `{len(invite_codes)}` invite links (`{len(blocked_invite_codes)}` blocked)"
                        )
                    else:
                        reasons.append(
                            f"single message contained `{len(invite_codes)}` invite links, which looks like invite flooding even though they may be individually allowed"
                        )

                if invite_message_recent_count >= int(settings["invite_threshold"]) and invite_channel_count >= 2:
                    should_fire = True
                    fired_invite_rule = True
                    if total_blocked_invites > 0:
                        reasons.append(
                            f"rapid invite posting across `{invite_channel_count}` channels (`{invite_message_recent_count}` invite messages, some blocked)"
                        )
                    else:
                        reasons.append(
                            f"rapid invite posting across `{invite_channel_count}` channels (`{invite_message_recent_count}` invite messages) even though the codes may be individually allowed"
                        )

                if invite_message_recent_count >= int(settings["invite_threshold"]) and recent_count >= int(settings["message_threshold"]):
                    should_fire = True
                    fired_invite_rule = True
                    if total_blocked_invites > 0:
                        reasons.append(
                            f"`{invite_message_recent_count}` invite-link messages during a broader `{recent_count}` message burst"
                        )
                    else:
                        reasons.append(
                            f"`{invite_message_recent_count}` invite-link messages during a broader `{recent_count}` message burst, which looks like hacked-account spam"
                        )

            if duplicate_count >= int(settings["duplicate_threshold"]) and recent_count >= 3:
                should_fire = True
                fired_duplicate_rule = True
                reasons.append(
                    f"same message repeated `{duplicate_count}` times inside `{int(settings['window_seconds'])}s`"
                )

            if channel_count >= 3 and recent_count >= int(settings["message_threshold"]):
                should_fire = True
                fired_channel_flood_rule = True
                reasons.append(f"rapid posting across `{channel_count}` channels")

            if mention_everyone_count >= 2:
                should_fire = True
                fired_everyone_rule = True
                reasons.append("repeated @everyone/@here behavior")

            if not member_is_verified:
                if len(non_invite_urls) >= UNVERIFIED_SINGLE_MESSAGE_URL_TRIGGER:
                    should_fire = True
                    fired_url_rule = True
                    reasons.append(
                        f"single message contained `{len(non_invite_urls)}` non-invite URLs from an unverified member"
                    )

                if (
                    url_message_recent_count >= UNVERIFIED_URL_MESSAGE_THRESHOLD
                    and url_channel_count >= UNVERIFIED_URL_CHANNEL_THRESHOLD
                ):
                    should_fire = True
                    fired_url_rule = True
                    reasons.append(
                        f"rapid non-invite URL posting across `{url_channel_count}` channels by an unverified member"
                    )

            last_action_at = float(state.get("last_action_at", 0.0) or 0.0)
            if should_fire and (now_mono - last_action_at) < float(settings["cooldown_seconds"]):
                return True

            if not should_fire:
                return False

            state["last_action_at"] = now_mono

            cleanup_refs: List[Dict[str, Any]] = []
            delete_count = 0

            if _normalize_mode(settings.get("mode")) in {"delete_only", "timeout", "quarantine", "kick", "ban"}:
                cleanup_refs = _select_cleanup_refs(
                    recent_messages=recent_messages,
                    delete_limit=int(settings["delete_history"]),
                    current_norm=content_norm,
                    fired_invite_rule=fired_invite_rule,
                    fired_duplicate_rule=fired_duplicate_rule,
                    fired_everyone_rule=fired_everyone_rule,
                    fired_url_rule=fired_url_rule,
                    fired_channel_flood_rule=fired_channel_flood_rule,
                )
                delete_count = await _delete_recent_messages(
                    guild=guild,
                    refs=cleanup_refs,
                    reason="Spam guard cleanup",
                )

            action_taken, quarantine_case = await _apply_mode_action(
                guild=guild,
                member=member,
                settings=settings,
                reason="Spam guard: probable hacked-account spam burst",
            )

            await _log_trigger(
                guild=guild,
                member=member,
                settings=settings,
                reasons=reasons,
                deleted_count=delete_count,
                action_taken=action_taken,
                recent_count=recent_count,
                duplicate_count=duplicate_count,
                blocked_invite_count=total_blocked_invites,
                invite_message_count=invite_message_recent_count,
                total_invite_count=total_invites_recent,
                url_message_count=url_message_recent_count,
                channel_count=channel_count,
                cleanup_refs=cleanup_refs,
                quarantine_case=quarantine_case,
            )

            try:
                RUNTIME_STATS["spam_guard_hits"] = int(RUNTIME_STATS.get("spam_guard_hits", 0) or 0) + 1
                RUNTIME_STATS["spam_guard_non_invite_urls_seen"] = int(
                    RUNTIME_STATS.get("spam_guard_non_invite_urls_seen", 0) or 0
                ) + int(total_non_invite_urls)
            except Exception:
                pass

            return True

    except Exception as e:
        _debug(f"message handler failed error={repr(e)}")
        return False


class SpamThresholdsModal(discord.ui.Modal, title="Spam Guard • Detection Rules"):
    def __init__(self, guild_id: int, channel_id: int, message_id: int, return_page: str, settings: Dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.return_page = return_page if return_page in SPAM_PANEL_PAGES else "detection"

        self.window_seconds = discord.ui.TextInput(
            label="Window Seconds",
            placeholder="How many seconds the bot should look back",
            default=str(int(settings["window_seconds"])),
            required=True,
            max_length=3,
        )
        self.message_threshold = discord.ui.TextInput(
            label="Message Threshold",
            placeholder="How many messages in the window should trigger",
            default=str(int(settings["message_threshold"])),
            required=True,
            max_length=3,
        )
        self.duplicate_threshold = discord.ui.TextInput(
            label="Duplicate Threshold",
            placeholder="How many repeated copies of the same message",
            default=str(int(settings["duplicate_threshold"])),
            required=True,
            max_length=3,
        )
        self.invite_threshold = discord.ui.TextInput(
            label="Invite-Message Threshold",
            placeholder="How many messages with invite links should contribute to a trigger",
            default=str(int(settings["invite_threshold"])),
            required=True,
            max_length=3,
        )
        self.multi_invite_immediate = discord.ui.TextInput(
            label="Immediate Multi-Invite Trigger",
            placeholder="How many invite links in one message should instantly trigger",
            default=str(int(settings["multi_invite_immediate"])),
            required=True,
            max_length=3,
        )

        self.add_item(self.window_seconds)
        self.add_item(self.message_threshold)
        self.add_item(self.duplicate_threshold)
        self.add_item(self.invite_threshold)
        self.add_item(self.multi_invite_immediate)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _ensure_staff_panel_access(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return await _reply_ephemeral(interaction, "Invalid context.")

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception as e:
            _debug(f"threshold modal defer failed guild={guild.id} error={repr(e)}")

        try:
            patch = {
                "window_seconds": _validated_int(self.window_seconds.value, label="Window Seconds", min_value=5, max_value=60),
                "message_threshold": _validated_int(self.message_threshold.value, label="Message Threshold", min_value=3, max_value=20),
                "duplicate_threshold": _validated_int(self.duplicate_threshold.value, label="Duplicate Threshold", min_value=2, max_value=12),
                "invite_threshold": _validated_int(self.invite_threshold.value, label="Invite-Message Threshold", min_value=1, max_value=12),
                "multi_invite_immediate": _validated_int(self.multi_invite_immediate.value, label="Immediate Multi-Invite Trigger", min_value=2, max_value=8),
            }
            _, persisted = await save_spam_settings(
                self.guild_id,
                patch,
                updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
            )
        except Exception as e:
            return await _reply_ephemeral(interaction, f"❌ Failed to save detection rules: {e}")

        ch = guild.get_channel(self.channel_id)
        if isinstance(ch, discord.TextChannel):
            await _rerender_panel_message(
                guild=guild,
                channel=ch,
                message_id=self.message_id,
                page=self.return_page,
                persisted_hint=persisted,
            )

        try:
            await interaction.followup.send(
                f"✅ Detection rules updated.\nPersistence: `{_build_persistence_label(self.guild_id, persisted)}`",
                ephemeral=True,
            )
        except Exception:
            pass


class SpamActionSettingsModal(discord.ui.Modal, title="Spam Guard • Actions + Codes"):
    def __init__(self, guild_id: int, channel_id: int, message_id: int, return_page: str, settings: Dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.return_page = return_page if return_page in SPAM_PANEL_PAGES else "enforcement"

        self.timeout_minutes = discord.ui.TextInput(
            label="Timeout Minutes",
            placeholder="Used in timeout and quarantine modes",
            default=str(int(settings["timeout_minutes"])),
            required=True,
            max_length=4,
        )
        self.delete_history = discord.ui.TextInput(
            label="Max Matching Messages To Delete",
            placeholder="Only matched messages tied to the trigger are removed",
            default=str(int(settings["delete_history"])),
            required=True,
            max_length=3,
        )
        self.cooldown_seconds = discord.ui.TextInput(
            label="Repeat Action Cooldown Seconds",
            placeholder="Stops the same user from being punished repeatedly too fast",
            default=str(int(settings["cooldown_seconds"])),
            required=True,
            max_length=4,
        )
        self.quarantine_role_id = discord.ui.TextInput(
            label="Quarantine Role ID (optional)",
            placeholder="Used only when mode = quarantine",
            default=_safe_str(settings.get("quarantine_role_id")),
            required=False,
            max_length=25,
        )
        self.allowed_invite_codes = discord.ui.TextInput(
            label="Allowed Invite Codes",
            placeholder="Comma or newline separated. Example: myserver, staffhub",
            default=", ".join(list(settings.get("allowed_invite_codes") or [])),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )

        self.add_item(self.timeout_minutes)
        self.add_item(self.delete_history)
        self.add_item(self.cooldown_seconds)
        self.add_item(self.quarantine_role_id)
        self.add_item(self.allowed_invite_codes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _ensure_staff_panel_access(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return await _reply_ephemeral(interaction, "Invalid context.")

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception as e:
            _debug(f"actions modal defer failed guild={guild.id} error={repr(e)}")

        try:
            quarantine_role_id = _safe_str(self.quarantine_role_id.value)
            if quarantine_role_id and not quarantine_role_id.isdigit():
                raise ValueError("Quarantine Role ID must be blank or a numeric role ID.")

            patch = {
                "timeout_minutes": _validated_int(self.timeout_minutes.value, label="Timeout Minutes", min_value=1, max_value=1440),
                "delete_history": _validated_int(self.delete_history.value, label="Max Matching Messages To Delete", min_value=1, max_value=30),
                "cooldown_seconds": _validated_int(self.cooldown_seconds.value, label="Repeat Action Cooldown Seconds", min_value=5, max_value=300),
                "quarantine_role_id": quarantine_role_id,
                "allowed_invite_codes": _parse_csvish_codes(_safe_str(self.allowed_invite_codes.value)),
            }
            _, persisted = await save_spam_settings(
                self.guild_id,
                patch,
                updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
            )
        except Exception as e:
            return await _reply_ephemeral(interaction, f"❌ Failed to save action settings: {e}")

        ch = guild.get_channel(self.channel_id)
        if isinstance(ch, discord.TextChannel):
            await _rerender_panel_message(
                guild=guild,
                channel=ch,
                message_id=self.message_id,
                page=self.return_page,
                persisted_hint=persisted,
            )

        try:
            await interaction.followup.send(
                f"✅ Actions and allowed invite codes updated.\nPersistence: `{_build_persistence_label(self.guild_id, persisted)}`",
                ephemeral=True,
            )
        except Exception:
            pass


class SpamListsModal(discord.ui.Modal, title="Spam Guard • Channels + Users"):
    def __init__(self, guild_id: int, channel_id: int, message_id: int, return_page: str, settings: Dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.return_page = return_page if return_page in SPAM_PANEL_PAGES else "access"

        self.allowed_channel_ids = discord.ui.TextInput(
            label="Allowed Channel IDs",
            placeholder="Invite-link rules will not fire in these channels",
            default=", ".join(list(settings.get("allowed_channel_ids") or [])),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.exempt_user_ids = discord.ui.TextInput(
            label="Exempt User IDs",
            placeholder="These users bypass the spam guard completely",
            default=", ".join(list(settings.get("exempt_user_ids") or [])),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )

        self.add_item(self.allowed_channel_ids)
        self.add_item(self.exempt_user_ids)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _ensure_staff_panel_access(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return await _reply_ephemeral(interaction, "Invalid context.")

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception as e:
            _debug(f"lists modal defer failed guild={guild.id} error={repr(e)}")

        try:
            patch = {
                "allowed_channel_ids": _parse_csvish_ids(_safe_str(self.allowed_channel_ids.value)),
                "exempt_user_ids": _parse_csvish_ids(_safe_str(self.exempt_user_ids.value)),
            }
            _, persisted = await save_spam_settings(
                self.guild_id,
                patch,
                updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
            )
        except Exception as e:
            return await _reply_ephemeral(interaction, f"❌ Failed to save channels/users: {e}")

        ch = guild.get_channel(self.channel_id)
        if isinstance(ch, discord.TextChannel):
            await _rerender_panel_message(
                guild=guild,
                channel=ch,
                message_id=self.message_id,
                page=self.return_page,
                persisted_hint=persisted,
            )

        try:
            await interaction.followup.send(
                f"✅ Channels and exempt users updated.\nPersistence: `{_build_persistence_label(self.guild_id, persisted)}`",
                ephemeral=True,
            )
        except Exception:
            pass


class SpamSectionSelect(discord.ui.Select):
    def __init__(self, current_page: str):
        options = [
            discord.SelectOption(
                label="Overview",
                value="overview",
                description="Main summary and simple explanations",
                default=current_page == "overview",
            ),
            discord.SelectOption(
                label="Detection Rules",
                value="detection",
                description="What counts as spam",
                default=current_page == "detection",
            ),
            discord.SelectOption(
                label="Response Actions",
                value="enforcement",
                description="What the bot does after detection",
                default=current_page == "enforcement",
            ),
            discord.SelectOption(
                label="Allow Lists + Exemptions",
                value="access",
                description="Who is allowed or exempt",
                default=current_page == "access",
            ),
        ]
        super().__init__(
            placeholder="Open a spam guard section…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"spamguard:{current_page}:section",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        target_page = self.values[0] if self.values and self.values[0] in SPAM_PANEL_PAGES else "overview"
        await _switch_panel_page(interaction, page=target_page)


class SpamModeSelect(discord.ui.Select):
    def __init__(self, current_page: str, current_mode: str):
        options = [
            discord.SelectOption(label="Log Only", value="log_only", description="Only record the event", default=current_mode == "log_only"),
            discord.SelectOption(label="Delete Only", value="delete_only", description="Remove matched spam messages only", default=current_mode == "delete_only"),
            discord.SelectOption(label="Timeout", value="timeout", description="Remove matched spam and timeout the user", default=current_mode == "timeout"),
            discord.SelectOption(label="Quarantine", value="quarantine", description="Remove matched spam, quarantine, and allow one-click restore", default=current_mode == "quarantine"),
            discord.SelectOption(label="Kick", value="kick", description="Remove matched spam and kick the user", default=current_mode == "kick"),
            discord.SelectOption(label="Ban", value="ban", description="Remove matched spam and ban the user", default=current_mode == "ban"),
        ]
        super().__init__(
            placeholder="Choose response mode…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"spamguard:{current_page}:mode",
            row=2,
        )
        self.current_page = current_page

    async def callback(self, interaction: discord.Interaction) -> None:
        new_mode = _normalize_mode(self.values[0], "timeout")
        await _save_patch_and_rerender(
            interaction,
            patch={"mode": new_mode},
            page=self.current_page,
            success_text=f"Spam guard mode set to `{new_mode}`.",
        )


class SpamInviteAllowedRolesSelect(discord.ui.RoleSelect):
    def __init__(self, current_page: str):
        super().__init__(
            placeholder="Invite-allowed roles",
            min_values=0,
            max_values=10,
            custom_id=f"spamguard:{current_page}:invite_roles",
            row=2,
        )
        self.current_page = current_page

    async def callback(self, interaction: discord.Interaction) -> None:
        await _save_patch_and_rerender(
            interaction,
            patch={"invite_allowed_role_ids": [str(role.id) for role in self.values]},
            page=self.current_page,
            success_text="Invite-allowed roles updated.",
        )


class SpamExemptRolesSelect(discord.ui.RoleSelect):
    def __init__(self, current_page: str):
        super().__init__(
            placeholder="Fully exempt roles",
            min_values=0,
            max_values=10,
            custom_id=f"spamguard:{current_page}:exempt_roles",
            row=3,
        )
        self.current_page = current_page

    async def callback(self, interaction: discord.Interaction) -> None:
        await _save_patch_and_rerender(
            interaction,
            patch={"exempt_role_ids": [str(role.id) for role in self.values]},
            page=self.current_page,
            success_text="Fully exempt roles updated.",
        )


class ToggleEnabledButton(discord.ui.Button):
    def __init__(self, page: str, enabled: bool):
        super().__init__(
            label="Disable Guard" if enabled else "Enable Guard",
            style=discord.ButtonStyle.danger if enabled else discord.ButtonStyle.success,
            custom_id=f"spamguard:{page}:toggle_enabled",
            row=0,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await _reply_ephemeral(interaction, "Invalid context.")
        current = await get_spam_settings(guild.id)
        new_value = not bool(current["enabled"])
        await _save_patch_and_rerender(
            interaction,
            patch={"enabled": new_value},
            page=self.page,
            success_text=f"Spam guard {'enabled' if new_value else 'disabled'}.",
        )


class RefreshPanelButton(discord.ui.Button):
    def __init__(self, page: str):
        super().__init__(
            label="Refresh",
            style=discord.ButtonStyle.primary,
            custom_id=f"spamguard:{page}:refresh",
            row=0,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _ensure_staff_panel_access(interaction):
            return
        await _switch_panel_page(interaction, page=self.page)
        try:
            if interaction.response.is_done():
                await interaction.followup.send("✅ Panel refreshed.", ephemeral=True)
        except Exception:
            pass


class ToggleExternalOnlyButton(discord.ui.Button):
    def __init__(self, page: str, enabled: bool):
        super().__init__(
            label="External Only",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            custom_id=f"spamguard:{page}:toggle_external_only",
            row=4,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await _reply_ephemeral(interaction, "Invalid context.")
        current = await get_spam_settings(guild.id)
        new_value = not bool(current["block_external_invites_only"])
        await _save_patch_and_rerender(
            interaction,
            patch={"block_external_invites_only": new_value},
            page=self.page,
            success_text=f"External-only invite blocking set to `{new_value}`.",
        )


class ToggleAllowServerInvitesButton(discord.ui.Button):
    def __init__(self, page: str, enabled: bool):
        super().__init__(
            label="Allow Own Invites",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            custom_id=f"spamguard:{page}:toggle_server_invites",
            row=4,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await _reply_ephemeral(interaction, "Invalid context.")
        current = await get_spam_settings(guild.id)
        new_value = not bool(current["allow_server_invites"])
        await _save_patch_and_rerender(
            interaction,
            patch={"allow_server_invites": new_value},
            page=self.page,
            success_text=f"Allow-this-server's-invites set to `{new_value}`.",
        )


class ToggleVerifiedUsersButton(discord.ui.Button):
    def __init__(self, page: str, enabled: bool):
        super().__init__(
            label="Watch Verified",
            style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
            custom_id=f"spamguard:{page}:toggle_verified",
            row=4,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await _reply_ephemeral(interaction, "Invalid context.")
        current = await get_spam_settings(guild.id)
        new_value = not bool(current["apply_to_verified_users"])
        await _save_patch_and_rerender(
            interaction,
            patch={"apply_to_verified_users": new_value},
            page=self.page,
            success_text=f"Apply-to-verified-users set to `{new_value}`.",
        )


class ThresholdsModalButton(discord.ui.Button):
    def __init__(self, page: str):
        super().__init__(
            label="Detection Rules",
            style=discord.ButtonStyle.secondary,
            custom_id=f"spamguard:{page}:thresholds",
            row=3,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _ensure_staff_panel_access(interaction):
            return
        guild = interaction.guild
        channel = interaction.channel
        message = interaction.message
        if guild is None or not isinstance(channel, discord.TextChannel) or message is None:
            return await _reply_ephemeral(interaction, "Invalid context.")

        settings = _fast_settings_for_ui(guild.id)
        await interaction.response.send_modal(
            SpamThresholdsModal(
                guild.id,
                channel.id,
                message.id,
                self.page,
                settings,
            )
        )


class ActionsModalButton(discord.ui.Button):
    def __init__(self, page: str):
        super().__init__(
            label="Actions + Codes",
            style=discord.ButtonStyle.secondary,
            custom_id=f"spamguard:{page}:actions",
            row=3 if page != "access" else 4,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _ensure_staff_panel_access(interaction):
            return
        guild = interaction.guild
        channel = interaction.channel
        message = interaction.message
        if guild is None or not isinstance(channel, discord.TextChannel) or message is None:
            return await _reply_ephemeral(interaction, "Invalid context.")

        settings = _fast_settings_for_ui(guild.id)
        await interaction.response.send_modal(
            SpamActionSettingsModal(
                guild.id,
                channel.id,
                message.id,
                self.page,
                settings,
            )
        )


class ListsModalButton(discord.ui.Button):
    def __init__(self, page: str):
        super().__init__(
            label="Channels + Users",
            style=discord.ButtonStyle.secondary,
            custom_id=f"spamguard:{page}:lists",
            row=3 if page != "access" else 4,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _ensure_staff_panel_access(interaction):
            return
        guild = interaction.guild
        channel = interaction.channel
        message = interaction.message
        if guild is None or not isinstance(channel, discord.TextChannel) or message is None:
            return await _reply_ephemeral(interaction, "Invalid context.")

        settings = _fast_settings_for_ui(guild.id)
        await interaction.response.send_modal(
            SpamListsModal(
                guild.id,
                channel.id,
                message.id,
                self.page,
                settings,
            )
        )


class SpamGuardPanelView(discord.ui.View):
    def __init__(self, *, page: str):
        super().__init__(timeout=None)
        self.page = page if page in SPAM_PANEL_PAGES else "overview"

    @classmethod
    def build(cls, *, page: str, settings: Dict[str, Any]) -> "SpamGuardPanelView":
        view = cls(page=page)

        view.add_item(ToggleEnabledButton(page, bool(settings["enabled"])))
        view.add_item(RefreshPanelButton(page))
        view.add_item(SpamSectionSelect(page))

        if page == "access":
            view.add_item(SpamInviteAllowedRolesSelect(page))
            view.add_item(SpamExemptRolesSelect(page))
            view.add_item(ActionsModalButton(page))
            view.add_item(ListsModalButton(page))
        else:
            view.add_item(SpamModeSelect(page, _normalize_mode(settings.get("mode"), "timeout")))
            view.add_item(ThresholdsModalButton(page))
            view.add_item(ActionsModalButton(page))
            view.add_item(ListsModalButton(page))
            view.add_item(ToggleExternalOnlyButton(page, bool(settings["block_external_invites_only"])))
            view.add_item(ToggleAllowServerInvitesButton(page, bool(settings["allow_server_invites"])))
            view.add_item(ToggleVerifiedUsersButton(page, bool(settings["apply_to_verified_users"])))

        return view


def _register_spam_guard_commands() -> None:
    global _SPAM_GUARD_COMMANDS_REGISTERED

    if _SPAM_GUARD_COMMANDS_REGISTERED:
        return

    if bot.tree.get_command("spam_guard") is None:
        @bot.tree.command(
            name="spam_guard",
            description="(Staff) Open the spam guard control panel.",
        )
        @app_commands.guild_only()
        async def spam_guard(interaction: discord.Interaction):
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            guild = interaction.guild
            channel = interaction.channel

            if not isinstance(member, discord.Member) or guild is None or not isinstance(channel, discord.TextChannel):
                return await _reply_ephemeral(interaction, "This command must be used in a server text channel.")

            if not _is_staffish(member):
                return await _reply_ephemeral(interaction, "You do not have permission to use this command.")

            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

            settings = await get_spam_settings(guild.id)
            embed = _build_panel_embed(guild, settings, page="overview")
            view = SpamGuardPanelView.build(page="overview", settings=settings)

            existing = await _find_existing_panel(channel)
            if existing is not None:
                try:
                    await existing.edit(embed=embed, view=view)
                    return await interaction.followup.send(
                        f"✅ Spam guard panel refreshed in {channel.mention}.",
                        ephemeral=True,
                    )
                except Exception:
                    pass

            try:
                await channel.send(embed=embed, view=view)
                return await interaction.followup.send(
                    f"✅ Spam guard panel posted in {channel.mention}.",
                    ephemeral=True,
                )
            except Exception as e:
                return await interaction.followup.send(
                    f"❌ Failed to post panel: {e}",
                    ephemeral=True,
                )

    if bot.tree.get_command("spam_guard_status") is None:
        @bot.tree.command(
            name="spam_guard_status",
            description="(Staff) Show the current spam guard status.",
        )
        @app_commands.guild_only()
        async def spam_guard_status(interaction: discord.Interaction):
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            guild = interaction.guild

            if not isinstance(member, discord.Member) or guild is None:
                return await _reply_ephemeral(interaction, "This command must be used in a server.")

            if not _is_staffish(member):
                return await _reply_ephemeral(interaction, "You do not have permission to use this command.")

            await interaction.response.defer(ephemeral=True)
            settings = await get_spam_settings(guild.id)
            embed = _build_panel_embed(guild, settings, page="overview")
            await interaction.followup.send(embed=embed, ephemeral=True)

    _SPAM_GUARD_COMMANDS_REGISTERED = True


@bot.listen("on_message")
async def _spam_guard_on_message(message: discord.Message):
    await handle_incoming_spam_message(message)


@bot.listen("on_ready")
async def _spam_guard_warm_settings_cache():
    if not claim_startup_flag("spam-guard-warm-settings"):
        return

    try:
        guilds = list(bot.guilds)
        enabled_guild_ids: List[int] = []
        runtime_only_enabled_guild_ids: List[int] = []

        for guild in guilds:
            settings = await get_spam_settings(guild.id)
            cached = _cached_runtime_settings(guild.id) or {}
            enabled = bool(settings.get("enabled"))
            persisted = bool(cached.get("__meta_persisted"))

            if enabled:
                enabled_guild_ids.append(int(guild.id))
                if not persisted:
                    runtime_only_enabled_guild_ids.append(int(guild.id))

        _debug(
            "startup settings "
            f"guilds={len(guilds)} "
            f"enabled={len(enabled_guild_ids)} "
            f"runtime_only_enabled={len(runtime_only_enabled_guild_ids)}"
        )

        if runtime_only_enabled_guild_ids:
            _debug(
                "startup settings runtime_only_guild_ids="
                + ",".join(str(gid) for gid in runtime_only_enabled_guild_ids[:20])
            )
    except Exception as e:
        _debug(f"startup settings warm failed error={repr(e)}")


@bot.listen("on_ready")
async def _register_spam_guard_views():
    global _SPAM_GUARD_VIEWS_REGISTERED

    cleanup_started = False

    if not cleanup_stale_memory.is_running():
        try:
            cleanup_stale_memory.start()
            cleanup_started = True
        except Exception as e:
            _debug(f"startup cleanup loop failed error={repr(e)}")

    if _SPAM_GUARD_VIEWS_REGISTERED:
        if cleanup_started:
            _debug("startup cleanup loop started")
        return

    try:
        for page in SPAM_PANEL_PAGES:
            bot.add_view(SpamGuardPanelView.build(page=page, settings=_default_settings(0)))
        bot.add_view(SpamIncidentRestoreView(restored=False))
        bot.add_view(SpamIncidentRestoreView(restored=True))
        _SPAM_GUARD_VIEWS_REGISTERED = True

        parts = []
        if cleanup_started:
            parts.append("cleanup_loop=started")
        else:
            parts.append(f"cleanup_loop={'running' if cleanup_stale_memory.is_running() else 'stopped'}")
        parts.append("persistent_views=ready")
        parts.append(f"pages={len(SPAM_PANEL_PAGES)}")
        _debug("startup " + " ".join(parts))
    except Exception as e:
        _debug(f"startup persistent views failed error={repr(e)}")


_register_spam_guard_commands()


__all__ = [
    "get_spam_settings",
    "save_spam_settings",
    "get_quarantine_case",
    "save_quarantine_case",
    "handle_incoming_spam_message",
]
