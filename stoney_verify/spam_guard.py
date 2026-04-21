from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import discord
from discord import app_commands

from .globals import *  # noqa: F401,F403

# ============================================================
# Spam / hacked-account guard with compact multi-page control UI
# ------------------------------------------------------------
# UX goals:
# - mobile-friendly
# - compact dashboard instead of one giant embed
# - section-based navigation like a real control center
# - fully featured and persistent when DB schema supports it
#
# Feature set:
# - /spam_guard posts or refreshes the control panel
# - /spam_guard_status shows compact ephemeral status
# - pages: overview / detection / enforcement / access
# - mode select: log_only / delete_only / timeout / quarantine / kick / ban
# - invite-allowed roles
# - fully exempt roles
# - exempt users
# - allowed channels
# - allowed invite codes
# - external-only blocking toggle
# - allow-own-server-invites toggle
# - apply-to-verified toggle
# - thresholds modal
# - actions + codes modal
# - users + channels modal
# - persistent views (page-specific custom_ids)
# - runtime works immediately
# - DB persistence is best-effort and falls back to runtime when schema is missing
# ============================================================

GUILD_SECURITY_SETTINGS_TABLE = "guild_security_settings"

SPAM_PANEL_FOOTER_BASE = "stoney_verify:spam_guard_panel:v5"
SPAM_PANEL_FOOTER_PREFIX = "stoney_verify:spam_guard_panel:"
SPAM_PANEL_PAGES = ("overview", "detection", "enforcement", "access")

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)/([A-Za-z0-9-]+)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

VALID_MODES = {"log_only", "delete_only", "timeout", "quarantine", "kick", "ban"}

_SETTINGS_TABLE_AVAILABLE: Optional[bool] = None
_SPAM_GUARD_COMMANDS_REGISTERED = False
_SPAM_GUARD_VIEWS_REGISTERED = False

_RUNTIME_SETTINGS: Dict[int, Dict[str, Any]] = {}
_MESSAGE_WINDOWS: Dict[Tuple[int, int], Dict[str, Any]] = {}
_LOCKS: Dict[str, asyncio.Lock] = {}
_GUILD_INVITE_CACHE: Dict[int, Dict[str, Any]] = {}


# ============================================================
# Small helpers
# ============================================================

def _lock(key: str) -> asyncio.Lock:
    clean = str(key or "").strip() or "default"
    found = _LOCKS.get(clean)
    if found is None:
        found = asyncio.Lock()
        _LOCKS[clean] = found
    return found


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


