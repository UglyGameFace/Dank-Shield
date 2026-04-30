from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import discord

from ..globals import get_supabase, now_utc, reset_supabase
from .panel_repository import (
    DEFAULT_PANEL_RULES,
    build_panel_runtime_config,
    get_ticket_panel,
    panel_creation_guard_scope,
)
from .panel_rules import (
    evaluate_panel_creation_request,
    get_effective_panel_runtime,
    panel_owner_open_limit,
    panel_rules_for_automation,
)


# ============================================================
# tickets_new/panel_runtime.py
# ------------------------------------------------------------
# Runtime bridge between the existing ticket panel UI and the
# new DB-backed multi-panel system.
#
# Why this exists:
# - panel.py is large and should not be blindly rewritten
# - this gives the existing panel flow a clean integration point
# - every create flow can call this before create_ticket_channel()
#
# Safety goals:
# - no server-specific .env reliance
# - guild-scoped config only
# - per-owner/panel creation lock
# - per-guild command pressure semaphore
# - DB-backed panel/rule lookup
# - no cross-server bleed
#
# Legal/privacy posture:
# - no hidden data collection here
# - no external sharing
# - only ticket operational metadata is attached
# - server owners should disclose ticket logging/transcripts
#   in server rules or panel copy
# ============================================================


DEFAULT_PANEL_KEY = "support"
DEFAULT_PANEL_NOTICE = (
    "By opening a ticket, you understand staff may review ticket messages, "
    "notes, actions, and transcripts for support/moderation purposes."
)

_OPEN_STATUSES = {"open", "claimed", "active", "reopened"}
_DB_MAX_ATTEMPTS = 5


# ============================================================
# Small helpers
# ============================================================

def _debug(msg: str) -> None:
    try:
        print(f"🧩 panel_runtime {msg}")
    except Exception:
        pass


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
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _now_iso() -> str:
    try:
        return now_utc().isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _slugify(value: Any, limit: int = 120) -> str:
    raw = _safe_str(value).lower().replace("&", " and ")
    out: List[str] = []
    prev_dash = False

    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_", "/", ":"}:
            if not prev_dash:
                out.append("-")
                prev_dash = True

    text = "".join(out).strip("-")
    return text[:limit] if text else ""


def normalize_panel_key(value: Any, *, default: str = DEFAULT_PANEL_KEY) -> str:
    key = _slugify(value, limit=80)
    return key or default


def normalize_category_slug(value: Any, *, default: str = "support") -> str:
    slug = _slugify(value, limit=120)
    return slug or default


def _truncate(value: Any, limit: int = 900) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


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
        "connection terminated",
        "httpcore",
        "httpx",
        "broken pipe",
        "connection pool",
        "stream closed",
        "try again",
    )
    return any(marker in text for marker in markers)


def _execute_db_op(op_name: str, executor, max_attempts: int = _DB_MAX_ATTEMPTS):
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass
                _debug(f"{op_name}: transient DB error {attempt}/{max_attempts}: {repr(e)}")
                continue
            raise

    if last_error is not None:
        raise last_error
    return None


async def _run_db(op_name: str, executor):
    return await asyncio.to_thread(_execute_db_op, op_name, executor)


# ============================================================
# Panel key / message helpers
# ============================================================

def panel_custom_id(panel_key: Any, action: str = "open", category_slug: Optional[Any] = None) -> str:
    key = normalize_panel_key(panel_key)
    act = normalize_panel_key(action, default="open")

    if category_slug:
        return f"sv:panel:{key}:{act}:{normalize_category_slug(category_slug)}"

    return f"sv:panel:{key}:{act}"


