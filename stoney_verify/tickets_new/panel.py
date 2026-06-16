from __future__ import annotations

import asyncio
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

import discord

from ..globals import (
    RESIDENT_ROLE_ID,
    STAFF_ROLE_ID,
    TICKET_CATEGORY_ID,
    UNVERIFIED_ROLE_ID,
    VERIFIED_ROLE_ID,
    bot,
    get_supabase,
)
from ..guild_context import get_guild_context
from ..interaction_guard import run_guarded_interaction
from .repository import get_ticket_by_any_channel_id
from .service import (
    add_internal_note,
    assign_ticket,
    create_ticket_channel,
    find_open_ticket_for_owner,
    list_internal_notes,
    set_ticket_priority,
    transfer_ticket,
    unclaim_ticket,
)

try:
    from .macros_service import (
        format_available_macros_for_ticket,
        list_available_macros_for_ticket,
        send_ticket_macro,
    )
except Exception:
    async def format_available_macros_for_ticket(*args, **kwargs) -> str:  # type: ignore
        return "Macros are currently unavailable."

    async def list_available_macros_for_ticket(*args, **kwargs) -> List[Dict[str, Any]]:  # type: ignore
        return []

    async def send_ticket_macro(*args, **kwargs) -> Dict[str, Any]:  # type: ignore
        return {
            "ok": False,
            "message": "Macros are currently unavailable.",
        }

try:
    from ..transcripts import prompt_ticket_close_confirmation
except Exception:
    async def prompt_ticket_close_confirmation(*args, **kwargs):  # type: ignore
        return None


_REASON_MAX_LEN = 600
_NOTE_MAX_LEN = 4000
_DISCORD_EPHEMERAL_LIMIT = 1900
_MACRO_OPTION_LIMIT = 25

FALLBACK_SUPPORT_CATEGORY = "support"
FALLBACK_VERIFICATION_CATEGORY = "verification_issue"
FALLBACK_GHOST_CATEGORY = "ghost"
VALID_PRIORITIES = {"low", "medium", "high", "urgent"}

_PERSISTENT_VIEWS_REGISTERED = False
_CREATE_IN_PROGRESS: set[tuple[int, int]] = set()
_CREATE_IN_PROGRESS_LOCK = asyncio.Lock()
_TICKET_ENTRY_GUARD_ACTIVE: set[int] = set()

_COMMON_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "at", "by",
    "with", "from", "my", "me", "i", "im", "i'm", "is", "it", "this", "that",
    "need", "want", "help", "issue", "problem", "ticket", "please", "can",
    "could", "would", "you", "we", "our", "about", "regarding", "trying",
    "try", "into", "out", "up", "down",
}

_REASON_SYNONYM_REPLACEMENTS: Tuple[Tuple[str, str], ...] = (
    (r"\bcod\b", "call of duty"),
    (r"\bwar zone\b", "warzone"),
    (r"\blobbys\b", "lobbies"),
    (r"\bbo6\b", "black ops 6"),
    (r"\bbo3\b", "black ops 3"),
    (r"\bbo2\b", "black ops 2"),
    (r"\bbo1\b", "black ops 1"),
    (r"\bmw3\b", "modern warfare 3"),
    (r"\bmw2\b", "modern warfare 2"),
    (r"\bwaw\b", "world at war"),
    (r"\bacc\b", "account"),
    (r"\bverif\b", "verify"),
    (r"\bverification issue\b", "verification"),
    (r"\bvc\b", "voice chat"),
    (r"\bdisc\b", "discord"),
    (r"\bpls\b", "please"),
    (r"\bu\b", "you"),
)

_INTENT_KEYWORD_GROUPS: Dict[str, Tuple[str, ...]] = {
    "verification": (
        "verify", "verification", "unverified", "verified", "id", "id verify",
        "voice chat verify", "secure upload", "face pic", "selfie", "approval",
        "approve", "approved", "rejected verification",
    ),
    "appeal": (
        "appeal", "ban", "unban", "banned", "kick", "kicked", "timeout",
        "muted", "blacklisted", "punishment", "warn", "warning", "strike",
    ),
    "report": (
        "report", "reported", "scam", "scammer", "abuse", "abusive", "harass",
        "harassment", "threat", "threaten", "fraud", "fake", "impersonating",
        "spam", "spamming", "raid", "raiding", "nsfw", "rule break", "rulebreak",
    ),
    "purchase": (
        "buy", "purchase", "paid", "payment", "refund", "chargeback",
        "receipt", "order", "checkout", "invoice", "price", "pricing",
    ),
    "partnership": (
        "partner", "partnership", "collab", "collaboration", "sponsor",
        "promotion", "promo", "advertise", "advertising",
    ),
    "bug": (
        "bug", "broken", "not working", "issue", "error", "glitch", "stuck",
        "crash", "failed", "failure", "isnt working", "isn't working",
    ),
    "gaming_lobby": (
        "call of duty", "warzone", "modern warfare", "black ops", "zombies",
        "lobby", "lobbies", "bot lobby", "ranked lobby", "unlock all",
        "challenge lobby", "recovery", "recoveries", "mod menu",
    ),
    "account": (
        "account", "login", "username", "email", "password", "2fa",
        "locked out", "hacked", "compromised", "access issue",
    ),
    "vouch_referral": (
        "vouch", "vouched", "voucher", "invite", "invited", "invite credit",
        "referral", "referrer", "who invited me", "invite reward",
    ),
    "staff_complaint": (
        "staff report", "staff complaint", "mod report", "moderator report",
        "admin report", "staff abuse", "staff issue", "bad staff", "abusive mod",
    ),
    "giveaway_reward": (
        "giveaway", "reward", "prize", "claim prize", "didn't get prize",
        "missing prize", "winner issue", "reward issue",
    ),
    "content_media": (
        "content", "media", "graphic", "design", "editing", "video",
        "thumbnail", "banner", "promo art", "content request",
    ),
}


_DEFAULT_BOOTSTRAP_CATEGORIES: Tuple[Dict[str, Any], ...] = (
    {
        "name": "Verification",
        "slug": "verification_issue",
        "description": "Verification help, secure upload, VC verify, selfie, or approval issues.",
        "intake_type": "verification",
        "match_keywords": [
            "verification", "verify", "unverified", "verified", "id verification",
            "secure upload", "verify in vc", "vc verify", "face pic", "selfie",
            "approval", "verification pending", "can not verify", "cant verify",
        ],
        "button_label": "Verification",
        "sort_order": 1,
        "is_default": False,
    },
    {
        "name": "Account / Access",
        "slug": "account_access",
        "description": "Account access, login, hacked account, email, password, and 2FA issues.",
        "intake_type": "account",
        "match_keywords": [
            "account", "account access", "login", "sign in", "username", "email",
            "password", "2fa", "locked out", "hacked", "compromised", "cant login",
            "cannot login", "access issue",
        ],
        "button_label": "Account / Access",
        "sort_order": 2,
        "is_default": False,
    },
    {
        "name": "Payments / Refunds",
        "slug": "payments_refunds",
        "description": "Payments, orders, receipts, invoices, refunds, and chargebacks.",
        "intake_type": "purchase",
        "match_keywords": [
            "payment", "paid", "purchase", "refund", "chargeback", "receipt",
            "invoice", "order", "checkout", "price", "pricing", "did not receive",
            "didn't receive", "order status",
        ],
        "button_label": "Payments / Refunds",
        "sort_order": 3,
        "is_default": False,
    },
    {
        "name": "Appeals",
        "slug": "appeal",
        "description": "Appeals for bans, kicks, blacklists, warns, timeouts, and punishments.",
        "intake_type": "appeal",
        "match_keywords": [
            "appeal", "ban appeal", "unban", "kick appeal", "timeout appeal",
            "warn appeal", "blacklist appeal", "punishment appeal",
        ],
        "button_label": "Appeal",
        "sort_order": 4,
        "is_default": False,
    },
    {
        "name": "Reports",
        "slug": "report",
        "description": "User reports, scams, abuse, threats, harassment, raids, and rulebreaking.",
        "intake_type": "report",
        "match_keywords": [
            "report", "scam", "scammer", "abuse", "harassment", "threat",
            "rule break", "raid", "spam", "staff report", "impersonation",
        ],
        "button_label": "Report",
        "sort_order": 5,
        "is_default": False,
    },
    {
        "name": "Staff Complaint",
        "slug": "staff_complaint",
        "description": "Complaints or escalation requests involving staff or moderator behavior.",
        "intake_type": "report",
        "match_keywords": [
            "staff complaint", "staff issue", "staff abuse", "bad staff",
            "mod complaint", "moderator report", "admin report", "abusive mod",
        ],
        "button_label": "Staff Complaint",
        "sort_order": 6,
        "is_default": False,
    },
    {
        "name": "Bug / Technical Support",
        "slug": "technical_support",
        "description": "Site bugs, panel problems, bot issues, broken flows, and technical failures.",
        "intake_type": "bug",
        "match_keywords": [
            "bug", "broken", "not working", "error", "glitch", "failed",
            "site issue", "dashboard issue", "ticket panel broken", "technical support",
            "command bug", "upload failed",
        ],
        "button_label": "Technical Support",
        "sort_order": 7,
        "is_default": False,
    },
    {
        "name": "COD Services",
        "slug": "cod_services",
        "description": "Call of Duty lobbies, recoveries, unlock all, zombies rank, and related service requests.",
        "intake_type": "custom",
        "match_keywords": [
            "cod", "call of duty", "lobby", "lobbies", "bot lobby", "challenge lobby",
            "recovery", "recoveries", "unlock all", "warzone", "black ops",
            "modern warfare", "mw2", "mw3", "bo6", "bo3", "waw", "zombies",
            "ranked play", "camo grind",
        ],
        "button_label": "COD Services",
        "sort_order": 8,
        "is_default": False,
    },
    {
        "name": "Service Requests",
        "slug": "service_request",
        "description": "General service requests, carries, boosts, recoveries, and fulfillment questions outside COD-specific requests.",
        "intake_type": "custom",
        "match_keywords": [
            "service", "services", "boost", "boosting", "carry", "carries",
            "recovery service", "unlock service", "rank help", "service request",
        ],
        "button_label": "Service Request",
        "sort_order": 9,
        "is_default": False,
    },
    {
        "name": "Vouch / Invite / Referral",
        "slug": "vouch_referral",
        "description": "Invite credit, referral rewards, vouch issues, and who-invited-who questions.",
        "intake_type": "custom",
        "match_keywords": [
            "vouch", "vouched", "voucher", "invite", "invited", "invite credit",
            "referral", "referrer", "who invited me", "invite reward",
        ],
        "button_label": "Vouch / Referral",
        "sort_order": 10,
        "is_default": False,
    },
    {
        "name": "Giveaway / Reward Issues",
        "slug": "giveaway_reward",
        "description": "Giveaway prizes, missing rewards, winner disputes, and reward claims.",
        "intake_type": "custom",
        "match_keywords": [
            "giveaway", "reward", "prize", "claim prize", "missing prize",
            "didn't get prize", "winner issue", "reward issue",
        ],
        "button_label": "Giveaway / Reward",
        "sort_order": 11,
        "is_default": False,
    },
    {
        "name": "Content / Media Requests",
        "slug": "content_media",
        "description": "Graphics, thumbnails, banners, content requests, media edits, and promo assets.",
        "intake_type": "custom",
        "match_keywords": [
            "content", "media", "graphic", "graphics", "design", "editing",
            "video", "thumbnail", "banner", "promo art", "content request",
        ],
        "button_label": "Content / Media",
        "sort_order": 12,
        "is_default": False,
    },
    {
        "name": "Partnerships",
        "slug": "partnership",
        "description": "Partnerships, sponsorships, collaborations, and promotions.",
        "intake_type": "partnership",
        "match_keywords": [
            "partnership", "partner", "collab", "collaboration", "sponsor",
            "promotion", "promo",
        ],
        "button_label": "Partnership",
        "sort_order": 13,
        "is_default": False,
    },
    {
        "name": "Questions",
        "slug": "question",
        "description": "General questions and how-to requests.",
        "intake_type": "question",
        "match_keywords": [
            "question", "questions", "how to", "how do i", "help question",
        ],
        "button_label": "Question",
        "sort_order": 14,
        "is_default": False,
    },
    {
        "name": "Support",
        "slug": "support",
        "description": "General support fallback for anything that does not fit a more specific category.",
        "intake_type": "general",
        "match_keywords": [
            "support", "help", "general support", "assistance",
        ],
        "button_label": "Support",
        "sort_order": 999,
        "is_default": True,
    },
)