def _is_table_missing_error(exc: Exception) -> bool:
    text = repr(exc or "").lower()
    return (
        GUILD_SECURITY_SETTINGS_TABLE in text
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


def _normalize_id_list(values: Any, *, limit: int = 50) -> List[str]:
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


def _normalize_code_list(values: Any, *, limit: int = 50) -> List[str]:
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


def _parse_csvish_ids(text: str, *, limit: int = 50) -> List[str]:
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


def _parse_csvish_codes(text: str, *, limit: int = 50) -> List[str]:
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
        "detection": "Detection",
        "enforcement": "Enforcement",
        "access": "Access",
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


# ============================================================
# Settings defaults / normalization
# ============================================================

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


# ============================================================
# Settings persistence
# ============================================================

def _fetch_settings_sync(guild_id: int) -> Optional[Dict[str, Any]]:
    global _SETTINGS_TABLE_AVAILABLE

    sb = _sb()
    if sb is None:
        return None

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
            return dict(rows[0])
        return None
    except Exception as e:
        if _is_table_missing_error(e):
            _SETTINGS_TABLE_AVAILABLE = False
            return None
        raise


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
        if _is_table_missing_error(e):
            _SETTINGS_TABLE_AVAILABLE = False
            return False

        _SETTINGS_TABLE_AVAILABLE = False
        _debug(f"settings upsert runtime fallback error={repr(e)}")
        return False


async def get_spam_settings(guild_id: int) -> Dict[str, Any]:
    gid = int(guild_id)

    runtime = _RUNTIME_SETTINGS.get(gid)
    if isinstance(runtime, dict):
        return _normalize_settings(gid, runtime)

    if _SETTINGS_TABLE_AVAILABLE is False:
        return _default_settings(gid)

    try:
        row = await asyncio.to_thread(_fetch_settings_sync, gid)
        normalized = _normalize_settings(gid, row)
        _RUNTIME_SETTINGS[gid] = dict(normalized)
        return normalized
    except Exception as e:
        _debug(f"settings fetch failed guild={gid} error={repr(e)}")
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

    _RUNTIME_SETTINGS[gid] = dict(normalized)

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

    return normalized, persisted


# ============================================================
# Panel rendering
# ============================================================

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
    persistence = (
        "DB-backed"
        if persisted_hint is True
        else "Runtime only"
        if persisted_hint is False
        else "Runtime/DB auto"
    )

    title = f"🛡️ Spam Guard • {_page_title(clean_page)}"
    embed = discord.Embed(
        title=title,
        color=discord.Color.green() if enabled else discord.Color.orange(),
        timestamp=_now_utc(),
    )

    if clean_page == "overview":
        embed.description = (
            "Compact control center for hacked-account and invite spam protection.\n"
            "Use the **section menu** below to jump into what you want to edit."
        )
        embed.add_field(
            name="System",
            value=(
                f"**Enabled:** {_bool_chip(bool(settings['enabled']))}\n"
                f"**Mode:** `{mode}`\n"
                f"**Persistence:** `{persistence}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Protection Scope",
            value=(
                f"**External-only:** {_bool_chip(bool(settings['block_external_invites_only']))}\n"
                f"**Allow this server's invites:** {_bool_chip(bool(settings['allow_server_invites']))}\n"
                f"**Watch verified users:** {_bool_chip(bool(settings['apply_to_verified_users']))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Quick Detection Summary",
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
            name="Quick Access Summary",
            value=(
                f"{_compact_count('Invite roles', list(settings.get('invite_allowed_role_ids') or []))}\n"
                f"{_compact_count('Exempt roles', list(settings.get('exempt_role_ids') or []))}\n"
                f"{_compact_count('Allowed channels', list(settings.get('allowed_channel_ids') or []))}\n"
                f"{_compact_count('Exempt users', list(settings.get('exempt_user_ids') or []))}\n"
                f"{_compact_count('Allowed codes', list(settings.get('allowed_invite_codes') or []))}"
            ),
            inline=False,
        )

    elif clean_page == "detection":
        embed.description = (
            "Tune what counts as suspicious behavior.\n"
            "This page is for the rules that decide when the guard fires."
        )
        embed.add_field(
            name="Burst Rules",
            value=(
                f"**Window:** `{int(settings['window_seconds'])}s`\n"
                f"**Message threshold:** `{int(settings['message_threshold'])}`\n"
                f"**Duplicate threshold:** `{int(settings['duplicate_threshold'])}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Invite Rules",
            value=(
                f"**Invite-message threshold:** `{int(settings['invite_threshold'])}`\n"
                f"**Immediate multi-invite trigger:** `{int(settings['multi_invite_immediate'])}`\n"
                f"**External-only blocking:** {_bool_chip(bool(settings['block_external_invites_only']))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Scope Toggles",
            value=(
                f"**Allow this server's invites:** {_bool_chip(bool(settings['allow_server_invites']))}\n"
                f"**Apply to verified users:** {_bool_chip(bool(settings['apply_to_verified_users']))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Operational Notes",
            value=(
                "• Invite-allowed roles bypass **invite-link rules only**\n"
                "• Allowed channels bypass **invite-link rules only**\n"
                "• Fully exempt roles/users bypass the guard entirely"
            ),
            inline=False,
        )

    elif clean_page == "enforcement":
        embed.description = (
            "Control what happens after spam is detected.\n"
            "This page is for response severity and cleanup behavior."
        )
        embed.add_field(
            name="Response Mode",
            value=f"**Mode:** `{mode}`",
            inline=False,
        )
        embed.add_field(
            name="Cleanup",
            value=(
                f"**Delete recent messages:** `{int(settings['delete_history'])}`\n"
                f"**Action cooldown:** `{int(settings['cooldown_seconds'])}s`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Moderation Action",
            value=(
                f"**Timeout length:** `{int(settings['timeout_minutes'])}m`\n"
                f"**Quarantine role:** {_quarantine_role_text(guild, settings)}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Behavior by Mode",
            value=(
                "• `log_only` = just record it\n"
                "• `delete_only` = clean messages\n"
                "• `timeout` = clean + timeout\n"
                "• `quarantine` = clean + quarantine role\n"
                "• `kick` / `ban` = strongest actions"
            ),
            inline=False,
        )

    else:
        embed.description = (
            "Manage who is allowed to bypass parts of the guard.\n"
            "Use the role pickers below, then use the buttons for channels, users, and invite codes."
        )
        embed.add_field(
            name="Invite-Allowed Roles",
            value=_format_role_list(guild, list(settings.get("invite_allowed_role_ids") or [])),
            inline=False,
        )
        embed.add_field(
            name="Fully Exempt Roles",
            value=_format_role_list(guild, list(settings.get("exempt_role_ids") or [])),
            inline=False,
        )
        embed.add_field(
            name="Allowed Channels / Exempt Users",
            value=(
                f"**Channels:** {_format_channel_list(guild, list(settings.get('allowed_channel_ids') or []))}\n"
                f"**Users:** {_format_user_list(guild, list(settings.get('exempt_user_ids') or []))}"
            ),
            inline=False,
        )
        embed.add_field(
            name="Allowed Invite Codes",
            value=", ".join(f"`{x}`" for x in list(settings.get("allowed_invite_codes") or [])[:12]) or "—",
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
            f"✅ {success_text}\nPersistence: `{'DB-backed' if persisted else 'runtime only'}`",
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


# ============================================================
# Invite allow helpers
# ============================================================

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


# ============================================================
# Detection state
# ============================================================

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


# ============================================================
# Enforcement
# ============================================================

async def _delete_recent_messages(
    *,
    guild: discord.Guild,
    refs: List[Dict[str, Any]],
    reason: str,
) -> int:
    deleted = 0
    seen: Set[Tuple[int, int]] = set()

    for row in sorted(refs, key=lambda x: float(x.get("ts", 0.0) or 0.0), reverse=True):
        channel_id = _safe_int(row.get("channel_id"), 0)
        message_id = _safe_int(row.get("message_id"), 0)
        if channel_id <= 0 or message_id <= 0:
            continue

        key = (channel_id, message_id)
        if key in seen:
            continue
        seen.add(key)

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None

        if not isinstance(channel, discord.TextChannel):
            continue

        try:
            msg = await channel.fetch_message(message_id)
            await msg.delete(reason=reason)
            deleted += 1
        except Exception:
            continue

    return deleted


async def _apply_mode_action(
    *,
    guild: discord.Guild,
    member: discord.Member,
    settings: Dict[str, Any],
    reason: str,
) -> str:
    mode = _normalize_mode(settings.get("mode"), "timeout")
    me = guild.me

    if me is None:
        return "no-action"

    try:
        if me.top_role <= member.top_role and not me.guild_permissions.administrator:
            return "no-action"
    except Exception:
        return "no-action"

    if mode == "log_only":
        return "log-only"

    if mode == "delete_only":
        return "delete-only"

    if mode == "timeout":
        try:
            if me.guild_permissions.moderate_members:
                until = _now_utc() + timedelta(minutes=max(1, int(settings["timeout_minutes"])))
                await member.timeout(until, reason=reason)
                return f"timeout:{int(settings['timeout_minutes'])}m"
        except Exception:
            pass
        return "delete-only"

    if mode == "quarantine":
        qrid = _safe_str(settings.get("quarantine_role_id"))
        try:
            if qrid.isdigit():
                role = guild.get_role(int(qrid))
                if isinstance(role, discord.Role) and me.guild_permissions.manage_roles and me.top_role > role:
                    if role not in member.roles:
                        await member.add_roles(role, reason=reason)
                    return f"quarantine:{role.id}"
        except Exception:
            pass

        try:
            if me.guild_permissions.moderate_members:
                until = _now_utc() + timedelta(minutes=max(1, int(settings["timeout_minutes"])))
                await member.timeout(until, reason=reason)
                return f"timeout-fallback:{int(settings['timeout_minutes'])}m"
        except Exception:
            pass
        return "delete-only"

    if mode == "kick":
        try:
            if me.guild_permissions.kick_members:
                await guild.kick(member, reason=reason)
                return "kick"
        except Exception:
            pass
        return "delete-only"

    if mode == "ban":
        try:
            if me.guild_permissions.ban_members:
                await guild.ban(member, reason=reason)
                return "ban"
        except Exception:
            pass
        return "delete-only"

    return "no-action"


async def _post_modlog_embed(guild: discord.Guild, embed: discord.Embed) -> None:
    try:
        from .modlog import _post_modlog
        await _post_modlog(guild, embed)
        return
    except Exception:
        pass

    try:
        if MODLOG_CHANNEL_ID:
            ch = guild.get_channel(int(MODLOG_CHANNEL_ID))
            if isinstance(ch, discord.TextChannel):
                await ch.send(embed=embed)
    except Exception:
        pass


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
    channel_count: int,
) -> None:
    embed = discord.Embed(
        title="🛡️ Spam Guard Triggered",
        description="Probable hacked-account or invite spam burst was blocked.",
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
            f"blocked_invites=`{blocked_invite_count}` • "
            f"channels=`{channel_count}`"
        ),
        inline=False,
    )
    if reasons:
        embed.add_field(
            name="Trigger Reasons",
            value="\n".join(f"• {r}" for r in reasons[:8]),
            inline=False,
        )
    await _post_modlog_embed(guild, embed)


# ============================================================
# Main detector
# ============================================================

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

        if not bool(settings.get("apply_to_verified_users", True)) and _is_verifiedish(member):
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
        guild_invite_codes: Set[str] = set()

        if bool(settings.get("allow_server_invites", True)) and invite_codes:
            guild_invite_codes = await _fetch_guild_invite_codes(guild)

        blocked_invite_codes: List[str] = []
        for code in invite_codes:
            allowed = False
            if code in allowed_invite_codes:
                allowed = True
            if bool(settings.get("allow_server_invites", True)) and code in guild_invite_codes:
                allowed = True

            if bool(settings.get("block_external_invites_only", True)):
                if not allowed:
                    blocked_invite_codes.append(code)
            else:
                if not allowed:
                    blocked_invite_codes.append(code)

        now_mono = time.monotonic()
        state_key = f"spam:{guild.id}:{member.id}"

        async with _lock(state_key):
            state = _state_for_user(guild.id, member.id)
            _cleanup_state(
                state,
                now_mono=now_mono,
                keep_seconds=max(15, int(settings["window_seconds"]) * 4),
            )

            messages: Deque[Dict[str, Any]] = state["messages"]

            content_norm = _normalize_message_content(message.content or "")
            messages.append(
                {
                    "ts": now_mono,
                    "channel_id": int(message.channel.id),
                    "message_id": int(message.id),
                    "norm": content_norm,
                    "blocked_invites": len(blocked_invite_codes),
                    "invite_count": len(invite_codes),
                    "mention_everyone": _message_mentions_everyone(message),
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

            if not invite_role_bypass and not channel_bypass:
                if len(blocked_invite_codes) >= int(settings["multi_invite_immediate"]):
                    should_fire = True
                    reasons.append(f"single message contained `{len(blocked_invite_codes)}` blocked invite links")

                if blocked_invite_recent_count >= int(settings["invite_threshold"]):
                    should_fire = True
                    reasons.append(
                        f"`{blocked_invite_recent_count}` invite-link messages inside `{int(settings['window_seconds'])}s`"
                    )

                if recent_count >= int(settings["message_threshold"]) and len(blocked_invite_codes) > 0:
                    should_fire = True
                    reasons.append(
                        f"`{recent_count}` messages inside `{int(settings['window_seconds'])}s` and current message contained a blocked invite"
                    )

            if duplicate_count >= int(settings["duplicate_threshold"]) and recent_count >= 3:
                should_fire = True
                reasons.append(
                    f"same message repeated `{duplicate_count}` times inside `{int(settings['window_seconds'])}s`"
                )

            if channel_count >= 3 and recent_count >= int(settings["message_threshold"]):
                should_fire = True
                reasons.append(f"rapid posting across `{channel_count}` channels")

            if mention_everyone_count >= 2:
                should_fire = True
                reasons.append("repeated @everyone/@here behavior")

            last_action_at = float(state.get("last_action_at", 0.0) or 0.0)
            if should_fire and (now_mono - last_action_at) < float(settings["cooldown_seconds"]):
                return True

            if not should_fire:
                return False

            state["last_action_at"] = now_mono

            delete_count = 0
            if _normalize_mode(settings.get("mode")) in {"delete_only", "timeout", "quarantine", "kick", "ban"}:
                delete_count = await _delete_recent_messages(
                    guild=guild,
                    refs=recent_messages[-int(settings["delete_history"]):],
                    reason="Spam guard cleanup",
                )

            action_taken = await _apply_mode_action(
                guild=guild,
                member=member,
                settings=settings,
                reason="Spam guard: probable hacked-account invite spam burst",
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
                channel_count=channel_count,
            )

            try:
                RUNTIME_STATS["spam_guard_hits"] = int(RUNTIME_STATS.get("spam_guard_hits", 0) or 0) + 1
            except Exception:
                pass

            return True

    except Exception as e:
        _debug(f"message handler failed error={repr(e)}")
        return False


# ============================================================
# Modals
# ============================================================

class SpamThresholdsModal(discord.ui.Modal, title="Spam Guard • Detection Thresholds"):
    def __init__(self, guild_id: int, channel_id: int, message_id: int, return_page: str, settings: Dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.return_page = return_page if return_page in SPAM_PANEL_PAGES else "detection"

        self.window_seconds = discord.ui.TextInput(
            label="Window Seconds",
            default=str(int(settings["window_seconds"])),
            required=True,
            max_length=3,
        )
        self.message_threshold = discord.ui.TextInput(
            label="Message Threshold",
            default=str(int(settings["message_threshold"])),
            required=True,
            max_length=3,
        )
        self.duplicate_threshold = discord.ui.TextInput(
            label="Duplicate Threshold",
            default=str(int(settings["duplicate_threshold"])),
            required=True,
            max_length=3,
        )
        self.invite_threshold = discord.ui.TextInput(
            label="Invite-Message Threshold",
            default=str(int(settings["invite_threshold"])),
            required=True,
            max_length=3,
        )
        self.multi_invite_immediate = discord.ui.TextInput(
            label="Immediate Multi-Invite Trigger",
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
            _, persisted = await save_spam_settings(
                self.guild_id,
                {
                    "window_seconds": int(_safe_str(self.window_seconds.value)),
                    "message_threshold": int(_safe_str(self.message_threshold.value)),
                    "duplicate_threshold": int(_safe_str(self.duplicate_threshold.value)),
                    "invite_threshold": int(_safe_str(self.invite_threshold.value)),
                    "multi_invite_immediate": int(_safe_str(self.multi_invite_immediate.value)),
                },
                updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
            )
        except Exception as e:
            return await _reply_ephemeral(interaction, f"❌ Failed to save thresholds: {e}")

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
                f"✅ Detection thresholds updated.\nPersistence: `{'DB-backed' if persisted else 'runtime only'}`",
                ephemeral=True,
            )
        except Exception:
            pass


class SpamActionSettingsModal(discord.ui.Modal, title="Spam Guard • Actions + Invite Codes"):
    def __init__(self, guild_id: int, channel_id: int, message_id: int, return_page: str, settings: Dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.return_page = return_page if return_page in SPAM_PANEL_PAGES else "enforcement"

        self.timeout_minutes = discord.ui.TextInput(
            label="Timeout Minutes",
            default=str(int(settings["timeout_minutes"])),
            required=True,
            max_length=4,
        )
        self.delete_history = discord.ui.TextInput(
            label="Delete Recent Messages",
            default=str(int(settings["delete_history"])),
            required=True,
            max_length=3,
        )
        self.cooldown_seconds = discord.ui.TextInput(
            label="Repeat Action Cooldown Seconds",
            default=str(int(settings["cooldown_seconds"])),
            required=True,
            max_length=4,
        )
        self.quarantine_role_id = discord.ui.TextInput(
            label="Quarantine Role ID (optional)",
            default=_safe_str(settings.get("quarantine_role_id")),
            required=False,
            max_length=25,
        )
        self.allowed_invite_codes = discord.ui.TextInput(
            label="Allowed Invite Codes (comma/newline separated)",
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
            _, persisted = await save_spam_settings(
                self.guild_id,
                {
                    "timeout_minutes": int(_safe_str(self.timeout_minutes.value)),
                    "delete_history": int(_safe_str(self.delete_history.value)),
                    "cooldown_seconds": int(_safe_str(self.cooldown_seconds.value)),
                    "quarantine_role_id": _safe_str(self.quarantine_role_id.value),
                    "allowed_invite_codes": _parse_csvish_codes(_safe_str(self.allowed_invite_codes.value)),
                },
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
                f"✅ Action settings updated.\nPersistence: `{'DB-backed' if persisted else 'runtime only'}`",
                ephemeral=True,
            )
        except Exception:
            pass


class SpamListsModal(discord.ui.Modal, title="Spam Guard • Channels + Exempt Users"):
    def __init__(self, guild_id: int, channel_id: int, message_id: int, return_page: str, settings: Dict[str, Any]):
        super().__init__(timeout=300)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.return_page = return_page if return_page in SPAM_PANEL_PAGES else "access"

        self.allowed_channel_ids = discord.ui.TextInput(
            label="Allowed Channel IDs",
            default=", ".join(list(settings.get("allowed_channel_ids") or [])),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.exempt_user_ids = discord.ui.TextInput(
            label="Exempt User IDs",
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
            _, persisted = await save_spam_settings(
                self.guild_id,
                {
                    "allowed_channel_ids": _parse_csvish_ids(_safe_str(self.allowed_channel_ids.value)),
                    "exempt_user_ids": _parse_csvish_ids(_safe_str(self.exempt_user_ids.value)),
                },
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
                f"✅ Channels and exempt users updated.\nPersistence: `{'DB-backed' if persisted else 'runtime only'}`",
                ephemeral=True,
            )
        except Exception:
            pass


# ============================================================
# Panel items
# ============================================================

class SpamSectionSelect(discord.ui.Select):
    def __init__(self, current_page: str):
        options = [
            discord.SelectOption(
                label="Overview",
                value="overview",
                description="Compact summary and quick controls",
                default=current_page == "overview",
            ),
            discord.SelectOption(
                label="Detection",
                value="detection",
                description="Thresholds and trigger logic",
                default=current_page == "detection",
            ),
            discord.SelectOption(
                label="Enforcement",
                value="enforcement",
                description="Response mode, cleanup, quarantine",
                default=current_page == "enforcement",
            ),
            discord.SelectOption(
                label="Access",
                value="access",
                description="Invite roles, exemptions, channels, users",
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
            discord.SelectOption(label="Log Only", value="log_only", description="Just log incidents", default=current_mode == "log_only"),
            discord.SelectOption(label="Delete Only", value="delete_only", description="Delete spam only", default=current_mode == "delete_only"),
            discord.SelectOption(label="Timeout", value="timeout", description="Delete spam and timeout", default=current_mode == "timeout"),
            discord.SelectOption(label="Quarantine", value="quarantine", description="Delete spam and add quarantine role", default=current_mode == "quarantine"),
            discord.SelectOption(label="Kick", value="kick", description="Delete spam and kick", default=current_mode == "kick"),
            discord.SelectOption(label="Ban", value="ban", description="Delete spam and ban", default=current_mode == "ban"),
        ]
        super().__init__(
            placeholder="Choose enforcement mode…",
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

        settings = await get_spam_settings(guild.id)
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

        settings = await get_spam_settings(guild.id)
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

        settings = await get_spam_settings(guild.id)
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


# ============================================================
# Commands / listeners
# ============================================================

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

            settings = await get_spam_settings(guild.id)
            embed = _build_panel_embed(guild, settings, page="overview")
            view = SpamGuardPanelView.build(page="overview", settings=settings)

            existing = await _find_existing_panel(channel)
            if existing is not None:
                try:
                    await existing.edit(embed=embed, view=view)
                    return await _reply_ephemeral(interaction, f"✅ Spam guard panel refreshed in {channel.mention}.")
                except Exception:
                    pass

            try:
                await channel.send(embed=embed, view=view)
                return await _reply_ephemeral(interaction, f"✅ Spam guard panel posted in {channel.mention}.")
            except Exception as e:
                return await _reply_ephemeral(interaction, f"❌ Failed to post panel: {e}")

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

            settings = await get_spam_settings(guild.id)
            embed = _build_panel_embed(guild, settings, page="overview")
            await interaction.response.send_message(embed=embed, ephemeral=True)

    _SPAM_GUARD_COMMANDS_REGISTERED = True


@bot.listen("on_message")
async def _spam_guard_on_message(message: discord.Message):
    await handle_incoming_spam_message(message)


@bot.listen("on_ready")
async def _register_spam_guard_views():
    global _SPAM_GUARD_VIEWS_REGISTERED

    if _SPAM_GUARD_VIEWS_REGISTERED:
        return

    try:
        for page in SPAM_PANEL_PAGES:
            bot.add_view(SpamGuardPanelView.build(page=page, settings=_default_settings(0)))
        _SPAM_GUARD_VIEWS_REGISTERED = True
        print("✅ spam_guard: compact multi-page persistent views registered")
    except Exception as e:
        print(f"⚠️ spam_guard: failed to register persistent views: {e}")


_register_spam_guard_commands()


__all__ = [
    "get_spam_settings",
    "save_spam_settings",
    "handle_incoming_spam_message",
]
