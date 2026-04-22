
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

import discord
from discord.ext import commands
from supabase import Client, create_client


# ============================================================
# Load Environment
# ============================================================

load_dotenv()


# ============================================================
# Helper Functions
# ============================================================

def _env_str(key: str, default: str = "") -> str:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip()


def _env_int(key: str, default: int = 0) -> int:
    value = _env_str(key)
    if not value:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    value = _env_str(key).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


def _log_info(message: str) -> None:
    try:
        print(f"ℹ️ globals: {message}")
    except Exception:
        pass


def _log_warn(message: str) -> None:
    try:
        print(f"⚠️ globals: {message}")
    except Exception:
        pass


def _safe_len(value: str) -> int:
    try:
        return len(value or "")
    except Exception:
        return 0


def _supabase_url_host() -> str:
    try:
        raw = _env_str("SUPABASE_URL")
        if not raw:
            return ""
        parsed = urlparse(raw)
        return str(parsed.netloc or parsed.path or "").strip()
    except Exception:
        return ""


# ============================================================
# Core Discord Configuration
# ============================================================

DISCORD_TOKEN: str = _env_str("DISCORD_TOKEN")
DISCORD_PUBLIC_KEY: str = _env_str("DISCORD_PUBLIC_KEY")

GUILD_ID: int = _env_int("GUILD_ID")


# ============================================================
# Supabase
# ============================================================