def _debug(msg: str) -> None:
    try:
        print(f"🧩 ticket_panel {msg}")
    except Exception:
        pass


def _safe_int(v: object, default: int = 0) -> int:
    try:
        return int(str(v or "0").strip())
    except Exception:
        return default


def _normalize_text(text: str, *, limit: int = _REASON_MAX_LEN) -> str:
    try:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        return cleaned[:limit]
    except Exception:
        return ""


def _normalize_multiline_text(text: str, *, limit: int = _NOTE_MAX_LEN) -> str:
    try:
        cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        return cleaned[:limit]
    except Exception:
        return ""


def _slugify(value: str) -> str:
    try:
        return (
            str(value or "")
            .strip()
            .lower()
            .replace("'", "")
            .replace('"', "")
            .replace("&", " and ")
        )
    except Exception:
        return ""


def _canonicalize_reason_text(text: str) -> str:
    value = _slugify(_normalize_text(text, limit=_REASON_MAX_LEN))
    for pattern, replacement in _REASON_SYNONYM_REPLACEMENTS:
        try:
            value = re.sub(pattern, replacement, value, flags=re.I)
        except Exception:
            continue
    value = re.sub(r"[^a-z0-9\s/_-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _tokenize_text(text: str) -> List[str]:
    cleaned = _canonicalize_reason_text(text)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return [part for part in cleaned.split() if part]


def _token_set(text: str) -> set[str]:
    return {t for t in _tokenize_text(text) if t and t not in _COMMON_STOPWORDS}


def _truncate(text: str, limit: int = 280) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _chunk_text_for_discord(text: str, limit: int = _DISCORD_EPHEMERAL_LIMIT) -> List[str]:
    raw = str(text or "")
    if not raw:
        return [""]

    chunks: List[str] = []
    remaining = raw

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit

        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]

        chunks.append(chunk)
        remaining = remaining[len(chunk):].lstrip()

    return chunks or [""]


async def _safe_channel_send(
    channel: discord.TextChannel,
    content: Optional[str] = None,
    **kwargs: Any,
) -> Optional[discord.Message]:
    try:
        kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())
        return await channel.send(content=content, **kwargs)
    except Exception:
        return None


async def _run_ticket_panel_action(
    interaction: discord.Interaction,
    action: Any,
    label: str,
) -> None:
    async def _runner() -> None:
        await action()

    result = await run_guarded_interaction(
        interaction,
        _runner,
        defer=False,
        ephemeral=True,
        error_title="❌ Ticket action failed",
        error_guidance=(
            f"Nothing was changed. Try **{label}** again. "
            "If it keeps happening, run `/dank diagnostics` and check ticket logs."
        ),
    )

    if not result.ok:
        _debug(
            f"guarded ticket action failed label={label!r} "
            f"type={result.error_type!r} message={result.error_message!r}"
        )


async def _run_ticket_entry_callback(
    interaction: discord.Interaction,
    action: Any,
    label: str,
) -> None:
    key = id(interaction)
    if key in _TICKET_ENTRY_GUARD_ACTIVE:
        await action()
        return

    _TICKET_ENTRY_GUARD_ACTIVE.add(key)
    try:
        await _run_ticket_panel_action(interaction, action, label)
    finally:
        _TICKET_ENTRY_GUARD_ACTIVE.discard(key)


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    try:
        if not role_id:
            return False
        return any(int(r.id) == int(role_id) for r in (member.roles or []))
    except Exception:
        return False


def _is_staff_member(member: discord.Member) -> bool:
    try:
        if member.guild_permissions.administrator:
            return True
        if member.guild_permissions.manage_channels:
            return True
        if member.guild_permissions.manage_guild:
            return True

        staff_role_id = _safe_int(STAFF_ROLE_ID, 0)
        if staff_role_id and any(int(r.id) == staff_role_id for r in member.roles):
            return True

        return False
    except Exception:
        return False


def _is_unverified_only_user(member: discord.Member) -> bool:
    try:
        if getattr(member, "bot", False):
            return False

        uv_id = _safe_int(UNVERIFIED_ROLE_ID, 0)
        verified_id = _safe_int(VERIFIED_ROLE_ID, 0)
        resident_id = _safe_int(RESIDENT_ROLE_ID, 0)
        staff_id = _safe_int(STAFF_ROLE_ID, 0)

        if staff_id and _member_has_role_id(member, staff_id):
            return False
        if verified_id and _member_has_role_id(member, verified_id):
            return False
        if resident_id and _member_has_role_id(member, resident_id):
            return False
        if uv_id and _member_has_role_id(member, uv_id):
            return True

        return False
    except Exception:
        return False


def _staff_role_ids_for_ticket(guild: discord.Guild) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []

    def _maybe_add(v: object) -> None:
        rid = _safe_int(v, 0)
        if rid <= 0 or rid in seen:
            return
        if guild.get_role(rid) is None:
            return
        seen.add(rid)
        out.append(rid)

    _maybe_add(STAFF_ROLE_ID)

    try:
        from .. import globals as _g  # type: ignore

        for attr in (
            "SUPPORT_ROLE_ID",
            "MOD_ROLE_ID",
            "MODERATOR_ROLE_ID",
            "ADMIN_ROLE_ID",
            "HELPER_ROLE_ID",
            "TRIAL_MOD_ROLE_ID",
        ):
            _maybe_add(getattr(_g, attr, 0))
    except Exception:
        pass

    return out


def _ticket_parent_category_id() -> Optional[int]:
    cid = _safe_int(TICKET_CATEGORY_ID, 0)
    return cid if cid > 0 else None


async def _ticket_panel_guild_context(guild: discord.Guild) -> Any:
    try:
        return await get_guild_context(int(guild.id), refresh=True)
    except Exception as exc:
        _debug(f"guild context unavailable for ticket panel guild={guild.id}: {repr(exc)}")
        return None


def _context_id(context: Any, key: str) -> int:
    try:
        if context is None:
            return 0
        value = context.get_id(key, 0)
        return _safe_int(value, 0)
    except Exception:
        return 0


def _ticket_parent_category_id_from_context(
    guild: discord.Guild,
    context: Any,
) -> Optional[int]:
    cid = _context_id(context, "ticket_category_id")
    if cid > 0:
        try:
            if isinstance(guild.get_channel(cid), discord.CategoryChannel):
                return cid
        except Exception:
            pass

    return _ticket_parent_category_id()


def _staff_role_ids_for_ticket_from_context(
    guild: discord.Guild,
    context: Any,
) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []

    def _maybe_add(value: object) -> None:
        rid = _safe_int(value, 0)
        if rid <= 0 or rid in seen:
            return
        if guild.get_role(rid) is None:
            return
        seen.add(rid)
        out.append(rid)

    for key in (
        "staff_role_id",
        "ticket_staff_role_id",
        "support_role_id",
        "mod_role_id",
        "moderator_role_id",
    ):
        _maybe_add(_context_id(context, key))

    if out:
        return out

    return _staff_role_ids_for_ticket(guild)


def _channel_looks_closed(channel: discord.TextChannel) -> bool:
    try:
        return str(channel.name or "").lower().startswith("closed-")
    except Exception:
        return False


