from __future__ import annotations

import asyncio
import re
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


def _tokenize_text(text: str) -> List[str]:
    cleaned = _slugify(text)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return [part for part in cleaned.split() if part]


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
            text = _normalize_text(str(item or ""), limit=120).lower()
            if text and text not in out:
                out.append(text)
    except Exception:
        pass
    return out


def _normalize_category_row(row: Dict[str, Any]) -> Dict[str, Any]:
    slug = _normalize_text(str(row.get("slug") or ""), limit=120).lower()
    name = _normalize_text(str(row.get("name") or ""), limit=200)
    description = _normalize_text(str(row.get("description") or ""), limit=500)
    intake_type = _normalize_text(str(row.get("intake_type") or ""), limit=80).lower()

    return {
        "id": row.get("id"),
        "guild_id": str(row.get("guild_id") or ""),
        "slug": slug,
        "name": name,
        "description": description,
        "intake_type": intake_type,
        "match_keywords": _normalize_keywords(row.get("match_keywords")),
        "is_default": bool(row.get("is_default", False)),
        "sort_order": row.get("sort_order"),
    }


def _fetch_dashboard_ticket_categories_sync(guild_id: int) -> List[Dict[str, Any]]:
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
    return normalized


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
    slug_clean = str(slug or "").strip().lower()
    for cat in categories:
        if str(cat.get("slug") or "").strip().lower() == slug_clean:
            label = str(cat.get("name") or "").strip()
            if label:
                return label
    return fallback


def _reason_has_cod_legacy_signals(reason: str) -> bool:
    text = f" {_normalize_text(reason).lower()} "
    signals = (
        " mw2 ", " mw3 ", " bo1 ", " bo2 ", " bo3 ",
        " world at war ", " waw ", " ghosts ", " cod ghosts ",
        " advanced warfare ", " aw ", " infinite warfare ", " iw ",
        " recovery ", " recoveries ", " challenge lobby ", " challenge lobbies ",
        " unlock all ", " mod menu ", " rgh ", " jtag ",
        " old cod ", " older cod ", " legacy cod ",
    )
    return any(s in text for s in signals)


def _score_reason_against_category(reason: str, cat: Dict[str, Any]) -> int:
    reason_norm = _normalize_text(reason, limit=_REASON_MAX_LEN).lower()
    reason_tokens = set(_tokenize_text(reason_norm))

    slug = str(cat.get("slug") or "").lower()
    name = str(cat.get("name") or "").lower()
    desc = str(cat.get("description") or "").lower()
    keywords = [str(x).lower() for x in (cat.get("match_keywords") or [])]

    score = 0

    for kw in keywords:
        kw_clean = _normalize_text(kw, limit=120).lower()
        if not kw_clean:
            continue
        if kw_clean in reason_norm:
            score += 25
            if len(kw_clean.split()) > 1:
                score += 10

    slug_words = [w for w in re.split(r"[-_\s]+", slug) if w]
    name_words = [w for w in _tokenize_text(name)]
    desc_words = [w for w in _tokenize_text(desc)]

    for word in slug_words:
        if len(word) >= 3 and word in reason_tokens:
            score += 6

    for word in name_words:
        if len(word) >= 3 and word in reason_tokens:
            score += 5

    for word in desc_words[:25]:
        if len(word) >= 4 and word in reason_tokens:
            score += 2

    intake_type = str(cat.get("intake_type") or "").lower()
    if intake_type == "appeal" and any(x in reason_norm for x in ["appeal", "unban", "timeout", "ban", "muted", "banned"]):
        score += 6
    elif intake_type == "report" and any(x in reason_norm for x in ["report", "scam", "abuse", "harassment", "threat"]):
        score += 6
    elif intake_type == "partnership" and any(x in reason_norm for x in ["partner", "partnership", "collab", "promo", "sponsor"]):
        score += 6
    elif intake_type == "question" and any(x in reason_norm for x in ["question", "help", "how do i", "how to"]):
        score += 4

    if _reason_has_cod_legacy_signals(reason_norm):
        haystack = f"{slug} {name} {desc} {' '.join(keywords)}"
        if any(x in haystack for x in [
            "cod", "call of duty", "legacy", "older", "old school", "recovery",
            "recoveries", "challenge lobby", "challenge lobbies", "unlock all",
            "mod menu", "mw2", "mw3", "bo2", "bo3", "ghosts", "waw", "rgh", "jtag"
        ]):
            score += 40

    return score


