from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any, List, Set

import discord

from .globals import *

try:
    from .guild_config import get_guild_config
except Exception:
    async def get_guild_config(*_args, **_kwargs):
        return {}

_MODLOG_RECENT_EVENT_KEYS: Dict[Tuple[int, str], float] = {}
_MODLOG_DEDUPE_LOCK = asyncio.Lock()

try:
    from .raidguard import build_member_risk_profile
except Exception:
    def build_member_risk_profile(member: discord.Member) -> Dict[str, Any]:
        return {}


try:
    from .identity_proof_service import get_identity_truth_context
except Exception:
    def get_identity_truth_context(*, guild_id: Any, user_id: Any) -> Dict[str, Any]:
        return {}


# ==========================================================
# Small local helpers
# ==========================================================

def _now_utc() -> datetime:
    try:
        return now_utc()
    except Exception:
        return datetime.now(timezone.utc)


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    try:
        if not role_id:
            return False
        return any(int(r.id) == int(role_id) for r in (member.roles or []))
    except Exception:
        return False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_list(value: Any) -> List[Any]:
    try:
        return list(value) if isinstance(value, list) else []
    except Exception:
        return []


def _safe_string_list(value: Any, max_items: int = 20) -> List[str]:
    out: List[str] = []
    try:
        if isinstance(value, list):
            for item in value:
                text = str(item or "").strip()
                if text and text not in out:
                    out.append(text)
        elif value is not None:
            text = str(value).strip()
            if text:
                out.append(text)
    except Exception:
        pass
    return out[:max_items]


def _dedupe_list(values: List[str], max_items: int = 20) -> List[str]:
    out: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out[:max_items]


def _truncate(text: Any, max_len: int = 1024) -> str:
    try:
        s = str(text or "")
        if len(s) <= max_len:
            return s
        return s[: max(0, max_len - 1)] + "…"
    except Exception:
        return ""


def _join_nonempty(parts: List[str], sep: str = " • ") -> str:
    clean = [str(p).strip() for p in parts if str(p or "").strip()]
    return sep.join(clean)


def _chunk_lines(lines: List[str], max_len: int = 1000) -> str:
    text = "\n".join([line for line in lines if str(line or "").strip()])
    return _truncate(text, max_len)


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "httpcore",
        "httpx",
        "connection terminated",
        "broken pipe",
        "readerror",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 2.5)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db_write_with_retry(op_name: str, executor, max_attempts: int = 5) -> bool:
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            executor()
            return True
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                print(
                    f"⚠️ {op_name}: transient DB error on attempt "
                    f"{attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue

            print(f"⚠️ {op_name} failed:", repr(e))
            return False

    if last_error is not None:
        print(f"⚠️ {op_name} failed after retries:", repr(last_error))
    return False


def _safe_dt_utc(value: Optional[datetime]) -> Optional[datetime]:
    try:
        if not value:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def _json_safe(value: Any):
    try:
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, datetime):
            dt = _safe_dt_utc(value)
            return dt.isoformat() if dt else None
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)
    except Exception:
        try:
            return str(value)
        except Exception:
            return None


def _discord_ts(dt: Optional[datetime]) -> str:
    try:
        dtu = _safe_dt_utc(dt)
        if not dtu:
            return "unknown"
        return f"<t:{int(dtu.timestamp())}:F>"
    except Exception:
        return "unknown"


def _member_display(member: Optional[discord.abc.User]) -> str:
    try:
        if not member:
            return "Unknown"
        display_name = (
            getattr(member, "display_name", None)
            or getattr(member, "global_name", None)
            or getattr(member, "name", None)
            or str(member)
        )
        username = getattr(member, "name", None)
        mid = getattr(member, "id", None)

        if username and display_name and username != display_name:
            base = f"{display_name} / {username}"
        else:
            base = str(display_name or username or "Unknown")

        if mid:
            return f"{base} ({mid})"
        return base
    except Exception:
        return "Unknown"


def _duration_label_from_minutes(minutes: int) -> str:
    m = max(1, int(minutes))
    if m % 1440 == 0:
        days = m // 1440
        return f"{days} day(s)"
    if m % 60 == 0:
        hours = m // 60
        return f"{hours} hour(s)"
    return f"{m} minute(s)"


def _has_default_avatar(user: Optional[discord.abc.User]) -> bool:
    try:
        if user is None:
            return False
        return getattr(user, "avatar", None) is None
    except Exception:
        return False


def _username_for_checks(user: Optional[discord.abc.User]) -> str:
    try:
        if user is None:
            return ""
        return str(
            getattr(user, "name", None)
            or getattr(user, "display_name", None)
            or getattr(user, "global_name", None)
            or ""
        ).strip()
    except Exception:
        return ""


def _digit_ratio(text: str) -> float:
    try:
        raw = str(text or "")
        if not raw:
            return 0.0
        count = sum(1 for ch in raw if ch.isdigit())
        return float(count) / float(max(1, len(raw)))
    except Exception:
        return 0.0


def _max_repeat_run(text: str) -> int:
    try:
        raw = str(text or "")
        if not raw:
            return 0
        best = 1
        cur = 1
        prev = raw[0]
        for ch in raw[1:]:
            if ch.lower() == prev.lower():
                cur += 1
                if cur > best:
                    best = cur
            else:
                cur = 1
                prev = ch
        return best
    except Exception:
        return 0


def _max_digit_run(text: str) -> int:
    try:
        raw = str(text or "")
        best = 0
        cur = 0
        for ch in raw:
            if ch.isdigit():
                cur += 1
                if cur > best:
                    best = cur
            else:
                cur = 0
        return best
    except Exception:
        return 0


def _score_to_level(score: int) -> str:
    s = max(0, int(score or 0))
    if s >= 70:
        return "high"
    if s >= 40:
        return "medium"
    return "low"


def _level_rank(level: str) -> int:
    normalized = str(level or "").strip().lower()
    if normalized == "high":
        return 3
    if normalized == "medium":
        return 2
    if normalized == "low":
        return 1
    return 0


def _humanize_seconds(total_seconds: float) -> str:
    try:
        seconds = max(0, int(total_seconds or 0))

        if seconds < 60:
            return "<1 minute"

        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} minute(s)"

        hours = minutes // 60
        rem_minutes = minutes % 60
        if hours < 24:
            if rem_minutes > 0 and hours < 6:
                return f"{hours}h {rem_minutes}m"
            return f"{hours} hour(s)"

        days = hours // 24
        rem_hours = hours % 24
        if days < 30:
            if rem_hours > 0 and days < 7:
                return f"{days}d {rem_hours}h"
            return f"{days} day(s)"

        months = days // 30
        if months < 12:
            return f"{months} month(s)"

        years = days // 365
        return f"{years} year(s)"
    except Exception:
        return "unknown"


def _account_age_human(member_or_user: Optional[discord.abc.User]) -> str:
    try:
        created_at = _safe_dt_utc(getattr(member_or_user, "created_at", None))
        if not created_at:
            return "unknown"
        return _humanize_seconds((_now_utc() - created_at).total_seconds())
    except Exception:
        return "unknown"


def _join_after_creation_delta(member: Optional[discord.abc.User]) -> Tuple[Optional[int], str]:
    try:
        if not isinstance(member, discord.Member):
            return (None, "")

        created_at = _safe_dt_utc(getattr(member, "created_at", None))
        joined_at = _safe_dt_utc(getattr(member, "joined_at", None))
        if not created_at or not joined_at:
            return (None, "")

        delta_seconds = max(0, int((joined_at - created_at).total_seconds()))
        return (delta_seconds, _humanize_seconds(delta_seconds))
    except Exception:
        return (None, "")