def _ticket_status_from_row(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return "unknown"
    raw = str(row.get("status") or "unknown").strip().lower()
    if raw in {"open", "claimed", "closed", "deleted"}:
        return raw
    if raw in {"active", "reopened"}:
        return "open"
    return "unknown"


def _ticket_is_deleted(row: Optional[Dict[str, Any]]) -> bool:
    return _ticket_status_from_row(row) == "deleted"


def _ticket_is_closed_or_stale_closed(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> bool:
    status = _ticket_status_from_row(row)
    return status == "closed" or _channel_looks_closed(channel)


def _ticket_is_open_like(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> bool:
    status = _ticket_status_from_row(row)
    if status in {"open", "claimed"} and not _channel_looks_closed(channel):
        return True
    return False


def _open_panel_state_error(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> str:
    status = _ticket_status_from_row(row)
    if status == "deleted":
        return "❌ This ticket is already deleted."
    if _ticket_is_closed_or_stale_closed(channel, row):
        return "❌ These are stale open-ticket controls. Use the closed-ticket controls instead."
    return "❌ This ticket is not in an active state for that action."


async def _safe_defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass


async def _safe_followup(interaction: discord.Interaction, content: str) -> None:
    chunks = _chunk_text_for_discord(content)

    for index, chunk in enumerate(chunks):
        try:
            if index == 0 and not interaction.response.is_done():
                await interaction.response.send_message(
                    chunk,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            else:
                await interaction.followup.send(
                    chunk,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception:
            pass


async def _begin_ticket_create_attempt(guild_id: int, user_id: int) -> bool:
    key = (int(guild_id), int(user_id))
    async with _CREATE_IN_PROGRESS_LOCK:
        if key in _CREATE_IN_PROGRESS:
            return False
        _CREATE_IN_PROGRESS.add(key)
        return True


async def _finish_ticket_create_attempt(guild_id: int, user_id: int) -> None:
    key = (int(guild_id), int(user_id))
    async with _CREATE_IN_PROGRESS_LOCK:
        _CREATE_IN_PROGRESS.discard(key)


def _resolve_member(interaction: discord.Interaction) -> Optional[discord.Member]:
    user = interaction.user
    guild = interaction.guild

    if guild is None:
        return None

    if isinstance(user, discord.Member):
        return user

    try:
        return guild.get_member(int(user.id))
    except Exception:
        return None


def _extract_user_id_from_text(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0

    mention_match = re.search(r"<@!?(\d+)>", text)
    if mention_match:
        return _safe_int(mention_match.group(1), 0)

    raw_digits = re.sub(r"[^\d]", "", text)
    if raw_digits:
        return _safe_int(raw_digits, 0)

    return 0


async def _resolve_existing_open_ticket_channel(
    *,
    guild: discord.Guild,
    owner_id: int,
) -> Optional[discord.TextChannel]:
    try:
        existing = await find_open_ticket_for_owner(
            guild_id=guild.id,
            owner_id=owner_id,
            category=None,
        )
    except Exception as e:
        _debug(f"existing-ticket lookup failed guild={guild.id} owner={owner_id} error={repr(e)}")
        existing = None

    if not existing:
        return None

    try:
        ch_id_raw = existing.get("discord_thread_id") or existing.get("channel_id")
        ch_id = int(str(ch_id_raw or "0") or 0)
    except Exception:
        ch_id = 0

    if ch_id <= 0:
        _debug(f"existing-ticket row found but invalid channel id guild={guild.id} owner={owner_id}")
        return None

    try:
        ch = guild.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            _debug(f"existing-ticket cache hit guild={guild.id} owner={owner_id} channel={ch.id}")
            return ch
    except Exception:
        pass

    try:
        fetched = await guild.fetch_channel(ch_id)
        if isinstance(fetched, discord.TextChannel):
            _debug(f"existing-ticket fetch hit guild={guild.id} owner={owner_id} channel={fetched.id}")
            return fetched
    except Exception as e:
        _debug(f"existing-ticket fetch failed guild={guild.id} owner={owner_id} channel={ch_id} error={repr(e)}")

    return None


def _normalize_keywords(value: Any) -> List[str]:
    out: List[str] = []
    try:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_items = str(value or "").split(",")

        for item in raw_items:
            text = _canonicalize_reason_text(str(item or ""))
            if text and text not in out:
                out.append(text)
    except Exception:
        pass
    return out


def _keyword_variants(phrase: str) -> List[str]:
    base = _canonicalize_reason_text(phrase)
    if not base:
        return []

    variants: set[str] = {base}

    if "lobbies" in base:
        variants.add(base.replace("lobbies", "lobby"))
        variants.add(base.replace("lobbies", "lobbys"))
    if "lobby" in base:
        variants.add(base.replace("lobby", "lobbies"))
        variants.add(base.replace("lobby", "lobbys"))

    if "call of duty" in base:
        variants.add(base.replace("call of duty", "cod"))

    if "black ops" in base:
        variants.add(base.replace("black ops", "bo"))

    if "modern warfare" in base:
        variants.add(base.replace("modern warfare", "mw"))

    return [v for v in variants if v]


def _normalize_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    slug = _canonicalize_reason_text(str(row.get("slug") or ""))
    name = _normalize_text(str(row.get("name") or ""), limit=200)
    description = _normalize_text(str(row.get("description") or ""), limit=500)
    intake_type = _normalize_text(str(row.get("intake_type") or ""), limit=80).lower()

    normalized_keywords = _normalize_keywords(row.get("match_keywords"))
    slug_text = slug.replace("-", " ").replace("_", " ")
    name_text = _canonicalize_reason_text(name)
    description_text = _canonicalize_reason_text(description)

    aliases: List[str] = []
    aliases.extend(_keyword_variants(slug_text))
    aliases.extend(_keyword_variants(name_text))
    aliases.extend(_keyword_variants(description_text))

    for keyword in normalized_keywords:
        aliases.extend(_keyword_variants(keyword))

    for intent_name, words in _INTENT_KEYWORD_GROUPS.items():
        haystack = f"{slug_text} {name_text} {description_text} {' '.join(normalized_keywords)} {intake_type}"
        if (
            intent_name in haystack
            or any(word in haystack for word in words)
        ):
            aliases.extend(words)

    deduped_aliases: List[str] = []
    seen: set[str] = set()
    for alias in aliases:
        cleaned = _canonicalize_reason_text(alias)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped_aliases.append(cleaned)

    return {
        "id": row.get("id"),
        "guild_id": str(row.get("guild_id") or ""),
        "slug": slug,
        "name": name,
        "description": description,
        "intake_type": intake_type,
        "match_keywords": normalized_keywords,
        "match_aliases": deduped_aliases,
        "is_default": bool(row.get("is_default", False)),
        "sort_order": row.get("sort_order"),
    }


def _bootstrap_categories_payload_for_guild(guild_id: int) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for row in _DEFAULT_BOOTSTRAP_CATEGORIES:
        out = dict(row)
        out["guild_id"] = str(guild_id)
        payload.append(out)
    return payload


def _seed_dashboard_ticket_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
    sb = None
    try:
        sb = get_supabase()
    except Exception:
        sb = None

    if not sb:
        _debug(f"category bootstrap skipped; supabase unavailable guild={guild_id}")
        return []

    payload = _bootstrap_categories_payload_for_guild(guild_id)
    inserted = 0

    for row in payload:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue

        try:
            existing_res = (
                sb.table("ticket_categories")
                .select("id, slug")
                .eq("guild_id", str(guild_id))
                .eq("slug", slug)
                .limit(1)
                .execute()
            )
            existing_rows = getattr(existing_res, "data", None) or []
            if existing_rows:
                continue
        except Exception as e:
            print(
                f"⚠️ ticket category bootstrap existing-check failed "
                f"guild={guild_id} slug={slug} error={repr(e)}"
            )
            continue

        try:
            sb.table("ticket_categories").insert(row).execute()
            inserted += 1
        except Exception as e:
            print(
                f"⚠️ ticket category bootstrap insert failed "
                f"guild={guild_id} slug={slug} error={repr(e)}"
            )

    _debug(f"category bootstrap guild={guild_id} inserted={inserted}")
    return _fetch_dashboard_ticket_categories_sync(guild_id, allow_bootstrap=False)


def _fetch_dashboard_ticket_categories_sync(
    guild_id: int,
    *,
    allow_bootstrap: bool = True,
) -> List[Dict[str, Any]]:
    sb = None
    try:
        sb = get_supabase()
    except Exception:
        sb = None

    if not sb:
        _debug(f"category fetch skipped; supabase unavailable guild={guild_id}")
        return []

    try:
        res = (
            sb.table("ticket_categories")
            .select("*")
            .eq("guild_id", str(guild_id))
            .execute()
        )
        rows = getattr(res, "data", None) or []
    except Exception as e:
        print(f"⚠️ ticket category fetch failed for guild={guild_id}: {repr(e)}")
        return []

    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            normalized.append(_normalize_category_row(row))

    try:
        normalized.sort(
            key=lambda c: (
                c.get("sort_order") is None,
                c.get("sort_order") if c.get("sort_order") is not None else 10_000,
                str(c.get("name") or "").lower(),
            )
        )
    except Exception:
        pass

    _debug(f"category fetch guild={guild_id} count={len(normalized)}")

    if normalized or not allow_bootstrap:
        return normalized

    _debug(f"category fetch empty -> bootstrap guild={guild_id}")
    return _seed_dashboard_ticket_categories_sync(guild_id)


async def _fetch_dashboard_ticket_categories(guild_id: int) -> List[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_fetch_dashboard_ticket_categories_sync, guild_id)
    except Exception as e:
        print(f"⚠️ async ticket category fetch failed for guild={guild_id}: {repr(e)}")
        return []


def _find_verification_category_slug(categories: List[Dict[str, Any]]) -> str:
    candidates: List[Dict[str, Any]] = []

    for cat in categories:
        slug = str(cat.get("slug") or "")
        name = str(cat.get("name") or "").lower()
        intake_type = str(cat.get("intake_type") or "").lower()

        if intake_type == "verification":
            candidates.append(cat)
            continue

        if slug in {"verification", "verification-issue", "verification_issue"}:
            candidates.append(cat)
            continue

        if "verification" in name:
            candidates.append(cat)
            continue

    if candidates:
        return str(candidates[0].get("slug") or FALLBACK_VERIFICATION_CATEGORY)

    return FALLBACK_VERIFICATION_CATEGORY


def _find_default_category_slug(categories: List[Dict[str, Any]]) -> str:
    for cat in categories:
        if bool(cat.get("is_default")):
            return str(cat.get("slug") or FALLBACK_SUPPORT_CATEGORY)

    for cat in categories:
        slug = str(cat.get("slug") or "")
        if slug in {"support", "general-support", "general_support"}:
            return slug

    return FALLBACK_SUPPORT_CATEGORY


def _find_category_label_by_slug(
    categories: List[Dict[str, Any]],
    slug: str,
    fallback: str,
) -> str:
    slug_clean = _canonicalize_reason_text(slug)
    for cat in categories:
        if _canonicalize_reason_text(str(cat.get("slug") or "")) == slug_clean:
            label = str(cat.get("name") or "").strip()
            if label:
                return label
    return fallback


def _find_category_row_by_slug(
    categories: List[Dict[str, Any]],
    slug: str,
) -> Optional[Dict[str, Any]]:
    slug_clean = _canonicalize_reason_text(slug)
    for cat in categories:
        if _canonicalize_reason_text(str(cat.get("slug") or "")) == slug_clean:
            return cat
    return None


def _phrase_hits(reason_norm: str, aliases: List[str]) -> int:
    score = 0
    padded_reason = f" {reason_norm} "
    for alias in aliases:
        for variant in _keyword_variants(alias):
            if not variant:
                continue
            if f" {variant} " in padded_reason:
                score += 18 if len(variant.split()) > 1 else 10
            elif variant in reason_norm:
                score += 8
    return score


def _token_overlap_score(reason_tokens: set[str], category_tokens: set[str]) -> int:
    if not reason_tokens or not category_tokens:
        return 0

    overlap = reason_tokens & category_tokens
    if not overlap:
        return 0

    score = 0
    for token in overlap:
        if len(token) >= 6:
            score += 7
        elif len(token) >= 4:
            score += 4
        else:
            score += 2
    return score


def _fuzzy_token_score(reason_tokens: set[str], category_tokens: set[str]) -> int:
    if not reason_tokens or not category_tokens:
        return 0

    score = 0
    for rt in reason_tokens:
        if len(rt) < 4:
            continue
        for ct in category_tokens:
            if len(ct) < 4:
                continue
            try:
                ratio = SequenceMatcher(None, rt, ct).ratio()
            except Exception:
                ratio = 0.0
            if ratio >= 0.92:
                score += 5
                break
            if ratio >= 0.84:
                score += 2
                break
    return score


def _reason_intent_hits(reason_norm: str) -> Dict[str, int]:
    hits: Dict[str, int] = {}
    for intent_name, words in _INTENT_KEYWORD_GROUPS.items():
        score = 0
        for word in words:
            canonical = _canonicalize_reason_text(word)
            if not canonical:
                continue
            if canonical in reason_norm:
                score += 1
        hits[intent_name] = score
    return hits


def _category_intent_score(reason_norm: str, cat: Dict[str, Any]) -> int:
    intent_hits = _reason_intent_hits(reason_norm)
    haystack = " ".join(
        [
            _canonicalize_reason_text(str(cat.get("slug") or "")),
            _canonicalize_reason_text(str(cat.get("name") or "")),
            _canonicalize_reason_text(str(cat.get("description") or "")),
            _canonicalize_reason_text(str(cat.get("intake_type") or "")),
            " ".join([_canonicalize_reason_text(x) for x in (cat.get("match_keywords") or [])]),
            " ".join([_canonicalize_reason_text(x) for x in (cat.get("match_aliases") or [])]),
        ]
    )

    score = 0
    for intent_name, hit_count in intent_hits.items():
        if hit_count <= 0:
            continue

        if intent_name == "gaming_lobby":
            if any(
                marker in haystack
                for marker in (
                    "call of duty", "cod", "warzone", "lobby", "lobbies",
                    "challenge lobby", "recovery", "recoveries", "mod menu",
                    "modern warfare", "black ops", "zombies",
                )
            ):
                score += 45 + (hit_count * 8)
                continue

        if intent_name in haystack:
            score += 25 + (hit_count * 4)
            continue

        intent_words = _INTENT_KEYWORD_GROUPS.get(intent_name, ())
        if any(word in haystack for word in intent_words):
            score += 18 + (hit_count * 3)

    return score


def _support_penalty(reason_norm: str, cat: Dict[str, Any]) -> int:
    slug = _canonicalize_reason_text(str(cat.get("slug") or ""))
    name = _canonicalize_reason_text(str(cat.get("name") or ""))
    intake_type = _canonicalize_reason_text(str(cat.get("intake_type") or ""))
    if slug not in {"support", "general support", "general-support", "general_support"} and "support" not in name:
        return 0

    hits = _reason_intent_hits(reason_norm)
    strong_other_intent = (
        hits.get("gaming_lobby", 0) >= 1
        or hits.get("verification", 0) >= 2
        or hits.get("appeal", 0) >= 1
        or hits.get("report", 0) >= 1
        or hits.get("purchase", 0) >= 1
        or hits.get("partnership", 0) >= 1
        or hits.get("account", 0) >= 2
    )
    if strong_other_intent:
        return 22

    if intake_type == "question" and hits.get("bug", 0) >= 1:
        return 5

    return 0


def _score_reason_against_category(reason: str, cat: Dict[str, Any]) -> int:
    reason_norm = _canonicalize_reason_text(reason)
    reason_tokens = _token_set(reason_norm)

    slug_text = _canonicalize_reason_text(str(cat.get("slug") or "").replace("-", " ").replace("_", " "))
    name_text = _canonicalize_reason_text(str(cat.get("name") or ""))
    desc_text = _canonicalize_reason_text(str(cat.get("description") or ""))
    intake_type = _canonicalize_reason_text(str(cat.get("intake_type") or ""))
    keywords = [_canonicalize_reason_text(x) for x in (cat.get("match_keywords") or [])]
    aliases = [_canonicalize_reason_text(x) for x in (cat.get("match_aliases") or [])]

    category_tokens = set()
    for source_text in [slug_text, name_text, desc_text, intake_type, *keywords, *aliases]:
        category_tokens.update(_token_set(source_text))

    score = 0

    score += _phrase_hits(reason_norm, aliases)
    score += _phrase_hits(reason_norm, keywords)

    if slug_text and slug_text in reason_norm:
        score += 14
    if name_text and name_text in reason_norm:
        score += 12
    if intake_type and intake_type in reason_norm:
        score += 10

    score += _token_overlap_score(reason_tokens, category_tokens)
    score += _fuzzy_token_score(reason_tokens, category_tokens)
    score += _category_intent_score(reason_norm, cat)

    if bool(cat.get("is_default")):
        score += 2

    score -= _support_penalty(reason_norm, cat)

    return max(score, 0)


def _build_match_payload(
    *,
    matched_row: Optional[Dict[str, Any]],
    matched_score: Optional[int],
    matched_reason: str,
    category_slug: str,
) -> Dict[str, Any]:
    return {
        "matched_category_id": str(matched_row.get("id")) if matched_row and matched_row.get("id") is not None else None,
        "matched_category_name": str(matched_row.get("name") or "").strip() if matched_row else None,
        "matched_category_slug": str(matched_row.get("slug") or category_slug).strip() if matched_row else category_slug,
        "matched_intake_type": str(matched_row.get("intake_type") or "").strip() if matched_row else None,
        "matched_category_reason": _truncate(matched_reason, 500) if matched_reason else None,
        "matched_category_score": int(matched_score) if matched_score is not None else None,
        "category_override": False,
        "category_id": str(matched_row.get("id")) if matched_row and matched_row.get("id") is not None else None,
    }


async def _infer_dashboard_category(
    *,
    guild_id: int,
    reason: str,
) -> Tuple[str, str, List[Dict[str, Any]], Dict[str, Any]]:
    categories = await _fetch_dashboard_ticket_categories(guild_id)
    reason_norm = _canonicalize_reason_text(reason)

    if not categories:
        _debug(f"category infer fallback=support guild={guild_id} reason_len={len(reason or '')}")
        return (
            FALLBACK_SUPPORT_CATEGORY,
            "Support",
            [],
            _build_match_payload(
                matched_row=None,
                matched_score=0,
                matched_reason=f"No dashboard ticket categories found; fallback to {FALLBACK_SUPPORT_CATEGORY}.",
                category_slug=FALLBACK_SUPPORT_CATEGORY,
            ),
        )

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for cat in categories:
        score = _score_reason_against_category(reason_norm, cat)
        scored.append((score, cat))

    scored.sort(
        key=lambda item: (
            item[0],
            1 if bool(item[1].get("is_default")) else 0,
            -(item[1].get("sort_order") if isinstance(item[1].get("sort_order"), int) else 10_000),
        ),
        reverse=True,
    )

    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0

    if best is not None and best_score >= 14:
        if best_score >= second_score + 5 or best_score >= 24:
            _debug(
                f"category infer matched guild={guild_id} "
                f"slug={best.get('slug')} label={best.get('name')} "
                f"score={best_score} second={second_score} reason={_truncate(reason_norm, 140)!r}"
            )
            return (
                str(best.get("slug") or FALLBACK_SUPPORT_CATEGORY),
                str(best.get("name") or "Support"),
                categories,
                _build_match_payload(
                    matched_row=best,
                    matched_score=best_score,
                    matched_reason=(
                        f"Matched from reason text using category scoring. "
                        f"best_score={best_score}, second_score={second_score}, "
                        f"reason={_truncate(reason_norm, 220)!r}"
                    ),
                    category_slug=str(best.get("slug") or FALLBACK_SUPPORT_CATEGORY),
                ),
            )

    default_slug = _find_default_category_slug(categories)
    default_cat = next((c for c in categories if str(c.get("slug") or "") == default_slug), None)

    if default_cat is not None:
        _debug(
            f"category infer default guild={guild_id} "
            f"slug={default_cat.get('slug')} label={default_cat.get('name')} "
            f"best_score={best_score} second={second_score} reason={_truncate(reason_norm, 140)!r}"
        )
        return (
            str(default_cat.get("slug") or FALLBACK_SUPPORT_CATEGORY),
            str(default_cat.get("name") or "Support"),
            categories,
            _build_match_payload(
                matched_row=default_cat,
                matched_score=best_score,
                matched_reason=(
                    f"No strong category winner. Fell back to default category. "
                    f"best_score={best_score}, second_score={second_score}, "
                    f"reason={_truncate(reason_norm, 220)!r}"
                ),
                category_slug=str(default_cat.get("slug") or FALLBACK_SUPPORT_CATEGORY),
            ),
        )

    _debug(f"category infer fallback=no-default guild={guild_id}")
    return (
        FALLBACK_SUPPORT_CATEGORY,
        "Support",
        categories,
        _build_match_payload(
            matched_row=None,
            matched_score=best_score,
            matched_reason=(
                f"No default dashboard category found. "
                f"Fallback to {FALLBACK_SUPPORT_CATEGORY}. "
                f"best_score={best_score}, second_score={second_score}, "
                f"reason={_truncate(reason_norm, 220)!r}"
            ),
            category_slug=FALLBACK_SUPPORT_CATEGORY,
        ),
    )


def _opening_message_for_category(
    *,
    user: discord.Member,
    category: str,
    category_label: str,
    reason: str = "",
    is_ghost: bool = False,
) -> str:
    ghost_prefix = "👻 Ghost Test Ticket\n\n" if is_ghost else ""

    if category in {"verification", "verification-issue", "verification_issue"}:
        return (
            f"{ghost_prefix}{user.mention} Welcome\n\n"
            "Please complete verification using the panel below.\n"
            "You can use **Get Secure Upload** or **Verify in VC**."
        )

    lines = [
        f"{ghost_prefix}{user.mention} Welcome",
        "",
        f"Ticket category: **{category_label}**",
    ]

    cleaned_reason = _normalize_text(reason)
    if cleaned_reason:
        lines.extend(
            [
                "",
                "**Reason provided:**",
                cleaned_reason,
            ]
        )

    if is_ghost:
        lines.extend(
            [
                "",
                "This is a staff ghost/test ticket using the matched category flow.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Support will be with you shortly.",
            ]
        )

    return "\n".join(lines)


async def _create_ticket_for_member(
    *,
    interaction: discord.Interaction,
    guild: discord.Guild,
    user: discord.Member,
    category: str,
    category_label: str,
    reason: str = "",
    source: str,
    is_ghost: bool = False,
    match_payload: Optional[Dict[str, Any]] = None,
) -> None:
    reason_preview = _normalize_text(reason, limit=120)
    _debug(
        f"create request guild={guild.id} user={user.id} "
        f"category={category} label={category_label!r} source={source} "
        f"ghost={is_ghost} reason={reason_preview!r} match={match_payload!r}"
    )

    if not await _begin_ticket_create_attempt(guild.id, user.id):
        await _safe_followup(
            interaction,
            "⏳ Your ticket is already being created. Please wait a moment instead of pressing Create Ticket again.",
        )
        return

    try:
        existing_channel = await _resolve_existing_open_ticket_channel(
            guild=guild,
            owner_id=user.id,
        )

        if existing_channel is not None:
            _debug(
                f"create blocked existing-open-ticket guild={guild.id} "
                f"user={user.id} channel={existing_channel.id}"
            )
            await _safe_followup(
                interaction,
                f"You already have an open ticket: {existing_channel.mention}",
            )
            return

        guild_context = await _ticket_panel_guild_context(guild)
        parent_category_id = _ticket_parent_category_id_from_context(guild, guild_context)
        staff_role_ids = _staff_role_ids_for_ticket_from_context(guild, guild_context)

        _debug(
            f"create resolved parent_category_id={parent_category_id} "
            f"staff_role_ids={staff_role_ids} guild={guild.id} user={user.id}"
        )

        opening_message = _opening_message_for_category(
            user=user,
            category=category,
            category_label=category_label,
            reason=reason,
            is_ghost=is_ghost,
        )

        payload = dict(match_payload or {})

        channel = await create_ticket_channel(
            guild=guild,
            owner=user,
            category=category,
            source=source,
            is_ghost=is_ghost,
            parent_category_id=parent_category_id,
            staff_role_ids=staff_role_ids,
            opening_message=opening_message,
            priority="low" if is_ghost else "medium",
            matched_category_id=payload.get("matched_category_id"),
            matched_category_name=payload.get("matched_category_name"),
            matched_category_slug=payload.get("matched_category_slug"),
            matched_intake_type=payload.get("matched_intake_type"),
            matched_category_reason=payload.get("matched_category_reason"),
            matched_category_score=payload.get("matched_category_score"),
            category_override=bool(payload.get("category_override", False)),
            category_id=payload.get("category_id"),
        )

        if channel is None:
            _debug(
                f"create failed guild={guild.id} user={user.id} "
                f"category={category} source={source} ghost={is_ghost}"
            )
            await _safe_followup(interaction, "Failed to create ticket.")
            return

        _debug(
            f"create success guild={guild.id} user={user.id} "
            f"channel={channel.id} name={channel.name!r} category={category} "
            f"source={source} matched_slug={payload.get('matched_category_slug')!r}"
        )

        await _safe_followup(
            interaction,
            f"Ticket created: {channel.mention}",
        )
    finally:
        await _finish_ticket_create_attempt(guild.id, user.id)


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    try:
        row = await get_ticket_by_any_channel_id(channel.id)
        if isinstance(row, dict):
            return row
        return None
    except Exception as e:
        _debug(f"ticket-row lookup failed channel={channel.id} error={repr(e)}")
        return None


def _claimed_by_id_from_row(row: Optional[Dict[str, Any]]) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("assigned_to", "claimed_by"):
        try:
            value = int(str(row.get(key) or "0") or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _priority_from_row(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return "medium"
    value = str(row.get("priority") or "medium").strip().lower()
    return value if value in VALID_PRIORITIES else "medium"


def _owner_id_from_row(row: Optional[Dict[str, Any]]) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("owner_id", "ticket_owner_id", "user_id", "member_id", "created_by"):
        value = _safe_int(row.get(key), 0)
        if value > 0:
            return value
    return 0


def _ticket_number_from_row(row: Optional[Dict[str, Any]]) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("ticket_number", "number"):
        value = _safe_int(row.get(key), 0)
        if value > 0:
            return value
    return 0


def _ticket_category_name_from_row(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return "Unknown"
    for key in ("matched_category_name", "category_name", "category"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return "Unknown"


def _ticket_summary_text(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> str:
    status = _ticket_status_from_row(row)
    claimed_by = _claimed_by_id_from_row(row)
    owner_id = _owner_id_from_row(row)
    ticket_number = _ticket_number_from_row(row)
    priority = _priority_from_row(row)
    category_name = _ticket_category_name_from_row(row)

    owner_text = f"<@{owner_id}>" if owner_id > 0 else "Unknown"
    claimed_text = f"<@{claimed_by}>" if claimed_by > 0 else "Nobody"
    panel_state = "Open controls" if _ticket_is_open_like(channel, row) else "Closed / stale controls"

    lines = [
        f"**Ticket Channel:** {channel.mention}",
        f"**Ticket Number:** `{ticket_number}`" if ticket_number > 0 else "**Ticket Number:** `Unknown`",
        f"**Status:** `{status}`",
        f"**Category:** `{category_name}`",
        f"**Priority:** `{priority}`",
        f"**Owner:** {owner_text}",
        f"**Claimed By:** {claimed_text}",
        f"**Panel State:** `{panel_state}`",
    ]

    matched_slug = str((row or {}).get("matched_category_slug") or "").strip()
    if matched_slug:
        lines.append(f"**Matched Category Slug:** `{matched_slug}`")

    return "\n".join(lines)


def _format_notes_for_display(notes: List[Dict[str, Any]], limit: int = 8) -> str:
    if not notes:
        return "No internal notes found."

    lines: List[str] = []
    for idx, row in enumerate(notes[:limit], start=1):
        author_name = _truncate(str(row.get("author_name") or row.get("author_id") or "Unknown"), 50)
        note_body = _truncate(str(row.get("note_body") or ""), 220)
        created_at = _truncate(str(row.get("created_at") or ""), 40)
        pinned = "📌 " if bool(row.get("is_pinned", False)) else ""
        lines.append(f"**{idx}.** {pinned}{author_name} — `{created_at}`\n{note_body}")

    return "\n\n".join(lines)


def _macro_option_label(row: Dict[str, Any]) -> str:
    name = _truncate(str(row.get("name") or row.get("slug") or "Macro"), 100)
    return name or "Macro"


def _macro_option_description(row: Dict[str, Any]) -> str:
    category = str(row.get("category") or "all").strip() or "all"
    source = str(row.get("_source") or "unknown").strip() or "unknown"
    desc = f"{category} • {source}"
    if bool(row.get("send_as_note", False)):
        desc += " • note"
    return _truncate(desc, 100)


class TicketReasonModal(discord.ui.Modal, title="Create Ticket"):
    def __init__(self):
        super().__init__(timeout=None)

        self.reason = discord.ui.TextInput(
            label="What do you need help with?",
            placeholder="Describe your issue clearly so the correct ticket category is used.",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=_REASON_MAX_LEN,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            guild = interaction.guild
            user = _resolve_member(interaction)

            if guild is None or user is None:
                await _safe_followup(interaction, "This can only be used inside the server.")
                return

            await _safe_defer(interaction)

            reason_text = _normalize_text(str(self.reason.value or ""))
            _debug(
                f"reason-modal submit guild={guild.id} user={user.id} "
                f"reason={_normalize_text(reason_text, limit=160)!r}"
            )

            category_slug, category_label, categories, match_payload = await _infer_dashboard_category(
                guild_id=int(guild.id),
                reason=reason_text,
            )

            if not match_payload:
                matched_row = _find_category_row_by_slug(categories, category_slug)
                match_payload = _build_match_payload(
                    matched_row=matched_row,
                    matched_score=None,
                    matched_reason="Modal classification fallback payload.",
                    category_slug=category_slug,
                )

            _debug(
                f"reason-modal inferred guild={guild.id} user={user.id} "
                f"slug={category_slug} label={category_label!r} match={match_payload!r}"
            )

            await _create_ticket_for_member(
                interaction=interaction,
                guild=guild,
                user=user,
                category=category_slug,
                category_label=category_label,
                reason=reason_text,
                source="discord_button_reason_modal",
                is_ghost=False,
                match_payload=match_payload,
            )
        except Exception as e:
            print("❌ Ticket reason modal submit failed:", repr(e))
            await _safe_followup(
                interaction,
                "Failed to create ticket. Please try again in a moment.",
            )


class GhostTicketReasonModal(discord.ui.Modal, title="Create Ghost Ticket"):
    def __init__(self):
        super().__init__(timeout=None)

        self.reason = discord.ui.TextInput(
            label="Ghost ticket reason",
            placeholder="Describe the issue to test dashboard category routing.",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=_REASON_MAX_LEN,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            guild = interaction.guild
            user = _resolve_member(interaction)

            if guild is None or user is None:
                await _safe_followup(interaction, "This can only be used inside the server.")
                return

            if not _is_staff_member(user):
                await _safe_followup(interaction, "You do not have permission to use this.")
                return

            await _safe_defer(interaction)

            reason_text = _normalize_text(str(self.reason.value or ""))
            _debug(
                f"ghost-modal submit guild={guild.id} user={user.id} "
                f"reason={_normalize_text(reason_text, limit=160)!r}"
            )

            category_slug, category_label, categories, match_payload = await _infer_dashboard_category(
                guild_id=int(guild.id),
                reason=reason_text,
            )

            if not match_payload:
                matched_row = _find_category_row_by_slug(categories, category_slug)
                match_payload = _build_match_payload(
                    matched_row=matched_row,
                    matched_score=None,
                    matched_reason="Ghost modal classification fallback payload.",
                    category_slug=category_slug,
                )

            _debug(
                f"ghost-modal inferred guild={guild.id} user={user.id} "
                f"slug={category_slug} label={category_label!r} match={match_payload!r}"
            )

            await _create_ticket_for_member(
                interaction=interaction,
                guild=guild,
                user=user,
                category=category_slug,
                category_label=category_label,
                reason=reason_text,
                source="discord_staff_ghost_reason_modal",
                is_ghost=True,
                match_payload=match_payload,
            )
        except Exception as e:
            print("❌ Ghost ticket reason modal submit failed:", repr(e))
            await _safe_followup(
                interaction,
                "Failed to create ghost ticket. Please try again in a moment.",
            )


class TransferTicketModal(discord.ui.Modal, title="Transfer Ticket"):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = int(channel_id)

        self.target_user = discord.ui.TextInput(
            label="Target staff member",
            placeholder="Paste @mention or user ID",
            style=discord.TextStyle.short,
            required=True,
            max_length=64,
        )
        self.add_item(self.target_user)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            guild = interaction.guild
            actor = _resolve_member(interaction)
            channel = interaction.channel

            if guild is None or actor is None or not isinstance(channel, discord.TextChannel):
                return await _safe_followup(interaction, "This can only be used inside a ticket channel.")

            if not _is_staff_member(actor):
                return await _safe_followup(interaction, "Only staff can transfer tickets.")

            await _safe_defer(interaction)

            row = await _ticket_row_for_channel(channel)
            if _ticket_is_deleted(row):
                return await _safe_followup(interaction, "❌ This ticket is deleted.")
            if _ticket_is_closed_or_stale_closed(channel, row):
                return await _safe_followup(interaction, "❌ Closed tickets cannot be transferred. Reopen it first.")

            target_id = _extract_user_id_from_text(str(self.target_user.value or ""))
            if target_id <= 0:
                return await _safe_followup(interaction, "Could not parse that user ID or mention.")

            target_member = guild.get_member(target_id)
            if target_member is None:
                try:
                    target_member = await guild.fetch_member(target_id)
                except Exception:
                    target_member = None

            if target_member is None:
                return await _safe_followup(interaction, "That member was not found in this server.")

            if not _is_staff_member(target_member):
                return await _safe_followup(interaction, "That member is not staff.")

            claimed_by_id = _claimed_by_id_from_row(row)

            if claimed_by_id == int(target_member.id):
                return await _safe_followup(interaction, f"This ticket is already assigned to {target_member.mention}.")

            ok = await transfer_ticket(
                channel_id=self.channel_id,
                to_staff_member=target_member,
                actor=actor,
            )

            if not ok:
                return await _safe_followup(interaction, "Failed to transfer this ticket.")

            _debug(
                f"transfer success guild={guild.id} actor={actor.id} "
                f"target={target_member.id} channel={channel.id}"
            )

            await _safe_channel_send(
                channel,
                f"🔁 Ticket transferred to {target_member.mention} by {actor.mention}.",
            )

            await _safe_followup(interaction, f"Transferred ticket to {target_member.mention}.")
        except Exception as e:
            print("❌ Transfer ticket modal submit failed:", repr(e))
            await _safe_followup(interaction, "Failed to transfer this ticket.")


class SetPriorityModal(discord.ui.Modal, title="Set Ticket Priority"):
    def __init__(self, channel_id: int, current_priority: str):
        super().__init__(timeout=None)
        self.channel_id = int(channel_id)

        self.priority = discord.ui.TextInput(
            label="Priority",
            placeholder="low, medium, high, or urgent",
            default=str(current_priority or "medium"),
            style=discord.TextStyle.short,
            required=True,
            max_length=16,
        )
        self.add_item(self.priority)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            guild = interaction.guild
            actor = _resolve_member(interaction)
            channel = interaction.channel

            if guild is None or actor is None or not isinstance(channel, discord.TextChannel):
                return await _safe_followup(interaction, "This can only be used inside a ticket channel.")

            if not _is_staff_member(actor):
                return await _safe_followup(interaction, "Only staff can change ticket priority.")

            await _safe_defer(interaction)

            row = await _ticket_row_for_channel(channel)
            if _ticket_is_deleted(row):
                return await _safe_followup(interaction, "❌ This ticket is deleted.")
            if _ticket_is_closed_or_stale_closed(channel, row):
                return await _safe_followup(interaction, "❌ Closed tickets cannot have priority changed. Reopen it first.")

            priority_value = str(self.priority.value or "").strip().lower()
            if priority_value not in VALID_PRIORITIES:
                return await _safe_followup(interaction, "Priority must be one of: low, medium, high, urgent.")

            ok = await set_ticket_priority(
                channel_id=self.channel_id,
                priority=priority_value,
                actor=actor,
            )

            if not ok:
                return await _safe_followup(interaction, "Failed to update ticket priority.")

            _debug(
                f"priority success guild={guild.id} actor={actor.id} "
                f"channel={channel.id} priority={priority_value}"
            )

            await _safe_channel_send(
                channel,
                f"🚦 Ticket priority set to **{priority_value}** by {actor.mention}.",
            )

            await _safe_followup(interaction, f"Priority set to **{priority_value}**.")
        except Exception as e:
            print("❌ Set priority modal submit failed:", repr(e))
            await _safe_followup(interaction, "Failed to update ticket priority.")


class AddInternalNoteModal(discord.ui.Modal, title="Add Internal Note"):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = int(channel_id)

        self.note_body = discord.ui.TextInput(
            label="Internal note",
            placeholder="Only staff should use this. Stored in the database, not shown to regular users.",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=_NOTE_MAX_LEN,
        )
        self.add_item(self.note_body)

        self.pin_note = discord.ui.TextInput(
            label="Pin this note? (yes/no)",
            placeholder="Optional: yes or no",
            style=discord.TextStyle.short,
            required=False,
            max_length=8,
        )
        self.add_item(self.pin_note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            guild = interaction.guild
            actor = _resolve_member(interaction)
            channel = interaction.channel

            if guild is None or actor is None or not isinstance(channel, discord.TextChannel):
                return await _safe_followup(interaction, "This can only be used inside a ticket channel.")

            if not _is_staff_member(actor):
                return await _safe_followup(interaction, "Only staff can add internal notes.")

            await _safe_defer(interaction)

            row = await _ticket_row_for_channel(channel)
            if _ticket_is_deleted(row):
                return await _safe_followup(interaction, "❌ This ticket is deleted.")

            note_text = _normalize_multiline_text(str(self.note_body.value or ""))
            if not note_text:
                return await _safe_followup(interaction, "The note cannot be empty.")

            pin_text = str(self.pin_note.value or "").strip().lower()
            is_pinned = pin_text in {"yes", "y", "true", "1", "pin", "pinned"}

            ok = await add_internal_note(
                channel_id=self.channel_id,
                author=actor,
                note=note_text,
                is_pinned=is_pinned,
            )

            if not ok:
                return await _safe_followup(
                    interaction,
                    "Failed to save note. The notes table may not exist yet, or the DB write failed.",
                )

            _debug(
                f"note success guild={guild.id} actor={actor.id} "
                f"channel={channel.id} pinned={is_pinned}"
            )

            preview = _truncate(note_text, 160)
            pin_prefix = "📌 " if is_pinned else ""
            await _safe_channel_send(
                channel,
                f"📝 {pin_prefix}Internal note added by {actor.mention}: {preview}",
            )

            await _safe_followup(interaction, "Internal note saved.")
        except Exception as e:
            print("❌ Add internal note modal submit failed:", repr(e))
            await _safe_followup(interaction, "Failed to save internal note.")


class MacroSelect(discord.ui.Select):
    def __init__(self, *, channel_id: int, actor_id: int, macro_rows: List[Dict[str, Any]]):
        self.channel_id = int(channel_id)
        self.actor_id = int(actor_id)
        self.macro_rows = list(macro_rows)

        options: List[discord.SelectOption] = []
        for row in self.macro_rows[:_MACRO_OPTION_LIMIT]:
            slug = str(row.get("slug") or "").strip()
            if not slug:
                continue

            options.append(
                discord.SelectOption(
                    label=_macro_option_label(row),
                    value=slug,
                    description=_macro_option_description(row),
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="No macros available",
                    value="__none__",
                    description="Nothing available for this ticket right now.",
                )
            )

        super().__init__(
            placeholder="Choose a macro to send…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"ticket_macro_select:{self.channel_id}:{self.actor_id}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            channel = interaction.channel
            actor = _resolve_member(interaction)

            if not isinstance(channel, discord.TextChannel) or actor is None:
                return await _safe_followup(interaction, "This can only be used inside a ticket channel.")

            if int(channel.id) != int(self.channel_id):
                return await _safe_followup(interaction, "This macro picker belongs to a different ticket.")

            if int(actor.id) != int(self.actor_id):
                return await _safe_followup(interaction, "Only the staff member who opened this picker can use it.")

            if not _is_staff_member(actor):
                return await _safe_followup(interaction, "Only staff can use ticket macros.")

            row = await _ticket_row_for_channel(channel)
            if _ticket_is_deleted(row):
                return await _safe_followup(interaction, "❌ This ticket is deleted.")
            if _ticket_is_closed_or_stale_closed(channel, row):
                return await _safe_followup(interaction, "❌ Closed tickets cannot send macros.")

            slug = str(self.values[0] if self.values else "").strip()
            if not slug or slug == "__none__":
                return await _safe_followup(interaction, "No macro was selected.")

            await _safe_defer(interaction)

            result = await send_ticket_macro(
                channel=channel,
                slug=slug,
                actor=actor,
            )

            if not result.get("ok"):
                _debug(
                    f"macro send failed guild={channel.guild.id} "
                    f"user={actor.id} channel={channel.id} slug={slug} "
                    f"error={result.get('message')!r}"
                )
                return await _safe_followup(
                    interaction,
                    str(result.get("message") or "Failed to send macro."),
                )

            macro = result.get("macro") or {}
            send_as_note = bool(result.get("send_as_note", False))
            macro_name = str(macro.get("name") or macro.get("slug") or slug)

            _debug(
                f"macro send success guild={channel.guild.id} "
                f"user={actor.id} channel={channel.id} slug={slug} "
                f"send_as_note={send_as_note}"
            )

            if send_as_note:
                await _safe_followup(
                    interaction,
                    f"Saved macro **{macro_name}** as an internal note.",
                )
            else:
                await _safe_followup(
                    interaction,
                    f"Sent macro **{macro_name}**.",
                )

        except Exception as e:
            print("❌ Macro select callback failed:", repr(e))
            await _safe_followup(interaction, "Failed to send the selected macro.")


class MacroPickerView(discord.ui.View):
    def __init__(self, *, channel_id: int, actor_id: int, macro_rows: List[Dict[str, Any]]):
        super().__init__(timeout=180)
        self.channel_id = int(channel_id)
        self.actor_id = int(actor_id)
        self.macro_rows = list(macro_rows)

        self.add_item(
            MacroSelect(
                channel_id=self.channel_id,
                actor_id=self.actor_id,
                macro_rows=self.macro_rows,
            )
        )


async def _action_claim(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        _debug(
            f"claim denied non-staff guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id}"
        )
        return await _safe_followup(interaction, "Only staff can claim tickets.")

    await _safe_defer(interaction)

    row = await _ticket_row_for_channel(channel)
    if not _ticket_is_open_like(channel, row):
        return await _safe_followup(interaction, _open_panel_state_error(channel, row))

    claimed_by_id = _claimed_by_id_from_row(row)

    _debug(
        f"claim click guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id} existing_claimed_by={claimed_by_id}"
    )

    if claimed_by_id == int(member.id):
        return await _safe_followup(interaction, "You already claimed this ticket.")

    if claimed_by_id > 0 and claimed_by_id != int(member.id):
        return await _safe_followup(interaction, f"This ticket is already claimed by <@{claimed_by_id}>.")

    ok = await assign_ticket(
        channel_id=channel.id,
        staff_member=member,
    )

    if not ok:
        _debug(
            f"claim failed guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id}"
        )
        return await _safe_followup(interaction, "Failed to claim this ticket.")

    _debug(
        f"claim success guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id}"
    )

    await _safe_channel_send(channel, f"🎯 Ticket claimed by {member.mention}.")
    await _safe_followup(interaction, "Ticket claimed.")


async def _action_unclaim(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        return await _safe_followup(interaction, "Only staff can unclaim tickets.")

    await _safe_defer(interaction)

    row = await _ticket_row_for_channel(channel)
    if not _ticket_is_open_like(channel, row):
        return await _safe_followup(interaction, _open_panel_state_error(channel, row))

    claimed_by_id = _claimed_by_id_from_row(row)

    _debug(
        f"unclaim click guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id} claimed_by={claimed_by_id}"
    )

    if claimed_by_id <= 0:
        return await _safe_followup(interaction, "This ticket is not currently claimed.")

    ok = await unclaim_ticket(
        channel_id=channel.id,
        actor=member,
    )
    if not ok:
        return await _safe_followup(interaction, "Failed to unclaim this ticket.")

    await _safe_channel_send(channel, f"↩️ Ticket unclaimed by {member.mention}.")
    await _safe_followup(interaction, "Ticket unclaimed.")


async def _action_transfer(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        return await _safe_followup(interaction, "Only staff can transfer tickets.")

    row = await _ticket_row_for_channel(channel)
    if not _ticket_is_open_like(channel, row):
        return await _safe_followup(interaction, _open_panel_state_error(channel, row))

    _debug(
        f"transfer modal-open guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id}"
    )

    try:
        await interaction.response.send_modal(TransferTicketModal(channel.id))
    except Exception as e:
        _debug(f"transfer modal-open failed channel={channel.id} error={repr(e)}")
        await _safe_followup(interaction, "Failed to open transfer form.")


async def _action_set_priority(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        return await _safe_followup(interaction, "Only staff can change ticket priority.")

    row = await _ticket_row_for_channel(channel)
    if not _ticket_is_open_like(channel, row):
        return await _safe_followup(interaction, _open_panel_state_error(channel, row))

    current_priority = _priority_from_row(row)

    _debug(
        f"priority modal-open guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id} current={current_priority}"
    )

    try:
        await interaction.response.send_modal(SetPriorityModal(channel.id, current_priority))
    except Exception as e:
        _debug(f"priority modal-open failed channel={channel.id} error={repr(e)}")
        await _safe_followup(interaction, "Failed to open priority form.")


async def _action_add_note(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        return await _safe_followup(interaction, "Only staff can add internal notes.")

    row = await _ticket_row_for_channel(channel)
    if _ticket_is_deleted(row):
        return await _safe_followup(interaction, "❌ This ticket is deleted.")

    _debug(
        f"note modal-open guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id}"
    )

    try:
        await interaction.response.send_modal(AddInternalNoteModal(channel.id))
    except Exception as e:
        _debug(f"note modal-open failed channel={channel.id} error={repr(e)}")
        await _safe_followup(interaction, "Failed to open note form.")


async def _action_view_notes(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        return await _safe_followup(interaction, "Only staff can view internal notes.")

    await _safe_defer(interaction)

    row = await _ticket_row_for_channel(channel)
    if _ticket_is_deleted(row):
        return await _safe_followup(interaction, "❌ This ticket is deleted.")

    notes = await list_internal_notes(channel_id=channel.id, limit=8)
    content = _format_notes_for_display(notes)

    _debug(
        f"view-notes guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id} count={len(notes)}"
    )

    await _safe_followup(interaction, content)


async def _action_list_macros(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        return await _safe_followup(interaction, "Only staff can view ticket macros.")

    await _safe_defer(interaction)

    row = await _ticket_row_for_channel(channel)
    if _ticket_is_deleted(row):
        return await _safe_followup(interaction, "❌ This ticket is deleted.")

    content = await format_available_macros_for_ticket(
        channel=channel,
        limit=_MACRO_OPTION_LIMIT,
    )

    _debug(
        f"list-macros guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id}"
    )

    await _safe_followup(interaction, content)


async def _action_use_macro(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        return await _safe_followup(interaction, "Only staff can use ticket macros.")

    row = await _ticket_row_for_channel(channel)
    if not _ticket_is_open_like(channel, row):
        return await _safe_followup(interaction, _open_panel_state_error(channel, row))

    try:
        macro_rows = await list_available_macros_for_ticket(channel=channel)
    except Exception as e:
        _debug(f"macro picker load failed channel={channel.id} error={repr(e)}")
        macro_rows = []

    if not macro_rows:
        return await _safe_followup(interaction, "No macros are available for this ticket.")

    view = MacroPickerView(
        channel_id=channel.id,
        actor_id=member.id,
        macro_rows=macro_rows,
    )

    _debug(
        f"macro-picker open guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={channel.id} count={len(macro_rows)}"
    )

    try:
        await interaction.response.send_message(
            "Choose a macro to send into this ticket:",
            ephemeral=True,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as e:
        _debug(f"macro picker open failed channel={channel.id} error={repr(e)}")
        await _safe_followup(interaction, "Failed to open macro picker.")


async def _action_close(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    _debug(
        f"close-click guild={member.guild.id if member.guild else 0} "
        f"user={member.id} channel={getattr(channel, 'id', 0)}"
    )

    allowed = False

    if _is_staff_member(member):
        allowed = True
    else:
        try:
            from ..tickets import find_ticket_owner_retry
            ticket_owner = await find_ticket_owner_retry(channel)
            if ticket_owner and int(ticket_owner.id) == int(member.id):
                allowed = True
        except Exception:
            pass

    if not allowed:
        _debug(
            f"close-click denied guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={getattr(channel, 'id', 0)}"
        )
        return await _safe_followup(
            interaction,
            "Only the ticket owner or staff can close this ticket.",
        )

    await _safe_defer(interaction)

    row = await _ticket_row_for_channel(channel)
    if not _ticket_is_open_like(channel, row):
        return await _safe_followup(interaction, _open_panel_state_error(channel, row))

    try:
        await prompt_ticket_close_confirmation(
            channel,
            requested_by=member,
        )
        _debug(
            f"close-confirmation posted guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id}"
        )
        await _safe_followup(interaction, "Close confirmation posted.")
    except Exception as e:
        print("❌ Failed to post close confirmation:", repr(e))
        await _safe_followup(
            interaction,
            "Failed to start close confirmation.",
        )


async def _action_ticket_info(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        return await _safe_followup(interaction, "Invalid ticket channel.")

    member = _resolve_member(interaction)
    if member is None:
        return await _safe_followup(interaction, "This can only be used inside the server.")

    if not _is_staff_member(member):
        return await _safe_followup(interaction, "Only staff can view ticket details here.")

    await _safe_defer(interaction)
    row = await _ticket_row_for_channel(channel)
    await _safe_followup(interaction, _ticket_summary_text(channel, row))


class TicketActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="Transfer Ticket",
                value="transfer",
                description="Move this ticket to another staff member",
                emoji="🔁",
            ),
            discord.SelectOption(
                label="Set Priority",
                value="priority",
                description="Change the urgency level",
                emoji="🚦",
            ),
            discord.SelectOption(
                label="Add Internal Note",
                value="add_note",
                description="Save a staff-only note",
                emoji="📝",
            ),
            discord.SelectOption(
                label="View Internal Notes",
                value="view_notes",
                description="Read staff-only notes for this ticket",
                emoji="📚",
            ),
            discord.SelectOption(
                label="List Macros",
                value="list_macros",
                description="See available saved replies",
                emoji="📋",
            ),
            discord.SelectOption(
                label="Send Macro",
                value="use_macro",
                description="Pick and send a saved reply",
                emoji="⚡",
            ),
        ]
        super().__init__(
            placeholder="More ticket actions…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_actions_more_select",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        action = str(self.values[0] if self.values else "").strip()

        if action == "transfer":
            return await _run_ticket_panel_action(interaction, lambda: _action_transfer(interaction), "transfer ticket")
        if action == "priority":
            return await _run_ticket_panel_action(interaction, lambda: _action_set_priority(interaction), "set priority")
        if action == "add_note":
            return await _run_ticket_panel_action(interaction, lambda: _action_add_note(interaction), "add internal note")
        if action == "view_notes":
            return await _run_ticket_panel_action(interaction, lambda: _action_view_notes(interaction), "view internal notes")
        if action == "list_macros":
            return await _run_ticket_panel_action(interaction, lambda: _action_list_macros(interaction), "list macros")
        if action == "use_macro":
            return await _run_ticket_panel_action(interaction, lambda: _action_use_macro(interaction), "send macro")

        return await _safe_followup(interaction, "That ticket action is not available.")


class TicketChannelActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketActionSelect())

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.primary,
        custom_id="ticket_claim_request",
        emoji="🎯",
        row=0,
    )
    async def claim_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_claim(interaction), "claim ticket")

    @discord.ui.button(
        label="Unclaim",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_unclaim_request",
        emoji="↩️",
        row=0,
    )
    async def unclaim_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_unclaim(interaction), "unclaim ticket")

    @discord.ui.button(
        label="Ticket Info",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_info_request",
        emoji="ℹ️",
        row=0,
    )
    async def ticket_info_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_ticket_info(interaction), "ticket info")

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close_request",
        emoji="🔒",
        row=0,
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_close(interaction), "close ticket")


class LegacyTicketChannelCompatibilityView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Transfer Ticket",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_transfer_request",
        emoji="🔁",
        row=0,
    )
    async def transfer_ticket_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_transfer(interaction), "transfer ticket")

    @discord.ui.button(
        label="Set Priority",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_set_priority_request",
        emoji="🚦",
        row=0,
    )
    async def set_priority_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_set_priority(interaction), "set priority")

    @discord.ui.button(
        label="Add Note",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_add_note_request",
        emoji="📝",
        row=1,
    )
    async def add_note_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_add_note(interaction), "add internal note")

    @discord.ui.button(
        label="View Notes",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_view_notes_request",
        emoji="📚",
        row=1,
    )
    async def view_notes_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_view_notes(interaction), "view internal notes")

    @discord.ui.button(
        label="List Macros",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_list_macros_request",
        emoji="📋",
        row=2,
    )
    async def list_macros_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_list_macros(interaction), "list macros")

    @discord.ui.button(
        label="Use Macro",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_use_macro_request",
        emoji="⚡",
        row=2,
    )
    async def use_macro_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await _run_ticket_panel_action(interaction, lambda: _action_use_macro(interaction), "send macro")


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.green,
        custom_id="ticket_create",
        emoji="🎫",
    )
    async def create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if id(interaction) not in _TICKET_ENTRY_GUARD_ACTIVE:
            return await _run_ticket_entry_callback(
                interaction,
                lambda: self.create_ticket(interaction, button),
                "create ticket",
            )
        try:
            guild = interaction.guild
            user = _resolve_member(interaction)

            if guild is None or user is None:
                await _safe_followup(interaction, "This can only be used inside the server.")
                return

            if getattr(user, "bot", False):
                await _safe_followup(interaction, "Bots cannot create tickets.")
                return

            unverified_only = _is_unverified_only_user(user)
            _debug(
                f"public-create click guild={guild.id} user={user.id} "
                f"unverified_only={unverified_only} "
                f"roles={[int(r.id) for r in (user.roles or []) if not r.is_default()]}"
            )

            if unverified_only:
                await _safe_defer(interaction)

                categories = await _fetch_dashboard_ticket_categories(int(guild.id))
                verification_slug = _find_verification_category_slug(categories)
                verification_label = _find_category_label_by_slug(
                    categories,
                    verification_slug,
                    "Verification",
                )
                verification_row = _find_category_row_by_slug(categories, verification_slug)
                verification_match_payload = _build_match_payload(
                    matched_row=verification_row,
                    matched_score=999,
                    matched_reason="User routed directly to verification because they only have the Unverified role.",
                    category_slug=verification_slug,
                )

                _debug(
                    f"public-create unverified-route guild={guild.id} user={user.id} "
                    f"verification_slug={verification_slug} match={verification_match_payload!r}"
                )

                await _create_ticket_for_member(
                    interaction=interaction,
                    guild=guild,
                    user=user,
                    category=verification_slug,
                    category_label=verification_label,
                    reason="",
                    source="discord_button_unverified",
                    is_ghost=False,
                    match_payload=verification_match_payload,
                )
                return

            _debug(f"public-create modal-route guild={guild.id} user={user.id}")

            if not interaction.response.is_done():
                await interaction.response.send_modal(TicketReasonModal())
                return

            await _safe_followup(interaction, "Please try again to open the ticket form.")

        except Exception as e:
            print("❌ Public ticket create failed:", repr(e))
            raise


class StaffGhostTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Quick Ghost Ticket",
        style=discord.ButtonStyle.secondary,
        custom_id="ghost_ticket_create_staff_only",
        emoji="👻",
    )
    async def create_ghost_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if id(interaction) not in _TICKET_ENTRY_GUARD_ACTIVE:
            return await _run_ticket_entry_callback(
                interaction,
                lambda: self.create_ghost_ticket(interaction, button),
                "quick ghost ticket",
            )
        await _safe_defer(interaction)

        try:
            guild = interaction.guild
            user = _resolve_member(interaction)

            if guild is None or user is None:
                await _safe_followup(interaction, "This can only be used inside the server.")
                return

            if not _is_staff_member(user):
                await _safe_followup(interaction, "You do not have permission to use this.")
                return

            quick_payload = _build_match_payload(
                matched_row=None,
                matched_score=0,
                matched_reason="Quick ghost ticket created without category inference.",
                category_slug=FALLBACK_GHOST_CATEGORY,
            )

            _debug(
                f"ghost-quick click guild={guild.id} user={user.id} "
                f"category={FALLBACK_GHOST_CATEGORY} match={quick_payload!r}"
            )

            await _create_ticket_for_member(
                interaction=interaction,
                guild=guild,
                user=user,
                category=FALLBACK_GHOST_CATEGORY,
                category_label="Ghost",
                reason="",
                source="discord_staff_hidden_quick_ghost",
                is_ghost=True,
                match_payload=quick_payload,
            )

        except Exception as e:
            print("❌ Quick ghost ticket create failed:", repr(e))
            raise

    @discord.ui.button(
        label="Ghost Ticket With Reason",
        style=discord.ButtonStyle.blurple,
        custom_id="ghost_ticket_reason_staff_only",
        emoji="🧪",
    )
    async def create_ghost_ticket_with_reason(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if id(interaction) not in _TICKET_ENTRY_GUARD_ACTIVE:
            return await _run_ticket_entry_callback(
                interaction,
                lambda: self.create_ghost_ticket_with_reason(interaction, button),
                "ghost ticket with reason",
            )
        try:
            guild = interaction.guild
            user = _resolve_member(interaction)

            if guild is None or user is None:
                await _safe_followup(interaction, "This can only be used inside the server.")
                return

            if not _is_staff_member(user):
                await _safe_followup(interaction, "You do not have permission to use this.")
                return

            _debug(f"ghost-modal open guild={guild.id} user={user.id}")

            await interaction.response.send_modal(GhostTicketReasonModal())

        except Exception as e:
            print("❌ Ghost ticket modal open failed:", repr(e))
            raise


async def send_ticket_panel(channel: discord.TextChannel):
    embed = discord.Embed(
        title="Support Tickets",
        description=(
            "Press **Create Ticket** to open a support ticket.\n\n"
            "**Unverified members** go straight to verification help.\n"
            "**Verified members** can describe the problem and the bot will choose the best dashboard category."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="How it works",
        value=(
            "1. Press the button\n"
            "2. Explain the issue\n"
            "3. The bot opens the right type of ticket"
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield Ticket System")

    _debug(f"send public panel channel={channel.id} guild={channel.guild.id}")

    await channel.send(
        embed=embed,
        view=TicketPanelView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def send_staff_ghost_ticket_panel(channel: discord.TextChannel):
    embed = discord.Embed(
        title="Ghost Ticket Test Panel",
        description=(
            "This panel is for staff/testing only.\n\n"
            "**Quick Ghost Ticket** makes a fast internal test ticket.\n"
            "**Ghost Ticket With Reason** opens a form, runs category inference, "
            "and still creates the ticket as ghost mode."
        ),
        color=discord.Color.dark_grey(),
    )
    embed.set_footer(text="Staff / Test Only")

    _debug(f"send ghost panel channel={channel.id} guild={channel.guild.id}")

    await channel.send(
        embed=embed,
        view=StaffGhostTicketView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.listen("on_ready")
async def register_ticket_panel():
    global _PERSISTENT_VIEWS_REGISTERED

    if _PERSISTENT_VIEWS_REGISTERED:
        return

    _PERSISTENT_VIEWS_REGISTERED = True

    try:
        bot.add_view(TicketPanelView())
        _debug("registered TicketPanelView")
    except Exception as e:
        print("⚠️ Failed to register public ticket panel view:", repr(e))

    try:
        bot.add_view(StaffGhostTicketView())
        _debug("registered StaffGhostTicketView")
    except Exception as e:
        print("⚠️ Failed to register staff ghost ticket view:", repr(e))

    try:
        bot.add_view(TicketChannelActionsView())
        _debug("registered TicketChannelActionsView")
    except Exception as e:
        print("⚠️ Failed to register ticket channel actions view:", repr(e))

    try:
        bot.add_view(LegacyTicketChannelCompatibilityView())
        _debug("registered LegacyTicketChannelCompatibilityView")
    except Exception as e:
        print("⚠️ Failed to register legacy ticket action compatibility view:", repr(e))

    print("✅ Ticket panel buttons registered.")


__all__ = [
    "TicketReasonModal",
    "GhostTicketReasonModal",
    "TransferTicketModal",
    "SetPriorityModal",
    "AddInternalNoteModal",
    "MacroSelect",
    "MacroPickerView",
    "TicketActionSelect",
    "TicketChannelActionsView",
    "LegacyTicketChannelCompatibilityView",
    "TicketPanelView",
    "StaffGhostTicketView",
    "send_ticket_panel",
    "send_staff_ghost_ticket_panel",
]