async def _infer_dashboard_category(
    *,
    guild_id: int,
    reason: str,
) -> Tuple[str, str, List[Dict[str, Any]]]:
    categories = await _fetch_dashboard_ticket_categories(guild_id)

    if not categories:
        _debug(f"category infer fallback=support guild={guild_id} reason_len={len(reason or '')}")
        return FALLBACK_SUPPORT_CATEGORY, "Support", []

    best: Optional[Dict[str, Any]] = None
    best_score = 0

    for cat in categories:
        score = _score_reason_against_category(reason, cat)
        if score > best_score:
            best_score = score
            best = cat

    if best is not None and best_score > 0:
        _debug(
            f"category infer matched guild={guild_id} "
            f"slug={best.get('slug')} label={best.get('name')} score={best_score}"
        )
        return (
            str(best.get("slug") or FALLBACK_SUPPORT_CATEGORY),
            str(best.get("name") or "Support"),
            categories,
        )

    default_slug = _find_default_category_slug(categories)
    default_cat = next((c for c in categories if str(c.get("slug") or "") == default_slug), None)

    if default_cat is not None:
        _debug(
            f"category infer default guild={guild_id} "
            f"slug={default_cat.get('slug')} label={default_cat.get('name')}"
        )
        return (
            str(default_cat.get("slug") or FALLBACK_SUPPORT_CATEGORY),
            str(default_cat.get("name") or "Support"),
            categories,
        )

    _debug(f"category infer fallback=no-default guild={guild_id}")
    return FALLBACK_SUPPORT_CATEGORY, "Support", categories


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
) -> None:
    reason_preview = _normalize_text(reason, limit=120)
    _debug(
        f"create request guild={guild.id} user={user.id} "
        f"category={category} label={category_label!r} source={source} "
        f"ghost={is_ghost} reason={reason_preview!r}"
    )

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

    parent_category_id = _ticket_parent_category_id()
    staff_role_ids = _staff_role_ids_for_ticket(guild)

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
        f"channel={channel.id} name={channel.name!r} category={category} source={source}"
    )

    await _safe_followup(
        interaction,
        f"Ticket created: {channel.mention}",
    )


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

            category_slug, category_label, _categories = await _infer_dashboard_category(
                guild_id=int(guild.id),
                reason=reason_text,
            )

            _debug(
                f"reason-modal inferred guild={guild.id} user={user.id} "
                f"slug={category_slug} label={category_label!r}"
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

            category_slug, category_label, _categories = await _infer_dashboard_category(
                guild_id=int(guild.id),
                reason=reason_text,
            )

            _debug(
                f"ghost-modal inferred guild={guild.id} user={user.id} "
                f"slug={category_slug} label={category_label!r}"
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

            row = await _ticket_row_for_channel(channel)
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


class TicketChannelActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Claim Ticket",
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
            return await _safe_followup(
                interaction,
                "Only staff can claim tickets.",
            )

        await _safe_defer(interaction)

        row = await _ticket_row_for_channel(channel)
        claimed_by_id = _claimed_by_id_from_row(row)

        _debug(
            f"claim click guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id} existing_claimed_by={claimed_by_id}"
        )

        if claimed_by_id == int(member.id):
            return await _safe_followup(
                interaction,
                "You already claimed this ticket.",
            )

        if claimed_by_id > 0 and claimed_by_id != int(member.id):
            return await _safe_followup(
                interaction,
                f"This ticket is already claimed by <@{claimed_by_id}>.",
            )

        ok = await assign_ticket(
            channel_id=channel.id,
            staff_member=member,
        )

        if not ok:
            _debug(
                f"claim failed guild={member.guild.id if member.guild else 0} "
                f"user={member.id} channel={channel.id}"
            )
            return await _safe_followup(
                interaction,
                "Failed to claim this ticket.",
            )

        _debug(
            f"claim success guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id}"
        )

        await _safe_channel_send(channel, f"🎯 Ticket claimed by {member.mention}.")
        await _safe_followup(interaction, "Ticket claimed.")

    @discord.ui.button(
        label="Unclaim Ticket",
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
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _safe_followup(interaction, "Invalid ticket channel.")

        member = _resolve_member(interaction)
        if member is None:
            return await _safe_followup(interaction, "This can only be used inside the server.")

        if not _is_staff_member(member):
            return await _safe_followup(interaction, "Only staff can transfer tickets.")

        _debug(
            f"transfer modal-open guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id}"
        )

        try:
            await interaction.response.send_modal(TransferTicketModal(channel.id))
        except Exception as e:
            _debug(f"transfer modal-open failed channel={channel.id} error={repr(e)}")
            await _safe_followup(interaction, "Failed to open transfer form.")

    @discord.ui.button(
        label="Set Priority",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_set_priority_request",
        emoji="🚦",
        row=1,
    )
    async def set_priority_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _safe_followup(interaction, "Invalid ticket channel.")

        member = _resolve_member(interaction)
        if member is None:
            return await _safe_followup(interaction, "This can only be used inside the server.")

        if not _is_staff_member(member):
            return await _safe_followup(interaction, "Only staff can change ticket priority.")

        row = await _ticket_row_for_channel(channel)
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
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _safe_followup(interaction, "Invalid ticket channel.")

        member = _resolve_member(interaction)
        if member is None:
            return await _safe_followup(interaction, "This can only be used inside the server.")

        if not _is_staff_member(member):
            return await _safe_followup(interaction, "Only staff can add internal notes.")

        _debug(
            f"note modal-open guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id}"
        )

        try:
            await interaction.response.send_modal(AddInternalNoteModal(channel.id))
        except Exception as e:
            _debug(f"note modal-open failed channel={channel.id} error={repr(e)}")
            await _safe_followup(interaction, "Failed to open note form.")

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
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _safe_followup(interaction, "Invalid ticket channel.")

        member = _resolve_member(interaction)
        if member is None:
            return await _safe_followup(interaction, "This can only be used inside the server.")

        if not _is_staff_member(member):
            return await _safe_followup(interaction, "Only staff can view internal notes.")

        await _safe_defer(interaction)

        notes = await list_internal_notes(channel_id=channel.id, limit=8)
        content = _format_notes_for_display(notes)

        _debug(
            f"view-notes guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id} count={len(notes)}"
        )

        await _safe_followup(interaction, content)

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
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _safe_followup(interaction, "Invalid ticket channel.")

        member = _resolve_member(interaction)
        if member is None:
            return await _safe_followup(interaction, "This can only be used inside the server.")

        if not _is_staff_member(member):
            return await _safe_followup(interaction, "Only staff can view ticket macros.")

        await _safe_defer(interaction)

        content = await format_available_macros_for_ticket(
            channel=channel,
            limit=_MACRO_OPTION_LIMIT,
        )

        _debug(
            f"list-macros guild={member.guild.id if member.guild else 0} "
            f"user={member.id} channel={channel.id}"
        )

        await _safe_followup(interaction, content)

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
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _safe_followup(interaction, "Invalid ticket channel.")

        member = _resolve_member(interaction)
        if member is None:
            return await _safe_followup(interaction, "This can only be used inside the server.")

        if not _is_staff_member(member):
            return await _safe_followup(interaction, "Only staff can use ticket macros.")

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

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket_close_request",
        emoji="🔒",
        row=2,
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
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

        try:
            await _safe_defer(interaction)
        except Exception:
            pass

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

                _debug(
                    f"public-create unverified-route guild={guild.id} user={user.id} "
                    f"verification_slug={verification_slug}"
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
                )
                return

            _debug(f"public-create modal-route guild={guild.id} user={user.id}")

            if not interaction.response.is_done():
                await interaction.response.send_modal(TicketReasonModal())
                return

            await _safe_followup(interaction, "Please try again to open the ticket form.")

        except Exception as e:
            print("❌ Public ticket create failed:", repr(e))
            await _safe_followup(
                interaction,
                "Failed to create ticket. Please try again in a moment.",
            )


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

            _debug(
                f"ghost-quick click guild={guild.id} user={user.id} "
                f"category={FALLBACK_GHOST_CATEGORY}"
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
            )

        except Exception as e:
            print("❌ Quick ghost ticket create failed:", repr(e))
            await _safe_followup(
                interaction,
                "Failed to create ghost ticket. Please try again in a moment.",
            )

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
            await _safe_followup(
                interaction,
                "Failed to open ghost ticket form. Please try again.",
            )


async def send_ticket_panel(channel: discord.TextChannel):
    embed = discord.Embed(
        title="Support Tickets",
        description=(
            "Press **Create Ticket** to open a ticket.\n\n"
            "Unverified users go straight into verification.\n"
            "Everyone else can describe their issue and the bot will use the dashboard ticket category setup."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="Stoney Verify Ticket System")

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
            "**Quick Ghost Ticket** creates a fast internal ghost ticket.\n"
            "**Ghost Ticket With Reason** opens a modal, uses dashboard category inference, "
            "and still creates the ticket with ghost mode enabled."
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

    print("✅ Ticket panel buttons registered.")


__all__ = [
    "TicketReasonModal",
    "GhostTicketReasonModal",
    "TransferTicketModal",
    "SetPriorityModal",
    "AddInternalNoteModal",
    "MacroSelect",
    "MacroPickerView",
    "TicketChannelActionsView",
    "TicketPanelView",
    "StaffGhostTicketView",
    "send_ticket_panel",
    "send_staff_ghost_ticket_panel",
]