def _bot_member_for_guild(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        me = getattr(guild, "me", None)
        if isinstance(me, discord.Member):
            return me
    except Exception:
        pass

    try:
        if getattr(bot, "user", None):
            return guild.get_member(int(bot.user.id))
    except Exception:
        pass

    return None


def _can_act_on_member(actor: discord.Member, target: discord.Member) -> Tuple[bool, str]:
    try:
        if actor.id == target.id:
            return (False, "You cannot moderate yourself.")

        if target.id == actor.guild.owner_id:
            return (False, "You cannot moderate the server owner.")

        if actor.guild.owner_id == actor.id:
            return (True, "")

        if target.top_role >= actor.top_role:
            return (False, "Your role hierarchy is too low for that action.")

        return (True, "")
    except Exception:
        return (False, "Failed to verify permission hierarchy.")


def _bot_can_act_on_member(guild: discord.Guild, target: discord.Member) -> Tuple[bool, str]:
    try:
        me = _bot_member_for_guild(guild)
        if not me:
            return (False, "Bot member could not be resolved.")
        if guild.owner_id == me.id:
            return (True, "")
        if target.id == guild.owner_id:
            return (False, "Bot cannot moderate the server owner.")
        if target.top_role >= me.top_role:
            return (False, "Bot role hierarchy is too low for that action.")
        return (True, "")
    except Exception:
        return (False, "Failed to verify bot hierarchy.")


def _moderator_has_permission(member: discord.Member, perm_name: str) -> bool:
    try:
        return bool(getattr(member.guild_permissions, perm_name, False))
    except Exception:
        return False


def _parse_timeout_minutes(extra: str) -> int:
    try:
        m = re.search(r"(?:^|:)m=(\d+)", str(extra or ""))
        if not m:
            return int(globals().get("MOD_TIMEOUT_MINUTES", 10) or 10)
        minutes = int(m.group(1))
        return max(1, min(minutes, 28 * 24 * 60))
    except Exception:
        return int(globals().get("MOD_TIMEOUT_MINUTES", 10) or 10)


def _quick_mod_default_reason(action: str, moderator: Optional[discord.Member]) -> str:
    actor = _member_display(moderator)
    a = str(action or "").strip().lower()
    if a == "ban":
        return f"Quick mod ban — by {actor}"
    if a == "kick":
        return f"Quick mod kick — by {actor}"
    if a == "timeout":
        return f"Quick mod timeout — by {actor}"
    return f"Quick moderation action — by {actor}"


def _interaction_has_manage_messages(interaction: discord.Interaction) -> bool:
    try:
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        return _moderator_has_permission(user, "manage_messages")
    except Exception:
        return False


# ==========================================================
# Compatibility helpers expected elsewhere
# ==========================================================

def _account_age_days(member_or_user: Optional[discord.abc.User]) -> int:
    try:
        created_at = _safe_dt_utc(getattr(member_or_user, "created_at", None))
        if not created_at:
            return 0
        delta = _now_utc() - created_at
        return max(0, int(delta.total_seconds() // 86400))
    except Exception:
        return 0


def _age_bucket(value: Any) -> str:
    try:
        days = value if isinstance(value, int) else _account_age_days(value)
        days = max(0, int(days or 0))
        if days < 1:
            return "<1d"
        if days < 3:
            return "1-2d"
        if days < 7:
            return "3-6d"
        if days < 30:
            return "7-29d"
        if days < 90:
            return "30-89d"
        if days < 180:
            return "90-179d"
        if days < 365:
            return "180-364d"
        return "365d+"
    except Exception:
        return "unknown"


def _behavior_fingerprint(member: Optional[discord.abc.User]) -> str:
    try:
        if not member:
            return ""
        display = (
            getattr(member, "global_name", None)
            or getattr(member, "display_name", None)
            or getattr(member, "name", None)
            or "unknown"
        )
        created_at = _safe_dt_utc(getattr(member, "created_at", None))
        created_day = int(created_at.timestamp() // 86400) if created_at else 0
        avatar_flag = "1" if _has_default_avatar(member) else "0"
        base = f"{str(display).strip().lower()}|{created_day}|{avatar_flag}"
        return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:12]
    except Exception:
        return ""


async def _maybe_trigger_raid(*args, **kwargs) -> bool:
    return False


async def _mass_role_strip_if_needed(*args, **kwargs) -> bool:
    return False


async def _post_raidlog(guild: discord.Guild, embed: discord.Embed, view: Optional[discord.ui.View] = None):
    await _post_modlog(guild, embed, view=view)


# ==========================================================
# Multi-guild modlog channel resolution
# ==========================================================

def _env_guild_override_int(base_name: str, guild_id: int, default: int = 0) -> int:
    """
    Supports:
      - MODLOG_CHANNEL_ID_<guild_id>
      - MODLOG_CHANNEL_ID__<guild_id>
      - GUILD_<guild_id>_MODLOG_CHANNEL_ID
    """
    try:
        gid = int(guild_id or 0)
    except Exception:
        gid = 0

    if gid <= 0:
        return default

    candidates = (
        f"{base_name}_{gid}",
        f"{base_name}__{gid}",
        f"GUILD_{gid}_{base_name}",
    )

    for key in candidates:
        raw = os.getenv(key, "")
        val = _safe_int(raw, 0)
        if val > 0:
            return val

    return default


def _same_guild_text_channel(candidate: Any, guild: discord.Guild) -> bool:
    try:
        return (
            isinstance(candidate, discord.TextChannel)
            and getattr(candidate, "guild", None) is not None
            and int(candidate.guild.id) == int(guild.id)
        )
    except Exception:
        return False


def _find_same_guild_modlog_channel_by_name(guild: discord.Guild) -> Optional[discord.TextChannel]:
    exact_names = {
        "mod-log",
        "modlog",
        "mod_log",
        "moderation-log",
        "staff-log",
        "modlogs",
    }

    contains_terms = (
        "mod-log",
        "modlog",
        "moderation",
        "staff-log",
    )

    try:
        for ch in guild.text_channels:
            name = _safe_str(getattr(ch, "name", "")).strip().lower()
            if name in exact_names:
                return ch
    except Exception:
        pass

    try:
        for ch in guild.text_channels:
            name = _safe_str(getattr(ch, "name", "")).strip().lower()
            if any(term in name for term in contains_terms):
                return ch
    except Exception:
        pass

    return None


def _candidate_modlog_channel_ids(guild: discord.Guild) -> List[int]:
    out: List[int] = []
    seen: Set[int] = set()

    def _push(value: Any) -> None:
        cid = _safe_int(value, 0)
        if cid > 0 and cid not in seen:
            seen.add(cid)
            out.append(cid)

    try:
        _push(_env_guild_override_int("MODLOG_CHANNEL_ID", int(guild.id), 0))
    except Exception:
        pass

    allow_global_modlog = True
    try:
        from .guild_config import public_config_isolation_enabled

        if public_config_isolation_enabled():
            home_gid = _safe_int(globals().get("GUILD_ID", 0), 0)
            allow_global_modlog = bool(home_gid > 0 and int(guild.id) == int(home_gid))
    except Exception:
        allow_global_modlog = False

    if allow_global_modlog:
        try:
            _push(globals().get("MODLOG_CHANNEL_ID", 0))
        except Exception:
            pass

    return out


def _cfg_id_value(cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            raw = getattr(cfg, key, None)
            val = _safe_int(raw, 0)
            if val > 0:
                return val
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                raw = cfg.get(key)
                val = _safe_int(raw, 0)
                if val > 0:
                    return val
        except Exception:
            pass
        for bucket in ("settings", "config", "metadata", "meta"):
            try:
                nested = getattr(cfg, bucket, None)
                if isinstance(nested, dict):
                    val = _safe_int(nested.get(key), 0)
                    if val > 0:
                        return val
            except Exception:
                pass
    return 0


async def _get_modlog_channel_async(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Resolve modlog channel using saved per-guild config first.

    Public multi-server installs must not depend on env globals or channel-name
    fallback when a guild has explicitly saved modlog_channel_id.
    """

    try:
        from .guild_config import get_guild_config

        cfg = await get_guild_config(int(guild.id), refresh=False)
        cid = _cfg_id_value(cfg, "modlog_channel_id", "mod_log_channel_id", "logs_channel_id")
        if cid > 0:
            try:
                ch = guild.get_channel(int(cid))
                if _same_guild_text_channel(ch, guild):
                    return ch
            except Exception:
                pass

            try:
                cached = bot.get_channel(int(cid))
                if _same_guild_text_channel(cached, guild):
                    return cached
            except Exception:
                pass

            try:
                fetched = await guild.fetch_channel(int(cid))
                if _same_guild_text_channel(fetched, guild):
                    return fetched
            except Exception as exc:
                print(
                    f"⚠️ configured modlog channel unavailable "
                    f"guild={getattr(guild, 'id', 'unknown')} channel={cid}: {type(exc).__name__}: {exc}"
                )
    except Exception as exc:
        print(
            f"⚠️ modlog config lookup failed "
            f"guild={getattr(guild, 'id', 'unknown')}: {type(exc).__name__}: {exc}"
        )

    return _get_modlog_channel(guild)


def _get_modlog_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    candidate_ids = _candidate_modlog_channel_ids(guild)

    for cid in candidate_ids:
        try:
            ch = guild.get_channel(int(cid))
            if _same_guild_text_channel(ch, guild):
                return ch
        except Exception:
            pass

        try:
            bot_cached = bot.get_channel(int(cid))
            if isinstance(bot_cached, discord.TextChannel):
                if int(bot_cached.guild.id) != int(guild.id):
                    print(
                        f"⚠️ modlog channel id={cid} belongs to a different guild "
                        f"expected_guild={guild.id} actual_guild={bot_cached.guild.id}"
                    )
                    continue
                return bot_cached
        except Exception:
            pass

    fallback = _find_same_guild_modlog_channel_by_name(guild)
    if isinstance(fallback, discord.TextChannel):
        return fallback

    print(
        f"⚠️ Modlog channel not found for guild {getattr(guild, 'id', 'unknown')} "
        f"checked_ids={candidate_ids}"
    )
    return None


async def _post_modlog(
    guild: discord.Guild,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
    *,
    event_key: Optional[str] = None,
    dedupe_window_seconds: float = 8.0,
) -> Optional[discord.Message]:
    """Send one moderation event and suppress semantic or identical repeats."""

    normalized_key = str(event_key or "").strip()
    effective_window = max(1.0, float(dedupe_window_seconds))

    if not normalized_key:
        try:
            payload = dict(embed.to_dict())
            payload.pop("timestamp", None)
            digest = hashlib.sha256(
                repr(payload).encode("utf-8", errors="replace")
            ).hexdigest()[:24]
            normalized_key = f"embed:{digest}"
            effective_window = min(effective_window, 3.0)
        except Exception:
            normalized_key = ""

    reservation: Optional[Tuple[int, str]] = None
    if normalized_key:
        reservation = (int(guild.id), normalized_key)
        now_mono = time.monotonic()
        async with _MODLOG_DEDUPE_LOCK:
            cutoff = now_mono - max(30.0, effective_window * 2)
            for key, seen_at in list(_MODLOG_RECENT_EVENT_KEYS.items()):
                if seen_at < cutoff:
                    _MODLOG_RECENT_EVENT_KEYS.pop(key, None)
            previous = _MODLOG_RECENT_EVENT_KEYS.get(reservation)
            if previous is not None and (now_mono - previous) < effective_window:
                return None
            _MODLOG_RECENT_EVENT_KEYS[reservation] = now_mono

    channel = await _get_modlog_channel_async(guild)
    if not channel:
        if reservation:
            async with _MODLOG_DEDUPE_LOCK:
                _MODLOG_RECENT_EVENT_KEYS.pop(reservation, None)
        print(
            f"⚠️ Modlog channel not found for guild "
            f"{getattr(guild, 'id', 'unknown')}"
        )
        return None

    try:
        return await channel.send(
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except TypeError:
        try:
            return await channel.send(embed=embed, view=view)
        except Exception as exc:
            if reservation:
                async with _MODLOG_DEDUPE_LOCK:
                    _MODLOG_RECENT_EVENT_KEYS.pop(reservation, None)
            print(
                f"⚠️ Failed sending modlog message "
                f"guild={getattr(guild, 'id', 'unknown')} "
                f"channel={getattr(channel, 'id', 'unknown')} "
                f"error={repr(exc)}"
            )
    except Exception as exc:
        if reservation:
            async with _MODLOG_DEDUPE_LOCK:
                _MODLOG_RECENT_EVENT_KEYS.pop(reservation, None)
        print(
            f"⚠️ Failed sending modlog message "
            f"guild={getattr(guild, 'id', 'unknown')} "
            f"channel={getattr(channel, 'id', 'unknown')} "
            f"error={repr(exc)}"
        )
    return None


# ==========================================================
# Quick mod view
# ==========================================================

class QuickModView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=60 * 60 * 24)
        self.target_user_id = int(target_user_id)

    async def _resolve_target(
        self,
        interaction: discord.Interaction,
    ) -> Tuple[Optional[discord.Member], Optional[str]]:
        try:
            if interaction.guild is None:
                return None, "Guild context missing."

            target = interaction.guild.get_member(self.target_user_id)
            if target is None:
                try:
                    target = await interaction.guild.fetch_member(self.target_user_id)
                except Exception:
                    target = None

            if target is None:
                return None, "Target member is no longer in this server. Use Ban to ban by user ID."

            return target, None
        except Exception:
            return None, "Failed to resolve target member."

    def _ban_object(self) -> discord.Object:
        return discord.Object(id=int(self.target_user_id))

    def _target_mention(self) -> str:
        return f"<@{int(self.target_user_id)}>"

    def _bot_can_ban_by_id(self, guild: discord.Guild) -> Tuple[bool, str]:
        try:
            me = _bot_member_for_guild(guild)
            if not isinstance(me, discord.Member):
                return (False, "Bot member could not be resolved.")
            if not _moderator_has_permission(me, "ban_members") and not _moderator_has_permission(me, "administrator"):
                return (False, "Bot needs **Ban Members** to ban a user who already left.")
            if int(self.target_user_id) == int(guild.owner_id or 0):
                return (False, "Bot cannot ban the server owner.")
            return (True, "")
        except Exception:
            return (False, "Failed to verify bot ban permission.")

    async def _ensure_mod(
        self,
        interaction: discord.Interaction,
        *,
        perm_name: str,
    ) -> Tuple[Optional[discord.Member], Optional[str]]:
        try:
            if not isinstance(interaction.user, discord.Member):
                return None, "Moderator member context missing."

            moderator = interaction.user
            if not _moderator_has_permission(moderator, perm_name) and not _moderator_has_permission(moderator, "administrator"):
                return None, "You do not have permission to use this quick action."

            return moderator, None
        except Exception:
            return None, "Failed moderator permission check."

    async def _deny(self, interaction: discord.Interaction, text: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:
            pass

    async def _ok(self, interaction: discord.Interaction, text: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except Exception:
            pass

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, emoji="🔨")
    async def ban_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        moderator, error = await self._ensure_mod(interaction, perm_name="ban_members")
        if error or moderator is None:
            await self._deny(interaction, error or "Permission denied.")
            return
        if interaction.guild is None:
            await self._deny(interaction, "Guild context missing.")
            return
        if int(self.target_user_id) == int(getattr(moderator, "id", 0) or 0):
            await self._deny(interaction, "You cannot ban yourself.")
            return

        target, _resolve_error = await self._resolve_target(interaction)
        reason = _quick_mod_default_reason("ban", moderator)

        if isinstance(target, discord.Member):
            ok, reason_text = _can_act_on_member(moderator, target)
            if not ok:
                await self._deny(interaction, reason_text)
                return

            ok, reason_text = _bot_can_act_on_member(interaction.guild, target)  # type: ignore[arg-type]
            if not ok:
                await self._deny(interaction, reason_text)
                return

            try:
                await target.ban(reason=reason, delete_message_days=0)
                await self._ok(interaction, f"🔨 Banned {target.mention}")
            except Exception as e:
                await self._deny(interaction, f"Ban failed: {e}")
            return

        ok, reason_text = self._bot_can_ban_by_id(interaction.guild)
        if not ok:
            await self._deny(interaction, reason_text)
            return

        try:
            await interaction.guild.ban(self._ban_object(), reason=reason, delete_message_days=0)
            await self._ok(interaction, f"🔨 Banned {self._target_mention()} by user ID. They had already left or been kicked.")
        except discord.NotFound:
            await self._deny(interaction, "Ban failed: Discord could not find that user ID.")
        except discord.Forbidden:
            await self._deny(interaction, "Ban failed: I need **Ban Members**, and my role/permissions must allow this action.")
        except Exception as e:
            await self._deny(interaction, f"Ban failed: {e}")

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.secondary, emoji="👢")
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        moderator, error = await self._ensure_mod(interaction, perm_name="kick_members")
        if error or moderator is None:
            await self._deny(interaction, error or "Permission denied.")
            return

        target, error = await self._resolve_target(interaction)
        if error or target is None:
            await self._deny(interaction, error or "Target not found.")
            return

        ok, reason_text = _can_act_on_member(moderator, target)
        if not ok:
            await self._deny(interaction, reason_text)
            return

        ok, reason_text = _bot_can_act_on_member(interaction.guild, target)  # type: ignore[arg-type]
        if not ok:
            await self._deny(interaction, reason_text)
            return

        reason = _quick_mod_default_reason("kick", moderator)

        try:
            await target.kick(reason=reason)
            await self._ok(interaction, f"👢 Kicked `{target}`")
        except Exception as e:
            await self._deny(interaction, f"Kick failed: {e}")

    @discord.ui.button(label="Timeout", style=discord.ButtonStyle.primary, emoji="⏳")
    async def timeout_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        moderator, error = await self._ensure_mod(interaction, perm_name="moderate_members")
        if error or moderator is None:
            await self._deny(interaction, error or "Permission denied.")
            return

        target, error = await self._resolve_target(interaction)
        if error or target is None:
            await self._deny(interaction, error or "Target not found.")
            return

        ok, reason_text = _can_act_on_member(moderator, target)
        if not ok:
            await self._deny(interaction, reason_text)
            return

        ok, reason_text = _bot_can_act_on_member(interaction.guild, target)  # type: ignore[arg-type]
        if not ok:
            await self._deny(interaction, reason_text)
            return

        minutes = _safe_int(globals().get("MOD_TIMEOUT_MINUTES", 10), 10)
        minutes = max(1, min(minutes, 28 * 24 * 60))
        until = _now_utc() + timedelta(minutes=minutes)
        reason = _quick_mod_default_reason("timeout", moderator)

        try:
            await target.timeout(until, reason=reason)
            await self._ok(interaction, f"⏳ Timed out `{target}` for {_duration_label_from_minutes(minutes)}")
        except Exception as e:
            await self._deny(interaction, f"Timeout failed: {e}")


def build_quick_mod_view(target_user_id: int) -> discord.ui.View:
    return QuickModView(int(target_user_id))


# ==========================================================
# Audit helpers
# ==========================================================

async def _iter_recent_audit_entries(
    guild: discord.Guild,
    action: discord.AuditLogAction,
    *,
    limit: int = 8,
    max_age_seconds: int = 20,
) -> List[discord.AuditLogEntry]:
    entries: List[discord.AuditLogEntry] = []
    cutoff = _now_utc() - timedelta(seconds=max_age_seconds)

    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            created = _safe_dt_utc(getattr(entry, "created_at", None))
            if created and created < cutoff:
                continue
            entries.append(entry)
    except Exception:
        return []

    return entries


def _audit_target_id(entry: Optional[discord.AuditLogEntry]) -> int:
    try:
        if entry is None:
            return 0
        target = getattr(entry, "target", None)
        return int(getattr(target, "id", 0) or 0)
    except Exception:
        return 0


def _audit_created_delta_seconds(entry: Optional[discord.AuditLogEntry]) -> float:
    try:
        if entry is None:
            return 999999.0
        created = _safe_dt_utc(getattr(entry, "created_at", None))
        if not created:
            return 999999.0
        return abs((_now_utc() - created).total_seconds())
    except Exception:
        return 999999.0


def _format_actor_from_audit(entry: Optional[discord.AuditLogEntry]) -> Tuple[str, str]:
    try:
        if not entry:
            return ("Unknown", "")
        user = getattr(entry, "user", None)
        reason = getattr(entry, "reason", None) or ""
        if user:
            name = (
                getattr(user, "global_name", None)
                or getattr(user, "name", None)
                or str(user)
            )
            actor = f"{name} ({getattr(user, 'id', 'unknown')})"
            return (actor, reason)
        return ("Unknown", reason)
    except Exception:
        return ("Unknown", "")


def _actor_id_from_audit(entry: Optional[discord.AuditLogEntry]) -> Optional[int]:
    try:
        if not entry:
            return None
        user = getattr(entry, "user", None)
        uid = int(getattr(user, "id", 0) or 0)
        return uid or None
    except Exception:
        return None

async def _audit_find_recent_ban(guild: discord.Guild, user_id: int) -> Optional[discord.AuditLogEntry]:
    try:
        for entry in await _iter_recent_audit_entries(
            guild,
            discord.AuditLogAction.ban,
            limit=8,
            max_age_seconds=30,
        ):
            if _audit_target_id(entry) == int(user_id):
                return entry
    except Exception:
        pass
    return None


async def _audit_find_recent_kick(guild: discord.Guild, user_id: int) -> Optional[discord.AuditLogEntry]:
    try:
        for entry in await _iter_recent_audit_entries(
            guild,
            discord.AuditLogAction.kick,
            limit=8,
            max_age_seconds=20,
        ):
            if _audit_target_id(entry) == int(user_id):
                return entry
    except Exception:
        pass
    return None


async def _audit_find_best_member_update_match(
    guild: discord.Guild,
    target_user_id: int,
) -> Optional[discord.AuditLogEntry]:
    candidates: List[discord.AuditLogEntry] = []

    try:
        candidates.extend(
            await _iter_recent_audit_entries(
                guild,
                discord.AuditLogAction.member_role_update,
                limit=12,
                max_age_seconds=20,
            )
        )
    except Exception:
        pass

    try:
        candidates.extend(
            await _iter_recent_audit_entries(
                guild,
                discord.AuditLogAction.member_update,
                limit=12,
                max_age_seconds=20,
            )
        )
    except Exception:
        pass

    filtered = [e for e in candidates if _audit_target_id(e) == int(target_user_id)]
    if not filtered:
        return None

    filtered.sort(key=_audit_created_delta_seconds)
    return filtered[0]


async def _audit_find_recent_voice_action(
    guild: discord.Guild,
    target_user_id: int,
) -> Optional[discord.AuditLogEntry]:
    candidates: List[discord.AuditLogEntry] = []

    for action in (
        discord.AuditLogAction.member_disconnect,
        discord.AuditLogAction.member_move,
        discord.AuditLogAction.member_update,
    ):
        try:
            candidates.extend(
                await _iter_recent_audit_entries(
                    guild,
                    action,
                    limit=8,
                    max_age_seconds=20,
                )
            )
        except Exception:
            continue

    filtered = [e for e in candidates if _audit_target_id(e) == int(target_user_id)]
    if not filtered:
        return None

    filtered.sort(key=_audit_created_delta_seconds)
    return filtered[0]


# ==========================================================
# Context / DB helpers
# ==========================================================

async def _run_blocking_db(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _sb_select_guild_member_sync(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    try:
        sb = get_supabase()
        if not sb:
            return None

        res = (
            sb.table("guild_members")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception as e:
        print("⚠️ _sb_select_guild_member_sync failed:", repr(e))
    return None


def _sb_select_latest_join_sync(guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    try:
        sb = get_supabase()
        if not sb:
            return None

        res = (
            sb.table("member_joins")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .order("joined_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            return dict(rows[0])
    except Exception as e:
        print("⚠️ _sb_select_latest_join_sync failed:", repr(e))
    return None


def _sb_select_warn_count_sync(guild_id: int, user_id: int) -> int:
    try:
        sb = get_supabase()
        if not sb:
            return 0

        res = (
            sb.table("warns")
            .select("id", count="exact")
            .eq("guild_id", str(int(guild_id)))
            .eq("user_id", str(int(user_id)))
            .execute()
        )
        return int(getattr(res, "count", 0) or 0)
    except Exception:
        return 0


def _sb_get_identity_truth_context_sync(guild_id: int, user_id: int) -> Dict[str, Any]:
    try:
        row = get_identity_truth_context(guild_id=str(int(guild_id)), user_id=str(int(user_id)))
        return dict(row) if isinstance(row, dict) else {}
    except Exception:
        return {}


def _source_key_from_join_rows(latest_join: Dict[str, Any], guild_member: Dict[str, Any]) -> Tuple[str, str]:
    latest = dict(latest_join or {})
    member_row = dict(guild_member or {})

    invite_code = _safe_str(latest.get("invite_code") or member_row.get("invite_code"))
    if invite_code and invite_code.lower() not in {"unknown", "none", "null"}:
        return ("invite_code", invite_code)

    join_source = _safe_str(latest.get("join_source") or member_row.get("join_source"))
    if join_source and join_source.lower() not in {"unknown", "unknown_join", "none", "null"}:
        return ("join_source", join_source)

    entry_method = _safe_str(latest.get("entry_method") or member_row.get("entry_method"))
    if entry_method and entry_method.lower() not in {"unknown", "unknown_join", "none", "null"}:
        return ("entry_method", entry_method)

    return ("", "")


def _sb_select_source_reputation_sync(
    guild_id: int,
    user_id: int,
    latest_join: Dict[str, Any],
    guild_member: Dict[str, Any],
) -> Dict[str, Any]:
    try:
        field, value = _source_key_from_join_rows(latest_join, guild_member)
        if not field or not value:
            return {}

        sb = get_supabase()
        if not sb:
            return {}

        res = (
            sb.table("member_joins")
            .select(
                "user_id,username,joined_at,invite_code,join_source,entry_method,entry_truth_quality,entry_confidence,risk_score,risk_level,evidence_tier",
                count="exact",
            )
            .eq("guild_id", str(int(guild_id)))
            .eq(field, value)
            .order("joined_at", desc=True)
            .limit(50)
            .execute()
        )

        rows = [dict(r) for r in (getattr(res, "data", None) or []) if isinstance(r, dict)]
        total = int(getattr(res, "count", 0) or len(rows) or 0)

        risky = 0
        strong_or_confirmed = 0
        low_confidence = 0
        unique_users: Set[str] = set()

        for row in rows:
            uid = _safe_str(row.get("user_id"))
            if uid:
                unique_users.add(uid)

            score = _safe_int(row.get("risk_score"), 0)
            level = _safe_str(row.get("risk_level")).lower()
            tier = _safe_str(row.get("evidence_tier")).lower()
            confidence = _safe_int(row.get("entry_confidence"), 0)

            if score >= 45 or level in {"medium", "high", "critical"}:
                risky += 1
            if tier in {"strongly_linked", "confirmed_duplicate"}:
                strong_or_confirmed += 1
            if confidence and confidence < 50:
                low_confidence += 1

        return {
            "source_field": field,
            "source_value": value,
            "sample_size": len(rows),
            "total_count": total,
            "unique_users": len(unique_users),
            "risky_count": risky,
            "strong_or_confirmed_count": strong_or_confirmed,
            "low_confidence_count": low_confidence,
        }
    except Exception as e:
        print("⚠️ _sb_select_source_reputation_sync failed:", repr(e))
        return {}


def _extract_flags_from_profile_like(data: Dict[str, Any]) -> List[str]:
    flags: List[str] = []

    if _safe_bool(data.get("default_avatar")):
        flags.append("default_avatar")
    if _safe_bool(data.get("suspicious_name_pattern")):
        flags.append("suspicious_name")
    if _safe_bool(data.get("repeated_char_pattern")):
        flags.append("repeated_chars")

    for item in _safe_string_list(data.get("suspicion_flags"), 20):
        if item not in flags:
            flags.append(item)

    return flags[:12]


def _pretty_flag_label(flag: Any) -> str:
    raw = _safe_str(flag)
    if not raw:
        return ""

    mapping = {
        "default_avatar": "Default avatar",
        "suspicious_name": "Suspicious name",
        "suspicious_name_pattern": "Suspicious name pattern",
        "repeated_chars": "Repeated characters",
        "repeated_character_pattern": "Repeated characters",
        "very_high_digit_ratio": "Very high digit ratio",
        "elevated_digit_ratio": "Elevated digit ratio",
        "high_digit_ratio": "High digit ratio",
        "long_digit_run": "Long digit run",
        "high_underscore_ratio": "High underscore ratio",
        "synthetic_style_name": "Synthetic-looking name",
        "staff_style_name": "Staff-style name",
        "join_burst": "Join burst",
        "shared_behavior_fingerprint": "Shared fingerprint",
        "similar_recent_username": "Similar recent usernames",
        "age_bucket_cluster": "Age-bucket cluster",
        "extremely_new_account": "Extremely new account",
        "very_new_account": "Very new account",
        "fresh_account": "Fresh account",
        "instant_join_after_creation": "Joined immediately after creation",
        "fast_join_after_creation": "Joined soon after creation",
        "same_day_join_after_creation": "Joined same day as creation",
        "bot_account": "Bot account",
        "cluster_triad": "Multi-signal cluster match",
        "burst_cluster_combo": "Burst + cluster combo",
        "name_cluster_combo": "Name cluster combo",
    }
    if raw in mapping:
        return mapping[raw]
    return raw.replace("_", " ").strip().capitalize()


def _pretty_truth_link_type(link_type: Any) -> str:
    raw = _safe_str(link_type).lower()
    if raw == "confirmed_duplicate":
        return "Confirmed duplicate"
    if raw == "same_person_likely":
        return "Likely same person"
    if raw == "not_linked":
        return "Not linked"
    return raw.replace("_", " ").strip().capitalize() if raw else "Unknown"


def _truth_context_other_id_label(guild: Optional[discord.Guild], row: Dict[str, Any]) -> str:
    uid = _safe_int(row.get("other_user_id") or row.get("matched_user_id") or row.get("user_id"), 0)
    if uid <= 0:
        return "`unknown`"
    try:
        if guild is not None:
            member = guild.get_member(uid)
            if member is not None:
                return f"{member.mention} (`{uid}`)"
    except Exception:
        pass
    return f"`{uid}`"


def _context_truth_value(
    guild: Optional[discord.Guild],
    truth_context: Dict[str, Any],
    merged_risk: Optional[Dict[str, Any]] = None,
) -> str:
    truth = dict(truth_context or {})
    merged = dict(merged_risk or {})

    proof_matches = list(truth.get("proof_matches") or [])
    manual_confirmed = list(truth.get("manual_confirmed") or [])
    manual_likely = list(truth.get("manual_likely") or [])
    manual_not_linked = list(truth.get("manual_not_linked") or [])

    proof_count = max(len(proof_matches), _safe_int(merged.get("identity_proof_match_count"), 0))
    confirmed_count = max(len(manual_confirmed), _safe_int(merged.get("manual_confirmed_match_count"), 0))
    likely_count = max(len(manual_likely), _safe_int(merged.get("manual_likely_match_count"), 0))
    not_linked_count = max(len(manual_not_linked), _safe_int(merged.get("manual_not_linked_count"), 0))

    if proof_count <= 0 and confirmed_count <= 0 and likely_count <= 0 and not_linked_count <= 0:
        return ""

    lines: List[str] = []
    header_parts: List[str] = []
    if proof_count > 0:
        header_parts.append(f"proof_matches={proof_count}")
    if confirmed_count > 0:
        header_parts.append(f"manual_confirmed={confirmed_count}")
    if likely_count > 0:
        header_parts.append(f"manual_likely={likely_count}")
    if not_linked_count > 0:
        header_parts.append(f"not_linked={not_linked_count}")
    if header_parts:
        lines.append(" • ".join(header_parts))

    for row in proof_matches[:3]:
        lines.append(
            f"• {_truth_context_other_id_label(guild, row)} — verified identity fingerprint match"
        )

    for row in manual_confirmed[:2]:
        reason = _safe_str(row.get("reason"))
        line = f"• {_truth_context_other_id_label(guild, row)} — {_pretty_truth_link_type(row.get('link_type'))}"
        if reason:
            line += f" ({_truncate(reason, 80)})"
        lines.append(line)

    for row in manual_likely[:2]:
        reason = _safe_str(row.get("reason"))
        line = f"• {_truth_context_other_id_label(guild, row)} — {_pretty_truth_link_type(row.get('link_type'))}"
        if reason:
            line += f" ({_truncate(reason, 80)})"
        lines.append(line)

    for row in manual_not_linked[:2]:
        reason = _safe_str(row.get("reason"))
        line = f"• {_truth_context_other_id_label(guild, row)} — {_pretty_truth_link_type(row.get('link_type'))}"
        if reason:
            line += f" ({_truncate(reason, 80)})"
        lines.append(line)

    return _chunk_lines(lines, 1000)


def _member_role_ids(member_or_user: discord.abc.User) -> Set[int]:
    out: Set[int] = set()
    try:
        for role in getattr(member_or_user, "roles", []) or []:
            role_id = _safe_int(getattr(role, "id", 0), 0)
            if role_id > 0:
                out.add(role_id)
    except Exception:
        pass
    return out


def _configured_access_state(
    member_or_user: discord.abc.User,
    runtime_config: Dict[str, Any],
    *,
    is_bot: bool,
) -> Tuple[str, str]:
    if is_bot:
        return (
            "BOT ACCOUNT",
            "Review who added the bot and whether its permissions are appropriate.",
        )

    role_ids = _member_role_ids(member_or_user)

    unverified_id = _safe_int(runtime_config.get("unverified_role_id"), 0)
    verified_id = _safe_int(runtime_config.get("verified_role_id"), 0)
    resident_id = _safe_int(runtime_config.get("resident_role_id"), 0)
    staff_id = _safe_int(runtime_config.get("staff_role_id"), 0)
    vc_staff_id = _safe_int(runtime_config.get("vc_staff_role_id"), 0)

    configured_ids = {
        role_id
        for role_id in (
            unverified_id,
            verified_id,
            resident_id,
            staff_id,
            vc_staff_id,
        )
        if role_id > 0
    }

    if staff_id > 0 and staff_id in role_ids:
        return ("STAFF / PRIVILEGED", "Configured staff role is present.")
    if vc_staff_id > 0 and vc_staff_id in role_ids:
        return ("STAFF / PRIVILEGED", "Configured VC staff role is present.")

    has_unverified = unverified_id > 0 and unverified_id in role_ids
    has_verified = verified_id > 0 and verified_id in role_ids
    has_resident = resident_id > 0 and resident_id in role_ids

    if has_unverified:
        return (
            "UNVERIFIED / CONTAINED",
            "Configured unverified role is present.",
        )

    if has_verified or has_resident:
        return (
            "VERIFIED ACCESS",
            "Configured verified/resident access role is present.",
        )

    if not configured_ids:
        return (
            "ROLE CONFIG MISSING",
            "No authoritative access-role IDs are configured for this server.",
        )

    return (
        "NO ACCESS ROLE DETECTED",
        "Member has none of the configured unverified, verified, resident, or staff roles.",
    )


def _join_source_is_uncertain(
    entry_method: str,
    join_source: str,
    entry_confidence: int,
) -> bool:
    uncertain_values = {
        "",
        "unknown",
        "unknown_join",
        "invite_unresolved",
        "invite_cache_warming",
        "invite_tracking_unavailable",
    }
    return (
        str(entry_method or "").strip().lower() in uncertain_values
        or str(join_source or "").strip().lower() in uncertain_values
        or int(entry_confidence or 0) < 50
    )


async def _build_member_context_fields(
    guild: discord.Guild,
    member_or_user: discord.abc.User,
) -> List[Tuple[str, str, bool]]:
    try:
        guild_member = await _run_blocking_db(
            _sb_select_guild_member_sync,
            guild.id,
            member_or_user.id,
        ) or {}
        latest_join = await _run_blocking_db(
            _sb_select_latest_join_sync,
            guild.id,
            member_or_user.id,
        ) or {}
        warn_count = await _run_blocking_db(
            _sb_select_warn_count_sync,
            guild.id,
            member_or_user.id,
        )
        truth_context = await _run_blocking_db(
            _sb_get_identity_truth_context_sync,
            guild.id,
            member_or_user.id,
        ) or {}
        source_reputation = await _run_blocking_db(
            _sb_select_source_reputation_sync,
            guild.id,
            member_or_user.id,
            latest_join if isinstance(latest_join, dict) else {},
            guild_member if isinstance(guild_member, dict) else {},
        ) or {}
    except Exception:
        guild_member = {}
        latest_join = {}
        warn_count = 0
        truth_context = {}
        source_reputation = {}

    try:
        runtime_config = dict(await get_guild_config(guild.id) or {})
    except Exception:
        runtime_config = {}

    risk_profile: Dict[str, Any] = {}
    try:
        if isinstance(member_or_user, discord.Member):
            risk_profile = build_member_risk_profile(member_or_user) or {}
    except Exception:
        risk_profile = {}

    merged_risk: Dict[str, Any] = {}
    merged_risk.update(guild_member if isinstance(guild_member, dict) else {})
    merged_risk.update(latest_join if isinstance(latest_join, dict) else {})
    merged_risk.update(risk_profile if isinstance(risk_profile, dict) else {})

    is_bot = (
        _safe_bool(merged_risk.get("is_bot_account"), False)
        or bool(getattr(member_or_user, "bot", False))
    )

    score = _safe_int(
        merged_risk.get("risk_score"),
        _safe_int(merged_risk.get("score"), 0),
    )
    level = _safe_str(
        merged_risk.get("risk_level") or merged_risk.get("level"),
        "low",
    ).upper()
    tier = _safe_str(
        merged_risk.get("evidence_tier"),
        "clear",
    ).replace("_", " ").upper()

    account_age = _account_age_human(member_or_user)
    _joined_gap_seconds, joined_gap_human = _join_after_creation_delta(
        member_or_user if isinstance(member_or_user, discord.Member) else None
    )

    entry_method = _safe_str(
        merged_risk.get("entry_method")
        or latest_join.get("entry_method")
        or guild_member.get("entry_method"),
        "unknown",
    )
    join_source = _safe_str(
        merged_risk.get("join_source")
        or latest_join.get("join_source")
        or guild_member.get("join_source"),
        "unknown",
    )
    invite_code = _safe_str(
        merged_risk.get("invite_code")
        or latest_join.get("invite_code")
        or guild_member.get("invite_code"),
        "unknown",
    )
    entry_quality = _safe_str(
        merged_risk.get("entry_truth_quality")
        or latest_join.get("entry_truth_quality")
        or guild_member.get("entry_truth_quality"),
        "unknown",
    )
    entry_confidence = _safe_int(
        merged_risk.get("entry_confidence")
        or latest_join.get("entry_confidence")
        or guild_member.get("entry_confidence"),
        0,
    )
    entry_reason = _safe_str(
        merged_risk.get("entry_quality_reason")
        or latest_join.get("entry_quality_reason")
        or guild_member.get("entry_quality_reason")
    )

    source_uncertain = _join_source_is_uncertain(
        entry_method,
        join_source,
        entry_confidence,
    )

    source_sample = _safe_int(source_reputation.get("sample_size"), 0)
    source_risky = _safe_int(source_reputation.get("risky_count"), 0)
    source_strong = _safe_int(
        source_reputation.get("strong_or_confirmed_count"),
        0,
    )
    source_low_conf = _safe_int(
        source_reputation.get("low_confidence_count"),
        0,
    )

    identity_matches = max(
        _safe_int(merged_risk.get("identity_proof_match_count"), 0),
        len(list(truth_context.get("proof_matches") or [])),
    )
    manual_confirmed = max(
        _safe_int(merged_risk.get("manual_confirmed_match_count"), 0),
        len(list(truth_context.get("manual_confirmed") or [])),
    )
    manual_likely = max(
        _safe_int(merged_risk.get("manual_likely_match_count"), 0),
        len(list(truth_context.get("manual_likely") or [])),
    )

    flags = _extract_flags_from_profile_like(merged_risk)
    pretty_flags = [
        _pretty_flag_label(flag)
        for flag in flags
        if _pretty_flag_label(flag)
    ]

    fingerprint_count = _safe_int(
        merged_risk.get("same_fingerprint_count"),
        0,
    )
    similar_name_count = _safe_int(
        merged_risk.get("similar_name_count"),
        0,
    )
    burst_count = _safe_int(
        merged_risk.get("burst_join_count")
        or merged_risk.get("burst_count"),
        0,
    )

    alt_tier = _safe_str(
        merged_risk.get("alt_evidence_tier")
        or merged_risk.get("evidence_tier"),
        "clear",
    ).lower()
    alt_score = _safe_int(merged_risk.get("alt_risk_score"), 0)
    spam_score = _safe_int(merged_risk.get("spam_risk_score"), 0)
    spam_level = _safe_str(merged_risk.get("spam_risk_level"), "low").lower()
    profile_score = _safe_int(merged_risk.get("profile_risk_score"), 0)
    context_score = _safe_int(
        merged_risk.get("context_risk_score"),
        profile_score,
    )
    review_verdict = _safe_str(merged_risk.get("review_verdict"))
    recommended_action = _safe_str(merged_risk.get("recommended_action"))
    account_age_days = _safe_int(merged_risk.get("account_age_days"), 999999)

    alt_labels = {
        "clear": "No linked-account evidence",
        "suspicious": "Possible correlated-account pattern",
        "strongly_linked": "Strong linked-account evidence",
        "confirmed_duplicate": "Confirmed duplicate identity",
        "excluded_bot": "Excluded from human alt scoring",
    }
    alt_label = alt_labels.get(alt_tier, "No linked-account evidence")
    if alt_score > 0 and alt_tier not in {"clear", "excluded_bot"}:
        alt_label += f" ({alt_score}/100)"

    if spam_score >= 35:
        spam_label = f"Detected ({spam_level}, {spam_score}/100)"
    else:
        spam_label = "No SpamGuard incident evidence"

    if is_bot:
        review_verdict = review_verdict or "OFFICIAL BOT — REVIEW PERMISSIONS"
        profile_label = "Bot account"
        recommended_action = recommended_action or (
            "Review who added the bot and whether its permissions are appropriate."
        )
    elif profile_score >= 35:
        profile_label = f"Low-confidence profile review ({profile_score}/100)"
    elif account_age_days <= 7:
        profile_label = "New account — normal verification context"
    elif context_score > 0:
        profile_label = "Minor profile context only"
    else:
        profile_label = "No notable profile pattern"

    if not review_verdict:
        if alt_tier == "confirmed_duplicate":
            review_verdict = "CONFIRMED DUPLICATE IDENTITY"
        elif alt_tier == "strongly_linked":
            review_verdict = "STRONG ALT LINK — STAFF REVIEW"
        elif spam_score >= 70:
            review_verdict = "HIGH-CONFIDENCE SPAM ACCOUNT"
        elif spam_score >= 35:
            review_verdict = "SPAM BEHAVIOR DETECTED"
        elif alt_tier == "suspicious":
            review_verdict = "POSSIBLE ALT LINK — REVIEW"
        elif account_age_days <= 7:
            review_verdict = "NEW ACCOUNT — VERIFY NORMALLY"
        else:
            review_verdict = "LOW CONCERN"

    recommended_action = recommended_action or (
        "Continue normal verification; act only on linked-account proof or observed behavior."
    )

    assessment_lines = [
        f"Status: **{review_verdict}**",
        f"Alt identity: **{alt_label}**",
        f"Spam behavior: **{spam_label}**",
        f"Profile context: **{profile_label}**",
        f"Recommended action: {recommended_action}",
    ]

    evidence_lines = [f"Account age: **{account_age}**"]
    if joined_gap_human:
        evidence_lines.append(f"Created-to-join timing: **{joined_gap_human}**")

    unknown_values = {
        "",
        "unknown",
        "unknown_join",
        "none",
        "null",
        "invite_unresolved",
        "invite_cache_warming",
        "invite_tracking_unavailable",
    }
    method_known = entry_method.strip().lower() not in unknown_values
    source_known = join_source.strip().lower() not in unknown_values
    invite_known = invite_code.strip().lower() not in unknown_values
    quality_known = entry_quality.strip().lower() not in unknown_values

    if method_known:
        evidence_lines.append(f"Entry method: **{entry_method.replace('_', ' ')}**")
    if source_known:
        evidence_lines.append(f"Source: **{join_source}**")
    if invite_known:
        evidence_lines.append(f"Invite: `discord.gg/{invite_code}`")
    if quality_known and entry_confidence > 0:
        evidence_lines.append(
            f"Source confidence: **{entry_quality} / {entry_confidence}/100**"
        )
    if entry_reason and (method_known or source_known or invite_known):
        evidence_lines.append(f"Source note: {_truncate(entry_reason, 180)}")

    if pretty_flags:
        evidence_lines.append(
            "Context — not identity proof: " + " • ".join(pretty_flags[:4])
        )

    cluster_bits: List[str] = []
    if fingerprint_count > 0:
        cluster_bits.append(f"shared profile matches={fingerprint_count}")
    if similar_name_count > 0:
        cluster_bits.append(f"similar recent names={similar_name_count}")
    if burst_count >= 3:
        cluster_bits.append(f"join surge={burst_count}")
    if cluster_bits:
        evidence_lines.append("Correlated activity: " + " • ".join(cluster_bits))

    if source_sample >= 3 and (source_risky > 0 or source_strong > 0):
        evidence_lines.append(
            "Source history: "
            f"{source_sample} prior join(s) • risky={source_risky} • "
            f"strong/confirmed={source_strong}"
        )

    if warn_count > 0:
        evidence_lines.append(f"Prior warnings: **{warn_count}**")

    fields: List[Tuple[str, str, bool]] = [
        ("Assessment", _chunk_lines(assessment_lines, 1000), False),
        ("Relevant Context", _chunk_lines(evidence_lines, 1000), False),
    ]

    truth_value = _context_truth_value(
        guild,
        truth_context,
        merged_risk,
    )
    if truth_value:
        fields.append(
            (
                "Identity Links",
                _truncate(truth_value, 1000),
                False,
            )
        )

    return fields



# ==========================================================
# Public logging helpers
# ==========================================================

async def maybe_log_recent_ban(guild: discord.Guild, user: discord.abc.User) -> bool:
    try:
        entry = await _audit_find_recent_ban(guild, int(user.id))
        actor, reason = _format_actor_from_audit(entry)

        embed = discord.Embed(
            title="🔨 Member Banned",
            color=discord.Color.red(),
            timestamp=_now_utc(),
        )
        embed.add_field(
            name="User",
            value=f"<@{user.id}> (`{user}` | `{user.id}`)",
            inline=False,
        )
        embed.add_field(name="By", value=_truncate(actor, 1024), inline=False)
        embed.add_field(name="Reason", value=_truncate(reason or "—", 1024), inline=False)

        try:
            if isinstance(user, discord.Member):
                for name, value, inline in await _build_member_context_fields(guild, user):
                    embed.add_field(name=name, value=value, inline=inline)
        except Exception:
            pass

        await _post_modlog(guild, embed)
        return True
    except Exception as e:
        print("⚠️ maybe_log_recent_ban error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


async def maybe_log_recent_kick(guild: discord.Guild, member: discord.Member) -> bool:
    try:
        entry = await _audit_find_recent_kick(guild, int(member.id))
        if entry is None:
            return False

        actor, reason = _format_actor_from_audit(entry)

        embed = discord.Embed(
            title="👢 Member Kicked",
            color=discord.Color.orange(),
            timestamp=_now_utc(),
        )
        embed.add_field(
            name="User",
            value=f"{member.mention} (`{member}` | `{member.id}`)",
            inline=False,
        )
        embed.add_field(name="By", value=_truncate(actor, 1024), inline=False)
        embed.add_field(name="Reason", value=_truncate(reason or "—", 1024), inline=False)

        try:
            for name, value, inline in await _build_member_context_fields(guild, member):
                embed.add_field(name=name, value=value, inline=inline)
        except Exception:
            pass

        await _post_modlog(guild, embed)
        return True
    except Exception as e:
        print("⚠️ maybe_log_recent_kick error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


def _roles_diff_lines(before: discord.Member, after: discord.Member) -> Tuple[List[str], List[str]]:
    before_roles = {int(r.id): r for r in (before.roles or []) if not r.is_default()}
    after_roles = {int(r.id): r for r in (after.roles or []) if not r.is_default()}

    added_ids = [rid for rid in after_roles if rid not in before_roles]
    removed_ids = [rid for rid in before_roles if rid not in after_roles]

    added = [f"+ {after_roles[rid].name} (`{rid}`)" for rid in added_ids]
    removed = [f"- {before_roles[rid].name} (`{rid}`)" for rid in removed_ids]
    return added[:20], removed[:20]


def _timeout_change_lines(before: discord.Member, after: discord.Member) -> List[str]:
    lines: List[str] = []
    try:
        b = _safe_dt_utc(getattr(before, "timed_out_until", None))
        a = _safe_dt_utc(getattr(after, "timed_out_until", None))

        b_active = bool(b and b > _now_utc())
        a_active = bool(a and a > _now_utc())

        if not b_active and a_active:
            remaining = max(0, int((a - _now_utc()).total_seconds() // 60))
            lines.append(f"Timeout set until {_discord_ts(a)} ({_duration_label_from_minutes(max(1, remaining))})")
        elif b_active and not a_active:
            lines.append("Timeout removed")
        elif b_active and a_active and int(a.timestamp()) != int(b.timestamp()):
            remaining = max(0, int((a - _now_utc()).total_seconds() // 60))
            lines.append(f"Timeout updated to {_discord_ts(a)} ({_duration_label_from_minutes(max(1, remaining))})")
    except Exception:
        pass
    return lines


def _role_id_map_for_update(member: discord.Member) -> Dict[int, discord.Role]:
    out: Dict[int, discord.Role] = {}
    try:
        for role in getattr(member, "roles", []) or []:
            if role.is_default():
                continue
            role_id = _safe_int(getattr(role, "id", 0), 0)
            if role_id > 0:
                out[role_id] = role
    except Exception:
        pass
    return out


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(config, dict):
            value = config.get(key)
            return default if value is None else value
        value = getattr(config, key, None)
        return default if value is None else value
    except Exception:
        return default


async def _is_expected_recent_unverified_assignment(
    guild: discord.Guild,
    before: discord.Member,
    after: discord.Member,
    *,
    added_ids: Set[int],
    removed_ids: Set[int],
    timeout_lines: List[str],
    nickname_changed: bool,
) -> bool:
    if (
        bool(getattr(after, "bot", False))
        or removed_ids
        or timeout_lines
        or nickname_changed
        or len(added_ids) != 1
    ):
        return False

    try:
        config = await get_guild_config(guild.id)
    except Exception:
        return False

    unverified_role_id = _safe_int(
        _config_value(config, "unverified_role_id", 0)
        or globals().get("UNVERIFIED_ROLE_ID")
        or globals().get("ROLE_UNVERIFIED_ID"),
        0,
    )
    if unverified_role_id <= 0 or added_ids != {unverified_role_id}:
        return False

    joined_at = _safe_dt_utc(getattr(after, "joined_at", None))
    if joined_at is None:
        return False
    return 0 <= (_now_utc() - joined_at).total_seconds() <= 300


def _member_update_event_key(
    after: discord.Member,
    *,
    added_ids: Set[int],
    removed_ids: Set[int],
    timeout_lines: List[str],
    nickname_changed: bool,
) -> str:
    payload = "|".join(
        [
            f"member={int(after.id)}",
            "added=" + ",".join(str(value) for value in sorted(added_ids)),
            "removed=" + ",".join(str(value) for value in sorted(removed_ids)),
            f"nick={int(bool(nickname_changed))}",
            "timeout=" + ";".join(timeout_lines),
        ]
    )
    digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"member_update:{after.id}:{digest}"


async def maybe_log_member_update_diff(
    guild: discord.Guild,
    before: discord.Member,
    after: discord.Member,
) -> bool:
    try:
        before_roles = _role_id_map_for_update(before)
        after_roles = _role_id_map_for_update(after)
        added_ids = set(after_roles) - set(before_roles)
        removed_ids = set(before_roles) - set(after_roles)

        role_added = [
            f"+ {after_roles[role_id].name} (`{role_id}`)"
            for role_id in sorted(added_ids)
        ][:20]
        role_removed = [
            f"- {before_roles[role_id].name} (`{role_id}`)"
            for role_id in sorted(removed_ids)
        ][:20]
        timeout_lines = _timeout_change_lines(before, after)
        nickname_changed = (before.nick or "") != (after.nick or "")

        if not role_added and not role_removed and not timeout_lines and not nickname_changed:
            return False

        if await _is_expected_recent_unverified_assignment(
            guild,
            before,
            after,
            added_ids=added_ids,
            removed_ids=removed_ids,
            timeout_lines=timeout_lines,
            nickname_changed=nickname_changed,
        ):
            return False

        entry = await _audit_find_best_member_update_match(guild, int(after.id))
        actor, audit_reason = _format_actor_from_audit(entry)

        embed = discord.Embed(
            title="📝 Member Updated",
            color=discord.Color.teal(),
            timestamp=_now_utc(),
        )
        embed.add_field(
            name="User",
            value=f"{after.mention} (`{after}` | `{after.id}`)",
            inline=False,
        )

        if role_added or role_removed:
            embed.add_field(
                name="Role Changes",
                value=_chunk_lines(role_added + role_removed, 1000),
                inline=False,
            )

        if timeout_lines:
            embed.add_field(
                name="Timeout",
                value=_chunk_lines(timeout_lines, 1000),
                inline=False,
            )

        if nickname_changed:
            before_nick = _safe_str(before.nick, "None")
            after_nick = _safe_str(after.nick, "None")
            embed.add_field(
                name="Nickname",
                value=_chunk_lines(
                    [f"Before: {before_nick}", f"After: {after_nick}"],
                    1000,
                ),
                inline=False,
            )

        embed.add_field(name="By", value=_truncate(actor, 1024), inline=False)
        if audit_reason:
            embed.add_field(
                name="Reason",
                value=_truncate(audit_reason, 1024),
                inline=False,
            )

        await _post_modlog(
            guild,
            embed,
            event_key=_member_update_event_key(
                after,
                added_ids=added_ids,
                removed_ids=removed_ids,
                timeout_lines=timeout_lines,
                nickname_changed=nickname_changed,
            ),
            dedupe_window_seconds=20,
        )
        return True
    except Exception as e:
        print("⚠️ maybe_log_member_update_diff error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


def _voice_state_change_lines(before: discord.VoiceState, after: discord.VoiceState) -> List[str]:
    lines: List[str] = []

    try:
        before_channel = getattr(before, "channel", None)
        after_channel = getattr(after, "channel", None)

        if before_channel is None and after_channel is not None:
            lines.append(f"Joined voice: `{after_channel.name}`")
        elif before_channel is not None and after_channel is None:
            lines.append(f"Left voice: `{before_channel.name}`")
        elif before_channel is not None and after_channel is not None and int(before_channel.id) != int(after_channel.id):
            lines.append(f"Moved voice: `{before_channel.name}` → `{after_channel.name}`")

        if bool(getattr(before, "mute", False)) != bool(getattr(after, "mute", False)):
            lines.append(f"Server mute: `{bool(getattr(after, 'mute', False))}`")

        if bool(getattr(before, "deaf", False)) != bool(getattr(after, "deaf", False)):
            lines.append(f"Server deaf: `{bool(getattr(after, 'deaf', False))}`")

        if bool(getattr(before, "self_mute", False)) != bool(getattr(after, "self_mute", False)):
            lines.append(f"Self mute: `{bool(getattr(after, 'self_mute', False))}`")

        if bool(getattr(before, "self_deaf", False)) != bool(getattr(after, "self_deaf", False)):
            lines.append(f"Self deaf: `{bool(getattr(after, 'self_deaf', False))}`")

        if bool(getattr(before, "self_stream", False)) != bool(getattr(after, "self_stream", False)):
            lines.append(f"Streaming: `{bool(getattr(after, 'self_stream', False))}`")

        if bool(getattr(before, "self_video", False)) != bool(getattr(after, "self_video", False)):
            lines.append(f"Video: `{bool(getattr(after, 'self_video', False))}`")
    except Exception:
        pass

    return lines[:20]


async def maybe_log_voice_state_update(
    guild: discord.Guild,
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> bool:
    try:
        lines = _voice_state_change_lines(before, after)
        if not lines:
            return False

        entry = await _audit_find_recent_voice_action(guild, int(member.id))
        actor, audit_reason = _format_actor_from_audit(entry)

        embed = discord.Embed(
            title="🎙️ Voice State Updated",
            color=discord.Color.blurple(),
            timestamp=_now_utc(),
        )
        embed.add_field(
            name="User",
            value=f"{member.mention} (`{member}` | `{member.id}`)",
            inline=False,
        )
        embed.add_field(name="Changes", value=_chunk_lines(lines, 1000), inline=False)

        meaningful_staff_action = False
        if entry is not None:
            meaningful_staff_action = True

        if meaningful_staff_action:
            embed.add_field(name="By", value=_truncate(actor, 1024), inline=False)
            if audit_reason:
                embed.add_field(name="Reason", value=_truncate(audit_reason, 1024), inline=False)

        await _post_modlog(guild, embed)
        return True
    except Exception as e:
        print("⚠️ maybe_log_voice_state_update error:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


# ==========================================================
# Compatibility wrappers / exports
# ==========================================================

def _pretty_cluster_reason(reason: Any) -> str:
    raw = _safe_str(reason)
    if not raw:
        return "Linked in recent cluster"
    if raw == "same_fingerprint":
        return "Shared behavioral fingerprint"
    if raw == "same_age_bucket":
        return "Same account-age cluster"
    if raw.startswith("name_similarity:"):
        try:
            pct = float(raw.split(":", 1)[1]) * 100.0
            return f"Very similar username ({pct:.0f}% match)"
        except Exception:
            return "Very similar username"
    return raw.replace("_", " ").strip().capitalize()


def _context_role_state_value(guild_member: Dict[str, Any]) -> str:
    if not guild_member:
        return ""

    parts = [
        _safe_str(guild_member.get("role_state")),
        _safe_str(guild_member.get("role_state_reason")),
    ]
    text = _join_nonempty(parts, sep=" — ")
    return _truncate(text, 400)


def _context_entry_value(guild_member: Dict[str, Any], latest_join: Dict[str, Any]) -> str:
    row = latest_join or guild_member or {}
    if not row:
        return ""

    lines: List[str] = []

    entry_method = _safe_str(row.get("entry_method"))
    verification_source = _safe_str(row.get("verification_source"))
    invite_code = _safe_str(row.get("invite_code"))
    invited_by_name = _safe_str(row.get("invited_by_name"))
    vouched_by_name = _safe_str(row.get("vouched_by_name"))
    approved_by_name = _safe_str(row.get("approved_by_name"))
    join_note = _safe_str(row.get("join_note"))
    entry_reason = _safe_str(guild_member.get("entry_reason") or row.get("entry_reason"))
    approval_reason = _safe_str(guild_member.get("approval_reason") or row.get("approval_reason"))

    header = _join_nonempty(
        [
            f"method={entry_method}" if entry_method else "",
            f"source={verification_source}" if verification_source else "",
            f"invite={invite_code}" if invite_code else "",
        ]
    )
    if header:
        lines.append(header)
    if invited_by_name:
        lines.append(f"invited_by={invited_by_name}")
    if vouched_by_name:
        lines.append(f"vouched_by={vouched_by_name}")
    if approved_by_name:
        lines.append(f"approved_by={approved_by_name}")
    if join_note:
        lines.append(join_note)
    if entry_reason:
        lines.append(f"entry_reason={entry_reason}")
    if approval_reason:
        lines.append(f"approval_reason={approval_reason}")

    return _chunk_lines(lines, 900)


__all__ = [
    "_account_age_days",
    "_age_bucket",
    "_behavior_fingerprint",
    "_get_modlog_channel",
    "_post_modlog",
    "_post_raidlog",
    "_audit_find_recent_ban",
    "_audit_find_recent_kick",
    "_audit_find_best_member_update_match",
    "build_quick_mod_view",
    "maybe_log_recent_ban",
    "maybe_log_recent_kick",
    "maybe_log_member_update_diff",
    "maybe_log_voice_state_update",
]
