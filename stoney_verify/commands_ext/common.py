# stoney_verify/commands_ext/common.py
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, Optional, Tuple, List

import discord

from ..globals import *  # noqa: F401,F403
from ..globals import _parse_iso_datetime


# ============================================================
# Compatibility / shared runtime state (globals-backed)
# Ensures shared dicts exist across split modules even if older
# globals.py versions did not define them.
# ============================================================
try:
    from .. import globals as _g  # type: ignore

    if not hasattr(_g, "VC_REQUESTS"):
        _g.VC_REQUESTS = {}
    if not hasattr(_g, "VC_REQUEST_COOLDOWNS"):
        _g.VC_REQUEST_COOLDOWNS = {}
    if not hasattr(_g, "RUNTIME_STATS"):
        _g.RUNTIME_STATS = {}
    if not hasattr(_g, "TICKET_LAST_ACTIVITY"):
        _g.TICKET_LAST_ACTIVITY = {}

    # Rebind local names to shared module attributes
    VC_REQUESTS = _g.VC_REQUESTS  # type: ignore
    VC_REQUEST_COOLDOWNS = _g.VC_REQUEST_COOLDOWNS  # type: ignore
    RUNTIME_STATS = _g.RUNTIME_STATS  # type: ignore
    TICKET_LAST_ACTIVITY = _g.TICKET_LAST_ACTIVITY  # type: ignore
except Exception:
    try:
        VC_REQUESTS  # type: ignore[name-defined]
    except Exception:
        VC_REQUESTS = {}  # type: ignore

    try:
        VC_REQUEST_COOLDOWNS  # type: ignore[name-defined]
    except Exception:
        VC_REQUEST_COOLDOWNS = {}  # type: ignore

    try:
        RUNTIME_STATS  # type: ignore[name-defined]
    except Exception:
        RUNTIME_STATS = {}  # type: ignore

    try:
        TICKET_LAST_ACTIVITY  # type: ignore[name-defined]
    except Exception:
        TICKET_LAST_ACTIVITY = {}  # type: ignore


# ============================================================
# Compatibility / safety fallbacks
# Prevent NameError across versions while splitting commands.py
# ============================================================
try:
    ENABLE_BOOT_TICKET_SWEEP  # type: ignore[name-defined]
except Exception:
    ENABLE_BOOT_TICKET_SWEEP = True

try:
    SITE_URL  # type: ignore[name-defined]
except Exception:
    SITE_URL = VERIFY_SITE_URL

try:
    TOKEN_RE  # type: ignore[name-defined]
except Exception:
    TOKEN_RE = re.compile(r"\bt:([A-Za-z0-9_\-]{16,})\b")  # type: ignore[name-defined]

try:
    VC_ACCESS_TASKS  # type: ignore[name-defined]
except Exception:
    VC_ACCESS_TASKS: Dict[str, asyncio.Task] = {}

try:
    ALLOW_USER_VERIFYLINK  # type: ignore[name-defined]
except NameError:
    ALLOW_USER_VERIFYLINK = False

try:
    VC_VERIFY_ACCESS_MINUTES  # type: ignore[name-defined]
except NameError:
    VC_VERIFY_ACCESS_MINUTES = 30

try:
    STONER_ROLE_ID  # type: ignore[name-defined]
except NameError:
    STONER_ROLE_ID = None

try:
    DRUNKEN_ROLE_ID  # type: ignore[name-defined]
except NameError:
    DRUNKEN_ROLE_ID = None

try:
    ACTIVE_DECISION_PANEL_MSG_ID  # type: ignore[name-defined]
except NameError:
    ACTIVE_DECISION_PANEL_MSG_ID = {}

try:
    RECENT_SUBMISSION_TOKENS  # type: ignore[name-defined]
except NameError:
    RECENT_SUBMISSION_TOKENS = {}

try:
    RECENT_SUBMISSION_MSG_IDS  # type: ignore[name-defined]