def parse_panel_custom_id(custom_id: Any) -> Dict[str, str]:
    """
    Supports:
    - sv:panel:<panel_key>
    - sv:panel:<panel_key>:open
    - sv:panel:<panel_key>:open:<category_slug>
    - sv:ticket:panel:<panel_key>
    - sv:ticket:panel:<panel_key>:<category_slug>

    Unknown/legacy IDs safely return default support.
    """
    raw = _safe_str(custom_id)

    if not raw:
        return {
            "panel_key": DEFAULT_PANEL_KEY,
            "action": "open",
            "category_slug": "",
            "source": "empty_default",
        }

    parts = raw.split(":")

    try:
        if len(parts) >= 3 and parts[0] == "sv" and parts[1] == "panel":
            return {
                "panel_key": normalize_panel_key(parts[2]),
                "action": normalize_panel_key(parts[3], default="open") if len(parts) >= 4 else "open",
                "category_slug": normalize_category_slug(parts[4], default="") if len(parts) >= 5 else "",
                "source": "sv_panel",
            }

        if len(parts) >= 4 and parts[0] == "sv" and parts[1] == "ticket" and parts[2] == "panel":
            return {
                "panel_key": normalize_panel_key(parts[3]),
                "action": "open",
                "category_slug": normalize_category_slug(parts[4], default="") if len(parts) >= 5 else "",
                "source": "sv_ticket_panel",
            }
    except Exception:
        pass

    return {
        "panel_key": DEFAULT_PANEL_KEY,
        "action": "open",
        "category_slug": "",
        "source": "legacy_default",
    }


def resolve_panel_key_from_interaction(
    interaction: discord.Interaction,
    *,
    fallback_panel_key: Any = DEFAULT_PANEL_KEY,
) -> str:
    try:
        data = getattr(interaction, "data", None) or {}
        custom_id = _safe_str(data.get("custom_id"))
        parsed = parse_panel_custom_id(custom_id)
        key = normalize_panel_key(parsed.get("panel_key"), default=normalize_panel_key(fallback_panel_key))
        return key
    except Exception:
        return normalize_panel_key(fallback_panel_key)


def resolve_category_from_interaction(
    interaction: discord.Interaction,
    *,
    fallback_category: Any = "",
) -> str:
    try:
        data = getattr(interaction, "data", None) or {}
        custom_id = _safe_str(data.get("custom_id"))
        parsed = parse_panel_custom_id(custom_id)
        slug = normalize_category_slug(parsed.get("category_slug"), default="")
        if slug:
            return slug

        values = data.get("values")
        if isinstance(values, list) and values:
            return normalize_category_slug(values[0], default=normalize_category_slug(fallback_category, default=""))

        return normalize_category_slug(fallback_category, default="")
    except Exception:
        return normalize_category_slug(fallback_category, default="")


# ============================================================
# Ticket lookup / concurrency helpers
# ============================================================

def _ticket_matches_panel(row: Dict[str, Any], panel_key: str) -> bool:
    key = normalize_panel_key(panel_key)

    direct_values = [
        row.get("panel_key"),
        row.get("source_panel_key"),
        row.get("ticket_panel_key"),
    ]

    for value in direct_values:
        if normalize_panel_key(value, default="") == key:
            return True

    for meta_key in ("metadata", "meta", "details", "extra"):
        meta = _safe_dict(row.get(meta_key))
        if not meta:
            continue

        for value_key in ("panel_key", "source_panel_key", "ticket_panel_key", "panel"):
            if normalize_panel_key(meta.get(value_key), default="") == key:
                return True

    return False


def _ticket_status(row: Dict[str, Any]) -> str:
    return _safe_str(row.get("status"), "unknown").lower()


def _ticket_open_like(row: Dict[str, Any]) -> bool:
    return _ticket_status(row) in _OPEN_STATUSES


def _ticket_owner_id(row: Dict[str, Any]) -> int:
    return _safe_int(row.get("owner_id") or row.get("user_id") or row.get("member_id"), 0)