SUPABASE_URL: str = _env_str("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY: str = _env_str("SUPABASE_SERVICE_ROLE_KEY")

_SUPABASE_CLIENT: Optional[Client] = None
_SUPABASE_LOCK = threading.Lock()
_SUPABASE_THREAD_LOCAL = threading.local()
_SUPABASE_RESET_EPOCH = 0

_STARTUP_FLAGS: set[str] = set()
_STARTUP_FLAGS_LOCK = threading.Lock()

_SUPABASE_STATUS_LOCK = threading.Lock()
_SUPABASE_STATUS: Dict[str, object] = {
    "url_present": bool(SUPABASE_URL),
    "service_role_present": bool(SUPABASE_SERVICE_ROLE_KEY),
    "service_role_len": _safe_len(SUPABASE_SERVICE_ROLE_KEY),
    "host": _supabase_url_host(),
    "client_state": "uninitialized",
    "client_reason": "not_attempted",
    "last_log_fingerprint": "",
}


def claim_startup_flag(name: str) -> bool:
    clean = str(name or "").strip().lower()
    if not clean:
        return False
    with _STARTUP_FLAGS_LOCK:
        if clean in _STARTUP_FLAGS:
            return False
        _STARTUP_FLAGS.add(clean)
        return True


def has_startup_flag(name: str) -> bool:
    clean = str(name or "").strip().lower()
    if not clean:
        return False
    with _STARTUP_FLAGS_LOCK:
        return clean in _STARTUP_FLAGS


def clear_startup_flag(name: str) -> None:
    clean = str(name or "").strip().lower()
    if not clean:
        return
    with _STARTUP_FLAGS_LOCK:
        _STARTUP_FLAGS.discard(clean)


def _refresh_supabase_env_snapshot() -> None:
    with _SUPABASE_STATUS_LOCK:
        _SUPABASE_STATUS["url_present"] = bool(SUPABASE_URL)
        _SUPABASE_STATUS["service_role_present"] = bool(SUPABASE_SERVICE_ROLE_KEY)
        _SUPABASE_STATUS["service_role_len"] = _safe_len(SUPABASE_SERVICE_ROLE_KEY)
        _SUPABASE_STATUS["host"] = _supabase_url_host()


def _set_supabase_status(state: str, reason: str) -> None:
    _refresh_supabase_env_snapshot()
    with _SUPABASE_STATUS_LOCK:
        _SUPABASE_STATUS["client_state"] = str(state or "unknown")
        _SUPABASE_STATUS["client_reason"] = str(reason or "")


def _supabase_status_fingerprint() -> str:
    with _SUPABASE_STATUS_LOCK:
        state = str(_SUPABASE_STATUS.get("client_state") or "")
        reason = str(_SUPABASE_STATUS.get("client_reason") or "")
        host = str(_SUPABASE_STATUS.get("host") or "")
        url_present = bool(_SUPABASE_STATUS.get("url_present"))
        key_present = bool(_SUPABASE_STATUS.get("service_role_present"))
        return f"{state}|{reason}|{host}|{url_present}|{key_present}"


def _log_supabase_status_once_per_change(*, force: bool = False) -> None:
    fingerprint = _supabase_status_fingerprint()
    with _SUPABASE_STATUS_LOCK:
        last = str(_SUPABASE_STATUS.get("last_log_fingerprint") or "")
        if not force and fingerprint == last:
            return
        _SUPABASE_STATUS["last_log_fingerprint"] = fingerprint

        state = str(_SUPABASE_STATUS.get("client_state") or "unknown")
        reason = str(_SUPABASE_STATUS.get("client_reason") or "")
        host = str(_SUPABASE_STATUS.get("host") or "")
        url_present = bool(_SUPABASE_STATUS.get("url_present"))
        key_present = bool(_SUPABASE_STATUS.get("service_role_present"))
        key_len = int(_SUPABASE_STATUS.get("service_role_len") or 0)

    message = (
        "supabase status: "
        f"state={state} "
        f"host={host or '(missing)'} "
        f"url_present={url_present} "
        f"service_role_present={key_present} "
        f"service_role_len={key_len}"
    )
    if reason:
        message += f" reason={reason}"

    if state == "ready":
        _log_info(message)
    else:
        _log_warn(message)


def reset_supabase() -> None:
    global _SUPABASE_CLIENT, _SUPABASE_RESET_EPOCH

    with _SUPABASE_LOCK:
        _SUPABASE_CLIENT = None
        _SUPABASE_RESET_EPOCH += 1

    try:
        if hasattr(_SUPABASE_THREAD_LOCAL, "client"):
            delattr(_SUPABASE_THREAD_LOCAL, "client")
    except Exception:
        pass

    try:
        if hasattr(_SUPABASE_THREAD_LOCAL, "epoch"):
            delattr(_SUPABASE_THREAD_LOCAL, "epoch")
    except Exception:
        pass

    _set_supabase_status("reset", f"epoch={_SUPABASE_RESET_EPOCH}")
    _log_supabase_status_once_per_change(force=True)


def _create_supabase_client() -> Optional[Client]:
    _refresh_supabase_env_snapshot()

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        _set_supabase_status(
            "missing_env",
            f"url_present={bool(SUPABASE_URL)} service_role_present={bool(SUPABASE_SERVICE_ROLE_KEY)}",
        )
        return None

    try:
        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        _set_supabase_status("ready", "create_client")
        return client
    except Exception as e:
        _set_supabase_status(
            "create_failed",
            f"{type(e).__name__}: {repr(e)}",
        )
        return None


def get_supabase(*, force_new: bool = False) -> Optional[Client]:
    global _SUPABASE_CLIENT

    current_epoch = _SUPABASE_RESET_EPOCH

    if threading.current_thread() is threading.main_thread():
        with _SUPABASE_LOCK:
            if force_new:
                _SUPABASE_CLIENT = None

            if _SUPABASE_CLIENT is None:
                _SUPABASE_CLIENT = _create_supabase_client()

            client = _SUPABASE_CLIENT

        if client is None:
            _log_supabase_status_once_per_change()
        return client

    local_client = getattr(_SUPABASE_THREAD_LOCAL, "client", None)
    local_epoch = getattr(_SUPABASE_THREAD_LOCAL, "epoch", None)

    if force_new or local_client is None or local_epoch != current_epoch:
        local_client = _create_supabase_client()
        try:
            _SUPABASE_THREAD_LOCAL.client = local_client
            _SUPABASE_THREAD_LOCAL.epoch = current_epoch
        except Exception:
            pass

    if local_client is None:
        _log_supabase_status_once_per_change()

    return local_client


def supabase_diagnostics(*, ensure_client: bool = False) -> dict:
    if ensure_client:
        try:
            get_supabase()
        except Exception:
            pass

    _refresh_supabase_env_snapshot()
    with _SUPABASE_STATUS_LOCK:
        payload = dict(_SUPABASE_STATUS)

    client_available = False
    try:
        client_available = get_supabase() is not None if ensure_client else (
            _SUPABASE_CLIENT is not None or getattr(_SUPABASE_THREAD_LOCAL, "client", None) is not None
        )
    except Exception:
        client_available = False

    return {
        "url_present": bool(payload.get("url_present")),
        "service_role_present": bool(payload.get("service_role_present")),
        "service_role_len": int(payload.get("service_role_len") or 0),
        "host": str(payload.get("host") or ""),
        "client_state": str(payload.get("client_state") or ""),
        "client_reason": str(payload.get("client_reason") or ""),
        "client_available": client_available,
        "reset_epoch": _SUPABASE_RESET_EPOCH,
    }


# ============================================================
# Verification Website / Bot API
# ============================================================

VERIFY_SITE_URL: str = _env_str("VERIFY_SITE_URL")
BOT_ACTIONS_API_URL: str = _env_str("BOT_ACTIONS_API_URL")

BOT_API_SHARED_SECRET: str = _env_str("BOT_API_SHARED_SECRET")
BOT_API_BIND_HOST: str = _env_str("BOT_API_BIND_HOST", "127.0.0.1")
BOT_API_PORT: int = _env_int("BOT_API_PORT", 8081)
BOT_API_REQUIRE_AUTH: bool = _env_bool("BOT_API_REQUIRE_AUTH", True)
BOT_API_ALLOW_INSECURE: bool = _env_bool("BOT_API_ALLOW_INSECURE", False)


# ============================================================
# Verification Channels
# ============================================================

VERIFY_CHANNEL_ID: int = _env_int("VERIFY_CHANNEL_ID")

VC_VERIFY_CHANNEL_ID: int = _env_int("VC_VERIFY_CHANNEL_ID")
VC_VERIFY_QUEUE_CHANNEL_ID: int = _env_int("VC_VERIFY_QUEUE_CHANNEL_ID")

# Older compatibility name some files still check
VC_VERIFY_VC_ID: int = VC_VERIFY_CHANNEL_ID

# Normalize so older code still works
if VERIFY_CHANNEL_ID == 0:
    VERIFY_CHANNEL_ID = VC_VERIFY_CHANNEL_ID


# ============================================================
# Ticket System
# ============================================================

TICKET_CATEGORY_ID: int = _env_int("TICKET_CATEGORY_ID")
TICKET_PREFIX: str = _env_str("TICKET_PREFIX", "ticket")

AUTO_DELETE_TICKET_SECONDS: int = _env_int("AUTO_DELETE_TICKET_SECONDS")

TRANSCRIPTS_CHANNEL_ID: int = _env_int("TRANSCRIPTS_CHANNEL_ID")
TRANSCRIPT_PANEL_NAME: str = _env_str("TRANSCRIPT_PANEL_NAME", "Support")

SINGLE_PANEL_MODE: bool = _env_bool("SINGLE_PANEL_MODE", True)

# Optional join log channel used by events.py
JOIN_LOG_CHANNEL_ID: int = _env_int("JOIN_LOG_CHANNEL_ID", 0)


# ============================================================
# Verification Tokens / Timers
# ============================================================

TOKEN_TTL_MINUTES: int = _env_int("TOKEN_TTL_MINUTES", 240)
VC_REQUEST_TTL_MINUTES: int = _env_int("VC_REQUEST_TTL_MINUTES", 240)

# Missing constant that commands.py expects
VERIFY_KICK_HOURS: int = _env_int("VERIFY_KICK_HOURS", 24)

# VC anti-spam cooldown used by verify_ui.py
VC_REQUEST_COOLDOWN_SECONDS: int = _env_int("VC_REQUEST_COOLDOWN_SECONDS", 60)


# ============================================================
# Roles
# ============================================================

UNVERIFIED_ROLE_ID: int = _env_int("UNVERIFIED_ROLE_ID")
VERIFIED_ROLE_ID: int = _env_int("VERIFIED_ROLE_ID")

RESIDENT_ROLE_ID: int = _env_int("RESIDENT_ROLE_ID")
STONER_ROLE_ID: int = _env_int("STONER_ROLE_ID")
DRUNKEN_ROLE_ID: int = _env_int("DRUNKEN_ROLE_ID")

STAFF_ROLE_ID: int = _env_int("STAFF_ROLE_ID")

# Older compatibility name some files may still reference
VC_STAFF_ROLE_ID: int = _env_int("VC_STAFF_ROLE_ID", STAFF_ROLE_ID)


# ============================================================
# Optional Role Prompt
# ============================================================

ENABLE_OPTIONAL_ROLE_PROMPT: bool = _env_bool("ENABLE_OPTIONAL_ROLE_PROMPT", True)

OPTIONAL_ROLE_AUTO_CLOSE_SECONDS: int = _env_int(
    "OPTIONAL_ROLE_AUTO_CLOSE_SECONDS", 0
)


# ============================================================
# Moderation
# ============================================================

MOD_TIMEOUT_MINUTES: int = _env_int("MOD_TIMEOUT_MINUTES", 60)

MOD_ACTION_AUDIT_LOOKBACK_SECONDS: int = _env_int(
    "MOD_ACTION_AUDIT_LOOKBACK_SECONDS", 20
)

MODLOG_CHANNEL_ID: int = _env_int("MODLOG_CHANNEL_ID")
RAIDLOG_CHANNEL_ID: int = _env_int("RAIDLOG_CHANNEL_ID")


# ============================================================
# Raid Detection
# ============================================================

RAID_WINDOW_SECONDS: int = _env_int("RAID_WINDOW_SECONDS", 60)

RAID_JOIN_THRESHOLD: int = _env_int("RAID_JOIN_THRESHOLD", 6)

RAID_LOCK_MINUTES: int = _env_int("RAID_LOCK_MINUTES", 15)

RAID_MASS_ROLE_STRIP: bool = _env_bool("RAID_MASS_ROLE_STRIP", True)


# ============================================================
# Alt Detection
# ============================================================

ALT_NEW_ACCOUNT_DAYS: int = _env_int("ALT_NEW_ACCOUNT_DAYS", 7)

ALT_CLUSTER_WINDOW_MINUTES: int = _env_int("ALT_CLUSTER_WINDOW_MINUTES", 30)

ALT_CLUSTER_MIN_GROUP: int = _env_int("ALT_CLUSTER_MIN_GROUP", 3)


# ============================================================
# Force Verify Logging
# ============================================================

FORCE_VERIFY_LOG_CHANNEL_ID: int = _env_int("FORCE_VERIFY_LOG_CHANNEL_ID")


# ============================================================
# Visual / Embed Defaults
# ============================================================

VERIFY_EMBED_COLOR = discord.Color.green()
VERIFY_EMBED_THUMBNAIL_URL: str = _env_str("VERIFY_EMBED_THUMBNAIL_URL", "")

ALLOW_USER_VERIFYLINK: bool = _env_bool("ALLOW_USER_VERIFYLINK", False)


# ============================================================
# Tables / Optional DB Names
# ============================================================

VERIFY_TOKEN_TABLE: str = _env_str("VERIFY_TOKEN_TABLE", "verification_tokens")
TOKEN_TABLE: str = _env_str("TOKEN_TABLE", VERIFY_TOKEN_TABLE)


# ============================================================
# Runtime Containers
# ============================================================

RUNTIME_STATS: Dict[str, int] = {
    "member_joins": 0,
    "member_leaves_detected": 0,
    "member_kicks_detected": 0,
    "member_bans_detected": 0,
    "role_changes": 0,
    "nickname_changes": 0,
    "tickets_closed": 0,
    "raw_link_clicks": 0,
    "open_link_clicks": 0,
    "vc_requests": 0,
}

JOIN_TIMES: Dict[int, List[datetime]] = {}
ALT_JOIN_BUCKETS: Dict[int, Dict[str, List[int]]] = {}
ALT_JOIN_BUCKET_TS: Dict[int, Dict[str, datetime]] = {}
RAID_RECENT_JOINERS: Dict[int, Dict[int, datetime]] = {}

VC_REQUESTS: Dict[str, Dict[str, object]] = {}
VC_REQUEST_COOLDOWNS: Dict[int, datetime] = {}

AUTO_TICKET_CATEGORY_IDS: set[int] = set()


# ============================================================
# Time Helpers
# ============================================================

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fmt_utc(dt: Optional[datetime] = None) -> str:
    try:
        target = dt or now_utc()
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return target.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "(time unavailable)"


def _parse_iso_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        try:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        except Exception:
            return value

    try:
        s = str(value).strip()
        if not s:
            return None
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


# ============================================================
# Utility Helpers
# ============================================================

def get_guild(bot_client: discord.Client) -> Optional[discord.Guild]:
    if not GUILD_ID:
        return None
    return bot_client.get_guild(GUILD_ID)


def get_role(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    if not role_id:
        return None
    return guild.get_role(role_id)


def get_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    if not channel_id:
        return None
    return guild.get_channel(channel_id)


# ============================================================
# Role Checks
# ============================================================

def is_verified(member: discord.Member) -> bool:
    try:
        return any(int(r.id) == int(VERIFIED_ROLE_ID) for r in member.roles)
    except Exception:
        return False


def is_unverified(member: discord.Member) -> bool:
    try:
        return any(int(r.id) == int(UNVERIFIED_ROLE_ID) for r in member.roles)
    except Exception:
        return False


def is_staff(member: discord.Member) -> bool:
    try:
        if member.guild_permissions.administrator:
            return True
    except Exception:
        pass

    try:
        return any(int(r.id) == int(STAFF_ROLE_ID) for r in member.roles)
    except Exception:
        return False


# ============================================================
# Optional Roles
# ============================================================

OPTIONAL_ROLE_IDS = [
    RESIDENT_ROLE_ID,
    STONER_ROLE_ID,
    DRUNKEN_ROLE_ID,
]


def get_optional_roles(guild: discord.Guild) -> list[discord.Role]:
    roles: list[discord.Role] = []
    for role_id in OPTIONAL_ROLE_IDS:
        if role_id:
            role = guild.get_role(role_id)
            if role:
                roles.append(role)
    return roles


# ============================================================
# Discord Intents / Bot
# ============================================================

def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    intents.messages = True
    intents.message_content = True
    intents.voice_states = True
    return intents


bot = commands.Bot(
    command_prefix="!",
    intents=build_intents(),
    help_command=None,
)


def config_summary() -> dict:
    return {
        "guild": GUILD_ID,
        "verify_channel": VERIFY_CHANNEL_ID,
        "vc_verify_channel": VC_VERIFY_CHANNEL_ID,
        "vc_verify_queue_channel": VC_VERIFY_QUEUE_CHANNEL_ID,
        "ticket_category": TICKET_CATEGORY_ID,
        "unverified_role": UNVERIFIED_ROLE_ID,
        "verified_role": VERIFIED_ROLE_ID,
        "staff_role": STAFF_ROLE_ID,
        "transcripts_channel": TRANSCRIPTS_CHANNEL_ID,
        "verify_kick_hours": VERIFY_KICK_HOURS,
        "bot_api_bind_host": BOT_API_BIND_HOST,
        "bot_api_port": BOT_API_PORT,
        "bot_api_require_auth": BOT_API_REQUIRE_AUTH,
        "supabase_env_present": bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY),
    }


@bot.listen("on_ready")
async def _globals_log_startup_once() -> None:
    if not claim_startup_flag("globals-on-ready-summary"):
        return

    try:
        summary = config_summary()
        _log_info(f"startup summary: {summary}")
    except Exception as e:
        _log_warn(f"failed to build startup summary: {repr(e)}")

    try:
        get_supabase()
        _log_supabase_status_once_per_change(force=True)
    except Exception as e:
        _log_warn(f"failed to initialize supabase diagnostics: {repr(e)}")