except NameError:
    RECENT_SUBMISSION_MSG_IDS = {}

try:
    KICK_TIMER_TASKS  # type: ignore[name-defined]
except NameError:
    KICK_TIMER_TASKS = {}

try:
    KICK_TIMER_STARTS  # type: ignore[name-defined]
except NameError:
    KICK_TIMER_STARTS = {}

try:
    KICK_TIMER_STARTED_BY  # type: ignore[name-defined]
except NameError:
    KICK_TIMER_STARTED_BY = {}

try:
    PERSIST_KICK_TIMERS  # type: ignore[name-defined]
except NameError:
    PERSIST_KICK_TIMERS = False

try:
    KICK_TIMER_TABLE  # type: ignore[name-defined]
except NameError:
    KICK_TIMER_TABLE = "kick_timers"

try:
    KICK_TIMER_PERSIST_AVAILABLE  # type: ignore[name-defined]
except NameError:
    KICK_TIMER_PERSIST_AVAILABLE = True

try:
    KICK_TIMER_PERSIST_DISABLED_REASON  # type: ignore[name-defined]
except NameError:
    KICK_TIMER_PERSIST_DISABLED_REASON = None


# ============================================================
# Safe shared helpers
# ============================================================
if "_staff_check" not in globals():
    def _staff_check(interaction: discord.Interaction) -> bool:
        try:
            return isinstance(interaction.user, discord.Member) and is_staff(interaction.user)
        except Exception:
            return False


def safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def normalize_lookup_text(value: object) -> str:
    try:
        return str(value or "").strip().lower()
    except Exception:
        return ""


async def safe_defer(interaction: discord.Interaction, *, ephemeral: bool = True) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
    except Exception:
        pass


async def safe_followup(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    **kwargs: Any,
) -> None:
    payload = dict(kwargs)
    if content is not None:
        payload["content"] = content
    payload.setdefault("ephemeral", True)

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
    except Exception:
        try:
            await interaction.followup.send(**payload)
        except Exception:
            pass


async def reply_once(interaction: discord.Interaction, payload: Dict[str, Any]) -> None:
    """
    Safe reply helper: replies if possible, else followUps.
    payload example: {"content":"...", "ephemeral": True}
    """
    try:
        payload = dict(payload or {})
        payload.setdefault("ephemeral", True)

        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
    except Exception:
        try:
            await interaction.followup.send(**payload)
        except Exception:
            pass


def token_is_expired(token_info: Dict[str, Any]) -> bool:
    """
    Common expiry gate used across handlers.
    Respects 'expires_at' ISO timestamp and treats missing/invalid as expired = True.
    """
    try:
        exp_str = token_info.get("expires_at")
        exp = _parse_iso_datetime(str(exp_str) if exp_str else "")
        if not exp:
            print(
                f"⚠️ token_is_expired: no valid expires_at for token "
                f"{token_info.get('token')} (value: {exp_str!r})"
            )
            return True

        now = now_utc()
        print(
            f"🔍 token_is_expired: token={token_info.get('token')} "
            f"expires_at={exp.isoformat()} now={now.isoformat()} expired={now >= exp}"
        )
        return now >= exp
    except Exception as e:
        print(f"❌ token_is_expired error: {e}")
        return True


# Best-effort import of link builder
try:
    from ..verify_ui import build_verify_link  # type: ignore
except Exception:
    def build_verify_link(token: str) -> str:
        base = (VERIFY_SITE_URL or SITE_URL or "").strip()
        if not base:
            return token
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}token={token}"


# ---------------------------
# TASK TRACKING
# ---------------------------
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _track_task(task: Optional[asyncio.Task], label: str = "task") -> None:
    """Track background tasks so they don't get GC'd. Safe no-op if task is None."""
    try:
        if task is None:
            return

        _BACKGROUND_TASKS.add(task)

        def _done(t: asyncio.Task):
            try:
                _BACKGROUND_TASKS.discard(t)
                exc = t.exception()
                if exc:
                    print(f"⚠️ background task '{label}' raised:", repr(exc))
            except asyncio.CancelledError:
                return
            except Exception:
                pass

        task.add_done_callback(_done)
    except Exception:
        pass