def _active_tickets_for_owner_sync(
    *,
    guild_id: int,
    owner_id: int,
    panel_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        return []

    def _read():
        return (
            sb.table("tickets")
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .or_(f"owner_id.eq.{int(owner_id)},user_id.eq.{int(owner_id)}")
            .in_("status", list(_OPEN_STATUSES))
            .execute()
        )

    try:
        res = _execute_db_op(
            f"active tickets for owner guild={guild_id} owner={owner_id}",
            _read,
        )
        rows = getattr(res, "data", None) or []
        clean_rows = [dict(row) for row in rows if isinstance(row, dict)]
    except Exception as e:
        _debug(f"active ticket lookup failed guild={guild_id} owner={owner_id}: {repr(e)}")
        return []

    if not panel_key:
        return [row for row in clean_rows if _ticket_open_like(row)]

    key = normalize_panel_key(panel_key)
    matching = [row for row in clean_rows if _ticket_open_like(row) and _ticket_matches_panel(row, key)]

    # Compatibility fallback:
    # if no rows have panel metadata yet, enforce the owner limit against
    # all open tickets so old tickets do not bypass safety.
    if not matching:
        any_panel_metadata = any(
            row.get("panel_key")
            or row.get("source_panel_key")
            or row.get("ticket_panel_key")
            or _safe_dict(row.get("metadata")).get("panel_key")
            or _safe_dict(row.get("meta")).get("panel_key")
            for row in clean_rows
        )
        if not any_panel_metadata:
            return [row for row in clean_rows if _ticket_open_like(row)]

    return matching


async def active_tickets_for_owner(
    *,
    guild_id: int,
    owner_id: int,
    panel_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return await _run_db(
        f"active tickets async guild={guild_id} owner={owner_id}",
        lambda: _active_tickets_for_owner_sync(
            guild_id=guild_id,
            owner_id=owner_id,
            panel_key=panel_key,
        ),
    )


async def owner_open_ticket_limit_snapshot(
    *,
    guild_id: int,
    owner_id: int,
    panel_key: str,
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    limit = max(1, _safe_int(rules.get("per_owner_open_limit"), 1))
    active = await active_tickets_for_owner(
        guild_id=int(guild_id),
        owner_id=int(owner_id),
        panel_key=panel_key,
    )

    return {
        "ok": len(active) < limit,
        "limit": limit,
        "active_count": len(active),
        "active_tickets": active,
        "reason": (
            ""
            if len(active) < limit
            else f"You already have {len(active)} open ticket(s) for this panel. Limit: {limit}."
        ),
    }


# ============================================================
# Runtime / rule evaluation
# ============================================================

async def ensure_panel_runtime_exists(
    *,
    guild_id: int,
    panel_key: Any,
) -> Optional[Dict[str, Any]]:
    key = normalize_panel_key(panel_key)

    runtime = await get_effective_panel_runtime(guild_id=guild_id, panel_key=key)
    if runtime is not None:
        return runtime

    # Compatibility fallback:
    # If no DB panel exists yet, let legacy support panel continue safely.
    if key == DEFAULT_PANEL_KEY:
        return {
            "panel_key": DEFAULT_PANEL_KEY,
            "panel_name": "Support",
            "panel_style": "buttons",
            "panel_channel_id": "",
            "panel_message_id": "",
            "is_enabled": True,
            "categories": [],
            "rules": dict(DEFAULT_PANEL_RULES),
            "preset_key": "",
            "prompt_title": "Need help?",
            "prompt_description": "Open a ticket and staff will help you as soon as possible.",
            "source": "legacy_default_runtime",
        }

    return None


async def evaluate_panel_ticket_request(
    *,
    member: discord.Member,
    panel_key: Any = DEFAULT_PANEL_KEY,
    category_slug: Optional[Any] = None,
    is_ghost: bool = False,
    enforce_owner_limit: bool = True,
) -> Dict[str, Any]:
    key = normalize_panel_key(panel_key)
    category = normalize_category_slug(category_slug, default="") if category_slug else ""

    access = await evaluate_panel_creation_request(
        member=member,
        panel_key=key,
        category_slug=category or None,
        is_ghost=is_ghost,
    )

    if not _safe_bool(access.get("ok"), False):
        return access

    rules = panel_rules_for_automation(access.get("panel") or {})
    if enforce_owner_limit:
        limit_snapshot = await owner_open_ticket_limit_snapshot(
            guild_id=int(member.guild.id),
            owner_id=int(member.id),
            panel_key=key,
            rules=rules,
        )

        if not _safe_bool(limit_snapshot.get("ok"), False):
            return {
                "ok": False,
                "reason": _safe_str(limit_snapshot.get("reason"), "You already have an open ticket."),
                "source": "owner_open_limit",
                "panel": access.get("panel"),
                "rules": rules,
                "role_state": access.get("role_state"),
                "owner_limit": limit_snapshot,
            }

        access["owner_limit"] = limit_snapshot

    access["ok"] = True
    access["panel_key"] = key
    access["category_slug"] = category
    access["rules"] = rules
    return access


@asynccontextmanager
async def guarded_panel_ticket_creation(
    *,
    member: discord.Member,
    panel_key: Any = DEFAULT_PANEL_KEY,
    category_slug: Optional[Any] = None,
    is_ghost: bool = False,
    semaphore_limit: int = 8,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Use this around the actual create_ticket_channel() call.

    Example:
        async with guarded_panel_ticket_creation(member=member, panel_key="support") as decision:
            if not decision["ok"]:
                ...
            # final check passed inside per-owner/panel lock
            ticket = await create_ticket_channel(...)
    """
    key = normalize_panel_key(panel_key)

    sem, lock = await panel_creation_guard_scope(
        guild_id=int(member.guild.id),
        owner_id=int(member.id),
        panel_key=key,
        semaphore_limit=semaphore_limit,
    )

    async with sem:
        async with lock:
            decision = await evaluate_panel_ticket_request(
                member=member,
                panel_key=key,
                category_slug=category_slug,
                is_ghost=is_ghost,
                enforce_owner_limit=True,
            )
            yield decision


def panel_ticket_notice(runtime: Optional[Dict[str, Any]] = None) -> str:
    try:
        rules = panel_rules_for_automation(runtime)
        custom = _safe_str(rules.get("ticket_notice") or rules.get("privacy_notice"))
        if custom:
            return custom
    except Exception:
        pass

    return DEFAULT_PANEL_NOTICE


def build_panel_ticket_metadata(
    *,
    panel_key: Any,
    category_slug: Optional[Any] = None,
    runtime: Optional[Dict[str, Any]] = None,
    decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    key = normalize_panel_key(panel_key)
    category = normalize_category_slug(category_slug, default="") if category_slug else ""

    rules = panel_rules_for_automation(runtime or (decision or {}).get("panel") or {})
    panel = _safe_dict(runtime or (decision or {}).get("panel"))

    return {
        "panel_key": key,
        "category_slug": category or None,
        "panel_name": _safe_str(panel.get("panel_name")),
        "panel_style": _safe_str(panel.get("panel_style")),
        "preset_key": _safe_str(panel.get("preset_key")),
        "panel_rules_snapshot": {
            "per_owner_open_limit": _safe_int(rules.get("per_owner_open_limit"), 1),
            "auto_close_enabled": _safe_bool(rules.get("auto_close_enabled"), False),
            "auto_close_minutes": _safe_int(rules.get("auto_close_minutes"), 1440),
            "inactivity_reminders_enabled": _safe_bool(rules.get("inactivity_reminders_enabled"), True),
            "inactivity_reminder_minutes": _safe_int(rules.get("inactivity_reminder_minutes"), 240),
            "transcript_mode": _safe_str(rules.get("transcript_mode"), "on_close"),
            "close_confirmation_required": _safe_bool(rules.get("close_confirmation_required"), True),
        },
        "ticket_notice": panel_ticket_notice(panel),
        "created_from_panel_runtime_at": _now_iso(),
    }


def attach_panel_metadata_to_payload(
    payload: Dict[str, Any],
    *,
    panel_key: Any,
    category_slug: Optional[Any] = None,
    runtime: Optional[Dict[str, Any]] = None,
    decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Adds panel metadata to a ticket create payload without destroying
    existing metadata/meta fields.
    """
    out = dict(payload or {})
    metadata = _safe_dict(out.get("metadata") or out.get("meta"))

    panel_metadata = build_panel_ticket_metadata(
        panel_key=panel_key,
        category_slug=category_slug,
        runtime=runtime,
        decision=decision,
    )

    metadata.update(panel_metadata)
    out["metadata"] = metadata
    out["meta"] = metadata

    out.setdefault("panel_key", panel_metadata["panel_key"])
    if panel_metadata.get("category_slug"):
        out.setdefault("category", panel_metadata["category_slug"])

    return out


def build_panel_denial_message(decision: Dict[str, Any]) -> str:
    source = _safe_str(decision.get("source"), "panel_rules")
    reason = _safe_str(decision.get("reason"), "You cannot open this ticket right now.")

    if source == "owner_open_limit":
        active = _safe_dict(decision.get("owner_limit")).get("active_tickets") or []
        if isinstance(active, list) and active:
            first = active[0] if isinstance(active[0], dict) else {}
            channel_id = _safe_int(first.get("channel_id") or first.get("discord_thread_id"), 0)
            if channel_id > 0:
                return f"❌ {reason}\nExisting ticket: <#{channel_id}>"

    return f"❌ {reason}"


def build_panel_runtime_embed(runtime: Dict[str, Any]) -> discord.Embed:
    rules = panel_rules_for_automation(runtime)

    embed = discord.Embed(
        title=f"🎛️ Panel Runtime: {_safe_str(runtime.get('panel_name'), runtime.get('panel_key'))}",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(name="Panel Key", value=f"`{_safe_str(runtime.get('panel_key'), DEFAULT_PANEL_KEY)}`", inline=True)
    embed.add_field(name="Style", value=f"`{_safe_str(runtime.get('panel_style'), 'buttons')}`", inline=True)
    embed.add_field(name="Enabled", value=str(_safe_bool(runtime.get("is_enabled"), True)), inline=True)

    categories = runtime.get("categories") or []
    if isinstance(categories, list) and categories:
        embed.add_field(
            name="Categories",
            value=", ".join(f"`{_safe_str(x)}`" for x in categories[:25])[:1024],
            inline=False,
        )
    else:
        embed.add_field(name="Categories", value="All categories allowed.", inline=False)

    embed.add_field(
        name="Limits",
        value=(
            f"Per-owner open limit: `{_safe_int(rules.get('per_owner_open_limit'), 1)}`\n"
            f"Cooldown: `{_safe_int(rules.get('cooldown_seconds'), 0)}s`\n"
            f"Window limit: `{_safe_int(rules.get('max_tickets_per_window'), 0)}` "
            f"per `{_safe_int(rules.get('window_minutes'), 0)}m`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Automation",
        value=(
            f"Auto-close: `{_safe_bool(rules.get('auto_close_enabled'), False)}` "
            f"after `{_safe_int(rules.get('auto_close_minutes'), 1440)}m`\n"
            f"Inactivity reminders: `{_safe_bool(rules.get('inactivity_reminders_enabled'), True)}` "
            f"after `{_safe_int(rules.get('inactivity_reminder_minutes'), 240)}m`\n"
            f"Transcript mode: `{_safe_str(rules.get('transcript_mode'), 'on_close')}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Notice",
        value=_truncate(panel_ticket_notice(runtime), 1000),
        inline=False,
    )

    return embed


async def resolve_runtime_from_interaction(
    interaction: discord.Interaction,
    *,
    fallback_panel_key: Any = DEFAULT_PANEL_KEY,
) -> Optional[Dict[str, Any]]:
    guild = interaction.guild
    if guild is None:
        return None

    panel_key = resolve_panel_key_from_interaction(
        interaction,
        fallback_panel_key=fallback_panel_key,
    )

    return await ensure_panel_runtime_exists(
        guild_id=int(guild.id),
        panel_key=panel_key,
    )


async def final_pre_create_panel_check(
    *,
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
    panel_key: Any = DEFAULT_PANEL_KEY,
    category_slug: Optional[Any] = None,
    is_ghost: bool = False,
) -> Dict[str, Any]:
    """
    Call this immediately before creating the ticket channel.

    It intentionally works even when member is not passed, resolving it
    from the interaction safely.
    """
    guild = interaction.guild
    if guild is None:
        return {
            "ok": False,
            "reason": "Tickets can only be created inside a server.",
            "source": "no_guild",
        }

    resolved_member = member
    if resolved_member is None:
        user = interaction.user
        if isinstance(user, discord.Member):
            resolved_member = user
        else:
            try:
                resolved_member = guild.get_member(int(user.id))
            except Exception:
                resolved_member = None

    if resolved_member is None:
        return {
            "ok": False,
            "reason": "Could not resolve your server membership.",
            "source": "member_missing",
        }

    key = normalize_panel_key(panel_key or resolve_panel_key_from_interaction(interaction))
    category = normalize_category_slug(
        category_slug or resolve_category_from_interaction(interaction),
        default="",
    )

    return await evaluate_panel_ticket_request(
        member=resolved_member,
        panel_key=key,
        category_slug=category or None,
        is_ghost=is_ghost,
        enforce_owner_limit=True,
    )


async def maybe_send_panel_denial(
    interaction: discord.Interaction,
    decision: Dict[str, Any],
) -> bool:
    if _safe_bool(decision.get("ok"), False):
        return False

    message = build_panel_denial_message(decision)

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return True
    except Exception:
        return False