def _discord_channel_url(guild_id: int, channel_id: int) -> str:
    return f"https://discord.com/channels/{int(guild_id)}/{int(channel_id)}"


# ---------------------------
# CUSTOM ID HELPERS
# ---------------------------
def make_custom_id(action: str, token: Optional[str] = None) -> str:
    """
    Verification + staff panel IDs.

    VERIFY UI:
      sv:verify:get
      sv:verify:raw
      sv:verify:regen
      sv:verify:vc
      sv:verify:reissue

    STAFF DECISIONS:
      approve
      denyclose
      resubmit

    VC STAFF FLOW:
      vc_accept
      vc_upload
      vc_approve
      vc_denyclose
      vc_end
    """
    a = (action or "").strip()
    t = (token or "").strip()

    if not a:
        return "sv:noop"
    if a.startswith("sv:verify:"):
        return f"{a}:{t}" if t else a
    return f"sv:act:{a}:{t}" if t else f"sv:act:{a}"


def parse_custom_id(custom_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (action, token) or (None, None).

    Supports:
      - Verify UI:  sv:verify:<subaction>[:<token>]
      - Actions:    sv:act:<action>[:<token>]
      - Legacy:     <action>:<token>
      - Stale/other: action appears anywhere, token is the next segment
    """
    cid = (custom_id or "").strip()
    if not cid:
        return None, None

    def _norm_action(a: Optional[str]) -> Optional[str]:
        if not a:
            return None

        x = str(a).strip().lower()
        alias = {
            "vc_start_session": "vc_start",
            "start_vc_session": "vc_start",
            "vc_session_start": "vc_start",
            "vc_done": "vc_complete",
            "vc_finish": "vc_complete",
            "vc_abort": "vc_cancel",
        }
        return alias.get(x, x)

    m = re.match(
        r"^(?:sv:)?(?:verify:)?(?:act:)?"
        r"(approve|denyclose|resubmit|vc_accept|vc_start|vc_complete|vc_cancel|"
        r"vc_upload|vc_reissue|vc_end|vc_approve|vc_denyclose|"
        r"vc_start_session|start_vc_session|vc_session_start|vc_done|vc_finish|vc_abort)"
        r"(?:[:|\-_])([A-Za-z0-9_\-]{8,})$",
        cid,
        re.I,
    )
    if m:
        return _norm_action((m.group(1) or "").strip().lower()), (m.group(2) or "").strip()

    parts = [p for p in cid.split(":") if p != ""]
    if len(parts) < 2:
        return None, None

    # sv:verify:<subaction>[:token]
    if len(parts) >= 3 and parts[0].lower() == "sv" and parts[1].lower() == "verify":
        sub = str(parts[2] or "").strip().lower()
        tok = str(parts[3] or "").strip() if len(parts) >= 4 else None
        return f"sv:verify:{sub}", tok

    # sv:act:<action>[:token]
    if len(parts) >= 3 and parts[0].lower() == "sv" and parts[1].lower() == "act":
        act = str(parts[2] or "").strip().lower()
        tok = str(parts[3] or "").strip() if len(parts) >= 4 else None
        return act, tok

    # legacy: <action>:<token>
    if len(parts) == 2:
        act = str(parts[0] or "").strip().lower()
        tok = str(parts[1] or "").strip()
        if act in (
            "approve",
            "denyclose",
            "resubmit",
            "vc_accept",
            "vc_upload",
            "vc_reissue",
            "vc_end",
            "vc_approve",
            "vc_denyclose",
        ):
            return act, tok

    # stale: action anywhere, token next segment
    known = {
        "approve",
        "denyclose",
        "resubmit",
        "vc_accept",
        "vc_upload",
        "vc_reissue",
        "vc_end",
        "vc_approve",
        "vc_denyclose",
    }
    lowered = [str(p or "").strip().lower() for p in parts]
    for i, p in enumerate(lowered):
        if p in known:
            tok = str(parts[i + 1] or "").strip() if i + 1 < len(parts) else None
            return p, tok

    return None, None


def make_mod_id(action: str, user_id: int, extra: str = "") -> str:
    """
    Staff quick-action button IDs.

    Compatible with tickets.parse_mod_id() which supports:
      - sv:mod:ban:<uid>[:extra...]
    """
    a = (action or "").strip().lower()
    uid = int(user_id)
    ex = (extra or "").strip()

    if ex:
        return f"sv:mod:{a}:{uid}:{ex}"
    return f"sv:mod:{a}:{uid}"


# ---------------------------
# TOKEN / SUBMISSION HELPERS
# ---------------------------
def extract_token_from_message(message: discord.Message) -> Optional[str]:
    """
    Detects token string from webhook submission messages and embeds.
    Accepts "t:<token>" in content/embeds.
    """
    try:
        txt = (message.content or "")
        m = TOKEN_RE.search(txt)
        if m:
            return (m.group(1) or "").strip()
    except Exception:
        pass

    try:
        for e in (message.embeds or []):
            blob = " ".join(
                [
                    str(e.title or ""),
                    str(e.description or ""),
                    " ".join(
                        [str(f.name or "") + " " + str(f.value or "") for f in (e.fields or [])]
                    ),
                ]
            )
            m = TOKEN_RE.search(blob)
            if m:
                return (m.group(1) or "").strip()
    except Exception:
        pass

    return None


def mark_ticket_activity(channel_id: int) -> None:
    """Counts as engagement for the 24h timer (button clicks, submissions, staff actions, etc.)."""
    try:
        TICKET_LAST_ACTIVITY[int(channel_id)] = now_utc()
    except Exception:
        pass


def _staff_ping_text() -> str:
    """Optional ping line for staff in ticket/queue posts."""
    try:
        vc_staff = globals().get("VC_STAFF_ROLE_ID")
        if vc_staff:
            return f"<@&{int(vc_staff)}>"
    except Exception:
        pass

    try:
        if STAFF_ROLE_ID:
            return f"<@&{STAFF_ROLE_ID}>"
    except Exception:
        pass

    return ""


# ============================================================
# Shared member target resolution helpers
# ============================================================
def parse_member_id_from_target(target: str) -> int:
    text = str(target or "").strip()
    if not text:
        return 0

    mention_match = re.search(r"<@!?(\d+)>", text)
    if mention_match:
        try:
            return int(mention_match.group(1))
        except Exception:
            return 0

    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return 0

    return 0


async def resolve_member_any(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    try:
        member = guild.get_member(int(user_id))
        if member is not None:
            return member
    except Exception:
        pass

    try:
        return await guild.fetch_member(int(user_id))
    except Exception:
        return None


def member_candidate_strings(member: discord.Member) -> List[str]:
    vals = [
        getattr(member, "name", None),
        getattr(member, "display_name", None),
        getattr(member, "global_name", None),
        str(member),
    ]
    out: List[str] = []
    for v in vals:
        norm = normalize_lookup_text(v)
        if norm and norm not in out:
            out.append(norm)
    return out


async def resolve_member_from_target(
    guild: discord.Guild,
    target: str,
) -> Optional[discord.Member]:
    raw = str(target or "").strip()
    if not raw:
        return None

    user_id = parse_member_id_from_target(raw)
    if user_id > 0:
        member = await resolve_member_any(guild, user_id)
        if member is not None:
            return member

    lowered = normalize_lookup_text(raw)
    if not lowered:
        return None

    try:
        member_list = list(guild.members or [])
    except Exception:
        member_list = []

    # exact matches first
    for member in member_list:
        try:
            if lowered in member_candidate_strings(member):
                return member
        except Exception:
            continue

    # discriminator style exact
    for member in member_list:
        try:
            tag = f"{member.name}#{member.discriminator}" if getattr(member, "discriminator", "0") != "0" else member.name
            if normalize_lookup_text(tag) == lowered:
                return member
        except Exception:
            continue

    # startswith fallback
    startswith_hits: List[discord.Member] = []
    for member in member_list:
        try:
            values = member_candidate_strings(member)
            if any(v.startswith(lowered) for v in values):
                startswith_hits.append(member)
        except Exception:
            continue

    if len(startswith_hits) == 1:
        return startswith_hits[0]

    # contains fallback
    contains_hits: List[discord.Member] = []
    for member in member_list:
        try:
            values = member_candidate_strings(member)
            if any(lowered in v for v in values):
                contains_hits.append(member)
        except Exception:
            continue

    if len(contains_hits) == 1:
        return contains_hits[0]

    # final chunk-and-retry
    try:
        await guild.chunk(cache=True)
        member_list = list(guild.members or [])
    except Exception:
        member_list = member_list

    for member in member_list:
        try:
            if lowered in member_candidate_strings(member):
                return member
        except Exception:
            continue

    return None


async def require_target_member(
    interaction: discord.Interaction,
    target: str,
    *,
    label: str = "member",
) -> Optional[discord.Member]:
    guild = interaction.guild
    if guild is None:
        await safe_followup(
            interaction,
            "❌ This command must be used in a server.",
            ephemeral=True,
        )
        return None

    member = await resolve_member_from_target(guild, target)
    if member is None:
        await safe_followup(
            interaction,
            (
                f"❌ I could not resolve that {label}.\n"
                "Use a mention, raw user ID, exact username, or exact display name."
            ),
            ephemeral=True,
        )
        return None

    return member


# ============================================================
# Per-token lock helper
# ============================================================
_LOCKS: Dict[str, asyncio.Lock] = {}


def _get_lock(key: str) -> asyncio.Lock:
    k = (key or "").strip()
    if not k:
        k = "default"

    lock = _LOCKS.get(k)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[k] = lock
    return lock


__all__ = [
    "VC_REQUESTS",
    "VC_REQUEST_COOLDOWNS",
    "RUNTIME_STATS",
    "TICKET_LAST_ACTIVITY",
    "VC_ACCESS_TASKS",
    "ACTIVE_DECISION_PANEL_MSG_ID",
    "RECENT_SUBMISSION_TOKENS",
    "RECENT_SUBMISSION_MSG_IDS",
    "KICK_TIMER_TASKS",
    "KICK_TIMER_STARTS",
    "KICK_TIMER_STARTED_BY",
    "PERSIST_KICK_TIMERS",
    "KICK_TIMER_TABLE",
    "KICK_TIMER_PERSIST_AVAILABLE",
    "KICK_TIMER_PERSIST_DISABLED_REASON",
    "ENABLE_BOOT_TICKET_SWEEP",
    "SITE_URL",
    "TOKEN_RE",
    "ALLOW_USER_VERIFYLINK",
    "VC_VERIFY_ACCESS_MINUTES",
    "STONER_ROLE_ID",
    "DRUNKEN_ROLE_ID",
    "_staff_check",
    "safe_str",
    "safe_int",
    "normalize_lookup_text",
    "safe_defer",
    "safe_followup",
    "reply_once",
    "token_is_expired",
    "build_verify_link",
    "_track_task",
    "_discord_channel_url",
    "make_custom_id",
    "parse_custom_id",
    "make_mod_id",
    "extract_token_from_message",
    "mark_ticket_activity",
    "_staff_ping_text",
    "parse_member_id_from_target",
    "resolve_member_any",
    "member_candidate_strings",
    "resolve_member_from_target",
    "require_target_member",
    "_get_lock",
]
