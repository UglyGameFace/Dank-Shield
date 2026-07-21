from __future__ import annotations

"""Solid public /dank setup flow.

This file is the public setup source of truth.

Design goals:
- One obvious command: /dank setup
- Simple language for server owners
- Purpose-based setup, not fixed role names
- Fully custom roles/channels/categories using Discord pickers
- Setup-builder choices are explicit and protected from accidental overwrite
- Auto-build fills missing items only
- Back / View Current Setup / Close controls on setup screens
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

import discord

from .common import safe_defer, safe_followup, safe_interaction_error
from .public_setup_group import (
    _build_setup_health,
    _can_manage_role,
    _category_missing_perms,
    _config_embed,
    _field_text,
    _require_setup_permission,
    _safe_str,
    _text_channel_missing_perms,
    _utc_iso,
    dank_group,
)
from ..globals import get_supabase, now_utc
from ..guild_config import get_guild_config, invalidate_guild_config
from ..setup_service_state import service_state_from_config
from ..tickets_new.panel import _DEFAULT_BOOTSTRAP_CATEGORIES
from .ticket_category_admin import _ALLOWED_INTAKE_TYPES


_ATTACHED = False

RECOMMENDED_CATEGORIES: tuple[dict[str, Any], ...] = tuple(
    dict(item) for item in _DEFAULT_BOOTSTRAP_CATEGORIES
)

INTAKE_TYPE_OPTIONS: tuple[str, ...] = tuple(
    sorted(value for value in _ALLOWED_INTAKE_TYPES if value != "ghost")
)


CONTROL_KEYS = {
    "__config_write_mode",
    "__config_write_source",
    "__config_write_allow_keys",
    "__config_write_dry_run",
}


@dataclass(frozen=True)
class CategoryLoad:
    rows: list[dict[str, Any]]
    error: str = ""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _short(value: Any, limit: int = 90) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _mention(obj: Any) -> str:
    mention = getattr(obj, "mention", None)
    return str(mention) if mention else f"`{getattr(obj, 'name', obj)}`"


def _snowflake(value: Any) -> str:
    return str(int(getattr(value, "id", value)))


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me
    except Exception:
        return None


def _cfg_value(cfg: Any, key: str) -> Any:
    try:
        if hasattr(cfg, "get"):
            return cfg.get(key)
    except Exception:
        pass
    try:
        return getattr(cfg, key, None)
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _setup_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    except Exception:
        pass
    return bool(default)


def _none_if_blank(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:50] or "custom"


def _split_keywords(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    out: list[str] = []
    for item in re.split(r"[,\n]", text):
        cleaned = item.strip().lower()
        if cleaned and cleaned not in out:
            out.append(cleaned[:40])
    return out[:20]


def _line_list(lines: Iterable[str], *, empty: str = "None", limit: int = 1000) -> str:
    kept: list[str] = []
    total = 0
    source = [str(x).strip() for x in lines if str(x).strip()]
    if not source:
        return empty
    for line in source:
        if total + len(line) + 1 > limit:
            kept.append(f"…and {len(source) - len(kept)} more")
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(kept)[:limit] or empty


def _channel_or_not_set(guild: discord.Guild, value: Any) -> str:
    channel = guild.get_channel(_safe_int(value, 0))
    return _mention(channel) if channel else "`Not set`"


def _role_or_not_set(guild: discord.Guild, value: Any) -> str:
    role = guild.get_role(_safe_int(value, 0))
    return _mention(role) if role else "`Not set`"


async def _safe_defer_update(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=False)
    except Exception:
        pass


async def _edit_or_followup(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
        return
    except Exception:
        pass

    try:
        await interaction.followup.send(
            embed=embed,
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        pass


async def _send_ephemeral(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


async def _save_config(interaction: discord.Interaction, payload: dict[str, Any]) -> None:
    """Save owner-picked setup choices through the protected setup writer."""
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This must be used inside a server.")

    from .public_setup_config_writer import upsert_guild_config

    final = dict(payload)
    final.setdefault("__config_write_mode", "setup_builder")
    final.setdefault("__config_write_source", "/dank setup guided builder")
    final.update(
        {
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        }
    )
    await upsert_guild_config(guild.id, final)
    invalidate_guild_config(guild.id)


async def _clear_config_keys(interaction: discord.Interaction, keys: Iterable[str]) -> None:
    """Clear optional setup slots.

    The public setup writer intentionally ignores empty snowflakes to prevent
    accidental data loss. This clear path is explicit and only available inside
    /dank setup.
    """
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This must be used inside a server.")

    keys = [str(k) for k in keys if str(k).strip() and str(k) not in CONTROL_KEYS]
    if not keys:
        return

    def _sync() -> None:
        sb = get_supabase()
        if sb is None:
            raise RuntimeError("Supabase is not available.")

        table = "guild_configs"
        try:
            import os

            table = (os.getenv("DANK_GUILD_CONFIG_TABLE") or table).strip() or table
        except Exception:
            pass

        res = sb.table(table).select("*").eq("guild_id", str(int(guild.id))).limit(1).execute()
        rows = getattr(res, "data", None) or []
        existing = rows[0] if rows and isinstance(rows[0], Mapping) else {}

        payload: dict[str, Any] = {
            "updated_at": _utc_iso(),
            "config_last_write_mode": "explicit_clear",
            "config_last_write_source": "/dank setup clear optional slots",
            "config_last_write_at": _utc_iso(),
        }

        for key in keys:
            if key in existing:
                payload[key] = None

        for json_key in ("settings", "config", "metadata", "meta"):
            current = existing.get(json_key) if isinstance(existing, Mapping) else None
            if isinstance(current, Mapping):
                next_payload = dict(current)
                for key in keys:
                    next_payload.pop(key, None)
                payload[json_key] = next_payload

        if existing:
            sb.table(table).update(payload).eq("guild_id", str(int(guild.id))).execute()
        else:
            payload["guild_id"] = str(int(guild.id))
            sb.table(table).upsert(payload).execute()

    await asyncio.to_thread(_sync)
    invalidate_guild_config(guild.id)


# ---------------------------------------------------------------------------
# health / summaries
# ---------------------------------------------------------------------------


async def _category_load(guild: discord.Guild) -> CategoryLoad:
    try:
        from . import ticket_category_admin as category_admin
    except Exception:
        category_admin = None

    def _read_sync() -> CategoryLoad:
        sb = get_supabase()
        if sb is None:
            return CategoryLoad([], "Supabase is not available, so ticket menu options cannot be checked.")
        try:
            res = (
                sb.table("ticket_categories")
                .select("*")
                .eq("guild_id", str(int(guild.id)))
                .execute()
            )
            rows_raw = getattr(res, "data", None) or []
            rows: list[dict[str, Any]] = []
            for item in rows_raw:
                if not isinstance(item, dict):
                    continue
                if category_admin is not None:
                    try:
                        item = category_admin._normalize_category_row(item)
                    except Exception:
                        pass
                rows.append(dict(item))
            rows.sort(
                key=lambda r: (
                    r.get("sort_order") is None,
                    r.get("sort_order") if r.get("sort_order") is not None else 10_000,
                    str(r.get("name") or "").lower(),
                    str(r.get("slug") or "").lower(),
                )
            )
            return CategoryLoad(rows, "")
        except Exception as e:
            return CategoryLoad([], f"Could not read `ticket_categories`: {type(e).__name__}: {str(e)[:350]}")

    return await asyncio.to_thread(_read_sync)


def _category_line(row: dict[str, Any]) -> str:
    slug = str(row.get("slug") or "unknown")
    name = str(row.get("name") or slug)
    intake_type = str(row.get("intake_type") or "custom")
    default = " ⭐" if bool(row.get("is_default")) else ""
    keywords = row.get("match_keywords") or []
    keyword_text = ", ".join(str(x) for x in keywords[:4]) if keywords else "no keywords"
    order = row.get("sort_order")
    order_text = f" • sort `{order}`" if order is not None else ""
    return f"• **{_short(name, 48)}**{default} — `{slug}` • `{intake_type}`{order_text}\n  ↳ {_short(keyword_text, 90)}"


def _category_list_text(rows: list[dict[str, Any]], *, empty: str = "No ticket menu options yet.") -> str:
    if not rows:
        return empty
    lines = [_category_line(row) for row in rows[:12]]
    if len(rows) > 12:
        lines.append(f"…and {len(rows) - 12} more")
    return "\n".join(lines)[:1024] or empty



def _category_member_preview_text(rows: list[dict[str, Any]]) -> str:
    """Plain preview of what members/staff will see in the ticket menu."""

    if not rows:
        return "No choices yet. Press **Create Missing Recommended Options** to add safe defaults."

    lines: list[str] = []
    for idx, row in enumerate(rows[:10], start=1):
        name = _short(row.get("name") or row.get("slug") or "Ticket choice", 42)
        desc = _short(row.get("description") or "No description set.", 72)
        default = " ⭐ default fallback" if bool(row.get("is_default")) else ""
        lines.append(f"{idx}. **{name}**{default}\n   ↳ {desc}")

    if len(rows) > 10:
        lines.append(f"…and {len(rows) - 10} more choices")

    return "\n".join(lines)[:1024] or "No choices yet."


def _category_governance_text(rows: list[dict[str, Any]]) -> str:
    warnings: list[str] = []
    if not rows:
        return "⚠️ No ticket menu options exist yet. Users may only get a generic support path."
    if not any(bool(row.get("is_default")) for row in rows):
        warnings.append("Pick one default option so unclear tickets have somewhere to go.")
    slugs = [str(row.get("slug") or "").strip().lower() for row in rows]
    if len(slugs) != len(set(slugs)):
        warnings.append("Two options share the same slug. Rename one so routing stays predictable.")
    if not warnings:
        return "✅ Ticket menu options look safe."
    return "\n".join(f"• {item}" for item in warnings)[:1024]


def _setup_doc_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    except Exception:
        pass
    return bool(default)


def _setup_doc_cfg_bool(cfg: Any, *keys: str, default: bool = False) -> bool:
    for key in keys:
        value = _cfg_value(cfg, key)
        if value is not None:
            return _setup_doc_bool(value, default)
    return bool(default)


def _setup_doc_has_id(cfg: Any, *keys: str) -> bool:
    for key in keys:
        if _safe_int(_cfg_value(cfg, key), 0) > 0:
            return True
    return False


def _setup_doc_features(cfg: Any) -> dict[str, bool]:
    """Use the same feature truth as Setup Home and Test Your Setup."""
    state = service_state_from_config(cfg)
    return {
        "tickets": bool(state.tickets),
        "basic_verify": bool(state.simple_verify),
        "vc_verify": bool(state.voice_verify),
        "logs": bool(state.logs),
    }

_LAYOUT_ONLY_PHRASES = (
    "wrong category",
    "expected it under",
    "expected under",
    "not grouped with",
    "split across categories",
    "in different categories",
    "category name looks unusual",
    "name looks unusual",
    "separate channels are cleaner",
    "same category",
    "category order",
    "cleaner layout",
    "between active tickets and archive",
    "public/start",
    "staff/tools",
)


_OPTIONAL_PERMISSION_PHRASES = (
    "view audit log",
    "manage messages",
    "kick members",
    "moderate members",
    "ban members",
)


_REQUIRED_PERMISSION_PHRASES = (
    "manage channels",
    "manage roles",
    "view channel",
    "send messages",
    "read message history",
    "embed links",
)


def _setup_doc_is_layout_only(line: str) -> bool:
    low = str(line or "").lower()
    return any(phrase in low for phrase in _LAYOUT_ONLY_PHRASES)


def _setup_doc_is_optional_control(line: str) -> bool:
    low = str(line or "").lower()
    return "server-control role" in low or "server control role" in low


def _setup_doc_is_optional_permission_only(line: str) -> bool:
    low = str(line or "").lower()
    if "permission" not in low:
        return False
    has_optional = any(phrase in low for phrase in _OPTIONAL_PERMISSION_PHRASES)
    has_required = any(phrase in low for phrase in _REQUIRED_PERMISSION_PHRASES)
    return bool(has_optional and not has_required)


def _setup_doc_is_vc_only(line: str) -> bool:
    low = str(line or "").lower()
    return "vc verify" in low or "vc verification" in low or "voice verification" in low or "vc queue" in low


def _setup_doc_dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _setup_doctor_truth_filter(
    cfg: Any,
    blockers: list[str],
    warnings: list[str],
    ok: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Final severity sanitizer for the live /dank setup check embed.

    Blockers must be runtime-breaking. Layout, optional control, disabled VC,
    and useful-but-not-required permission notes become warnings/ok.
    """

    features = _setup_doc_features(cfg)

    new_blockers: list[str] = []
    new_warnings: list[str] = list(warnings)
    new_ok: list[str] = list(ok)

    for line in blockers:
        value = str(line or "").strip()
        if not value:
            continue

        if _setup_doc_is_optional_control(value):
            new_warnings.append(
                "Optional setup control role is not saved. Admin/Manage Server users can still configure setup."
            )
            continue

        if _setup_doc_is_layout_only(value):
            new_warnings.append("Layout/style cleanup: " + value)
            continue

        if _setup_doc_is_optional_permission_only(value):
            new_warnings.append("Useful optional permission: " + value)
            continue

        if _setup_doc_is_vc_only(value) and not features.get("vc_verify", False):
            new_ok.append("VC Verify is disabled/not configured, so VC-only missing items are not blockers.")
            continue

        new_blockers.append(value)

    new_ok.append(
        "Detected feature scope: "
        f"tickets={'on' if features.get('tickets') else 'off'}, "
        f"basic verify={'on' if features.get('basic_verify') else 'off'}, "
        f"vc verify={'on' if features.get('vc_verify') else 'off'}, "
        f"logs={'on' if features.get('logs') else 'off'}."
    )

    return (
        _setup_doc_dedupe(new_blockers),
        _setup_doc_dedupe(new_warnings),
        _setup_doc_dedupe(new_ok),
    )


async def _build_health_embed(guild: discord.Guild) -> discord.Embed:
    """Dashboard-style health check.

    This reuses the existing _build_setup_health() checks. It does not create a
    second health system; it only groups the same evidence into customer-readable
    service cards.
    """

    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    cfg: Any = None
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        b, w, p = _build_setup_health(guild, cfg)
        blockers.extend([str(x).replace("/dank setup-tickets", "/dank setup") for x in b])
        warnings.extend([str(x).replace("/dank setup-tickets", "/dank setup") for x in w])
        ok.extend(str(x) for x in p)
    except Exception as e:
        blockers.append(f"Could not load this server's saved setup: {type(e).__name__}: {str(e)[:250]}")

    category_load = await _category_load(guild)
    if category_load.error:
        blockers.append(category_load.error)
    elif not category_load.rows:
        warnings.append("Ticket menu options are missing. Press Tickets → Ticket Menu Options → Create Recommended.")
    else:
        ok.append(f"Ticket menu options loaded: `{len(category_load.rows)}`.")
        governance = _category_governance_text(category_load.rows)
        if governance.startswith("•") or governance.startswith("⚠️"):
            warnings.append(governance)
        else:
            ok.append(governance)

    blockers, warnings, ok = _setup_doctor_truth_filter(cfg, blockers, warnings, ok)

    from stoney_verify.setup_doctor_canonical import normalize_setup_health, truth_rules_text

    doctor = normalize_setup_health(cfg=cfg, blockers=blockers, warnings=warnings, ok=ok)
    blockers, warnings, ok = doctor.blockers, doctor.warnings, doctor.ok

    def _matches(line: str, words: tuple[str, ...]) -> bool:
        low = str(line or "").lower()
        return any(word in low for word in words)

    buckets: dict[str, tuple[str, tuple[str, ...]]] = {
        "🎫 Tickets": (
            "ticket",
            (
                "ticket",
                "tickets",
                "archive",
                "archived",
                "closed",
                "open ticket",
                "panel",
                "transcript",
                "category",
                "staff role",
                "menu option",
                "routing",
            ),
        ),
        "✅ Verify": (
            "verify",
            (
                "verify",
                "verification",
                "verified",
                "unverified",
                "resident",
                "waiting role",
                "approved role",
                "access role",
                "voice",
                "vc",
                "kick timer",
            ),
        ),
        "🛡️ Protection": (
            "protection",
            (
                "spam",
                "spamguard",
                "spam guard",
                "automod",
                "invite",
                "link",
                "protection",
                "shield",
                "filter",
            ),
        ),
        "🧾 Logs + Status": (
            "logs",
            (
                "log",
                "logs",
                "modlog",
                "mod/security",
                "status",
                "join",
                "leave",
                "raid",
                "transcript",
            ),
        ),
        "🔐 Permissions": (
            "permissions",
            (
                "permission",
                "permissions",
                "manage",
                "read",
                "send",
                "view",
                "role hierarchy",
                "hierarchy",
                "missing access",
            ),
        ),
    }

    all_evidence: list[tuple[str, str]] = (
        [("blocker", item) for item in blockers]
        + [("warning", item) for item in warnings]
        + [("ok", item) for item in ok]
    )

    used: set[int] = set()

    def _bucket_lines(words: tuple[str, ...], *, limit: int = 5) -> str:
        selected: list[str] = []

        for severity in ("blocker", "warning", "ok"):
            for idx, (sev, line) in enumerate(all_evidence):
                if idx in used or sev != severity:
                    continue
                if not _matches(line, words):
                    continue

                used.add(idx)
                icon = "🚫" if sev == "blocker" else "⚠️" if sev == "warning" else "✅"
                selected.append(f"{icon} {_short(line, 155)}")
                if len(selected) >= limit:
                    return "\n".join(selected)[:1024]

        if not selected:
            return "✅ No obvious issue found for this section."
        return "\n".join(selected)[:1024]

    ready = not blockers
    embed = discord.Embed(
        title="🩺 Dank Shield Setup Check",
        description=(
            "🚫 **Fix blockers first.**" if blockers else
            "⚠️ **Usable, but review warnings before public launch.**" if warnings else
            "✅ **Looks ready to test.**"
        ),
        color=discord.Color.red() if blockers else discord.Color.orange() if warnings else discord.Color.green(),
        timestamp=now_utc(),
    )

    embed.add_field(
        name="Truth Rules",
        value=(
            "❌ **Blocker** = enabled feature cannot run, saved ID is missing/deleted/wrong type, or required permission is missing.\n"
            "⚠️ **Warning** = optional feature incomplete, useful permission missing, or layout/privacy/style cleanup.\n"
            "✅ **Passing** = saved item exists and is usable for the detected feature scope."
        )[:1024],
        inline=False,
    )

    embed.add_field(
        name="Summary",
        value=(
            f"Blockers: `{len(blockers)}`\n"
            f"Warnings: `{len(warnings)}`\n"
            f"Passing checks: `{len(ok)}`"
        ),
        inline=True,
    )

    if blockers:
        next_action = f"Fix this first: {_short(blockers[0], 220)}"
    elif warnings:
        next_action = f"Review this next: {_short(warnings[0], 220)}"
    else:
        next_action = "Create a test ticket, close it, test verify, then test invite/protection."

    embed.add_field(name="Next Best Action", value=next_action[:1024], inline=False)

    for label, (_slug, words) in buckets.items():
        embed.add_field(name=label, value=_bucket_lines(words), inline=False)

    leftovers: list[str] = []
    for idx, (sev, line) in enumerate(all_evidence):
        if idx in used:
            continue
        icon = "🚫" if sev == "blocker" else "⚠️" if sev == "warning" else "✅"
        leftovers.append(f"{icon} {_short(line, 150)}")
        if len(leftovers) >= 5:
            break

    if leftovers:
        embed.add_field(name="Other Checks", value="\n".join(leftovers)[:1024], inline=False)

    embed.add_field(
        name="What To Press Next",
        value=(
            "• Use **View Current Setup** to see saved IDs.\n"
            "• Use **Use My Existing Server** to map existing channels/roles.\n"
            "• Use **Review / Create Missing Items** only when something is missing."
        ),
        inline=False,
    )

    embed.set_footer(text=f"Guild {guild.id} • setup check groups existing health evidence")
    return embed


async def _build_current_setup_embed(guild: discord.Guild) -> discord.Embed:
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        embed = discord.Embed(
            title="📋 Current Setup",
            description=f"Could not load setup: `{type(e).__name__}: {str(e)[:250]}`",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )
        return embed

    embed = _config_embed(guild, cfg, title="📋 Current Setup")
    embed.description = (
        "This is what Dank Shield currently has saved for this server.\n"
        "Names can be anything. Dank Shield uses the saved Discord IDs."
    )
    embed.add_field(
        name="Access Role Style",
        value=(
            f"Mode: `{_safe_str(_cfg_value(cfg, 'verification_mode'), 'not chosen')}`\n"
            f"New/waiting role: {_role_or_not_set(guild, _cfg_value(cfg, 'unverified_role_id'))}\n"
            f"Approved role: {_role_or_not_set(guild, _cfg_value(cfg, 'verified_role_id'))}\n"
            f"Full access/member role: {_role_or_not_set(guild, _cfg_value(cfg, 'resident_role_id'))}"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Owner Controls",
        value=(
            f"Server-control role: {_role_or_not_set(guild, _cfg_value(cfg, 'server_control_role_id') or _cfg_value(cfg, 'control_role_id') or _cfg_value(cfg, 'perm_role_id'))}\n"
            f"Ticket staff role: {_role_or_not_set(guild, _cfg_value(cfg, 'staff_role_id'))}\n"
            f"Ticket prefix: `{_safe_str(_cfg_value(cfg, 'ticket_prefix'), 'ticket')}`\n"
            f"Verify kick hours: `{_safe_str(_cfg_value(cfg, 'verify_kick_hours'), '24')}`"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Main Channels / Categories",
        value=(
            f"Open tickets: {_channel_or_not_set(guild, _cfg_value(cfg, 'ticket_category_id'))}\n"
            f"Closed tickets: {_channel_or_not_set(guild, _cfg_value(cfg, 'ticket_archive_category_id'))}\n"
            f"Ticket panel: {_channel_or_not_set(guild, _cfg_value(cfg, 'ticket_panel_channel_id') or _cfg_value(cfg, 'support_channel_id'))}\n"
            f"Transcripts: {_channel_or_not_set(guild, _cfg_value(cfg, 'transcripts_channel_id'))}\n"
            f"Mod/security log: {_channel_or_not_set(guild, _cfg_value(cfg, 'modlog_channel_id') or _cfg_value(cfg, 'raidlog_channel_id'))}\n"
            f"Status: {_channel_or_not_set(guild, _cfg_value(cfg, 'status_channel_id') or _cfg_value(cfg, 'bot_status_channel_id'))}"
        )[:1024],
        inline=False,
    )
    return embed



async def _add_saved_setup_section(embed: discord.Embed, guild: discord.Guild, section: str) -> None:
    """Add a small current-saved snapshot to the exact setup section being edited.

    This intentionally reuses existing config helpers in this file. It is not a
    second setup summary system and does not write config or create anything.
    """

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        embed.add_field(
            name="Currently Saved",
            value=f"Could not load saved setup: `{type(e).__name__}: {str(e)[:220]}`",
            inline=False,
        )
        return

    section = str(section or "").strip().lower()

    if section == "ticket_basics":
        value = (
            f"Open tickets: {_channel_or_not_set(guild, _cfg_value(cfg, 'ticket_category_id'))}\n"
            f"Closed/archive: {_channel_or_not_set(guild, _cfg_value(cfg, 'ticket_archive_category_id') or _cfg_value(cfg, 'ticket_closed_category_id'))}\n"
            f"Ticket panel: {_channel_or_not_set(guild, _cfg_value(cfg, 'ticket_panel_channel_id') or _cfg_value(cfg, 'support_channel_id'))}\n"
            f"Staff role: {_role_or_not_set(guild, _cfg_value(cfg, 'staff_role_id') or _cfg_value(cfg, 'ticket_staff_role_id'))}\n"
            f"Transcripts: {_channel_or_not_set(guild, _cfg_value(cfg, 'transcripts_channel_id') or _cfg_value(cfg, 'transcript_channel_id'))}"
        )
    elif section == "access_roles":
        value = (
            f"Mode: `{_safe_str(_cfg_value(cfg, 'verification_mode'), 'not chosen')}`\n"
            f"New/waiting role: {_role_or_not_set(guild, _cfg_value(cfg, 'unverified_role_id') or _cfg_value(cfg, 'waiting_role_id'))}\n"
            f"Approved role: {_role_or_not_set(guild, _cfg_value(cfg, 'verified_role_id') or _cfg_value(cfg, 'approved_role_id'))}\n"
            f"Full access role: {_role_or_not_set(guild, _cfg_value(cfg, 'resident_role_id') or _cfg_value(cfg, 'member_role_id'))}\n"
            f"Server-control role: {_role_or_not_set(guild, _cfg_value(cfg, 'server_control_role_id') or _cfg_value(cfg, 'control_role_id') or _cfg_value(cfg, 'perm_role_id'))}"
        )
    elif section == "verification_channels":
        value = (
            f"Verify text channel: {_channel_or_not_set(guild, _cfg_value(cfg, 'verify_channel_id') or _cfg_value(cfg, 'verification_channel_id'))}\n"
            f"Voice verify channel: {_channel_or_not_set(guild, _cfg_value(cfg, 'vc_verify_channel_id') or _cfg_value(cfg, 'voice_verify_channel_id'))}\n"
            f"Staff VC queue: {_channel_or_not_set(guild, _cfg_value(cfg, 'vc_verify_queue_channel_id') or _cfg_value(cfg, 'vc_verify_requests_channel_id'))}\n"
            f"Welcome/start channel: {_channel_or_not_set(guild, _cfg_value(cfg, 'welcome_channel_id'))}"
        )
    elif section == "logs_status":
        value = (
            f"Mod/security log: {_channel_or_not_set(guild, _cfg_value(cfg, 'modlog_channel_id') or _cfg_value(cfg, 'raidlog_channel_id') or _cfg_value(cfg, 'raid_log_channel_id'))}\n"
            f"Ticket transcripts: {_channel_or_not_set(guild, _cfg_value(cfg, 'transcripts_channel_id') or _cfg_value(cfg, 'transcript_channel_id'))}\n"
            f"Join/leave log: {_channel_or_not_set(guild, _cfg_value(cfg, 'join_log_channel_id') or _cfg_value(cfg, 'join_exit_log_channel_id'))}\n"
            f"Bot status: {_channel_or_not_set(guild, _cfg_value(cfg, 'status_channel_id') or _cfg_value(cfg, 'bot_status_channel_id') or _cfg_value(cfg, 'health_channel_id'))}"
        )
    elif section == "behavior":
        wait_enabled = _setup_bool(_cfg_value(cfg, "verification_wait_timer_enabled"), False)
        idle_enabled = _setup_bool(_cfg_value(cfg, "verification_idle_kick_enabled"), False)
        value = (
            f"Verification mode: `{_safe_str(_cfg_value(cfg, 'verification_mode'), 'not chosen')}`\n"
            f"Ticket prefix: `{_safe_str(_cfg_value(cfg, 'ticket_prefix'), 'ticket')}`\n"
            f"Verification wait timer: `{'ON' if wait_enabled else 'OFF'}`\n"
            f"Wait timer hours: `{_safe_str(_cfg_value(cfg, 'verify_kick_hours'), '24')}`\n"
            f"No-start auto-remove: `{'ON' if idle_enabled else 'OFF'}`\n"
            f"No-start minutes: `{_safe_str(_cfg_value(cfg, 'verification_idle_kick_minutes'), '60')}`"
        )
    else:
        value = "No section snapshot available."

    embed.add_field(name="Currently Saved", value=value[:1024], inline=False)
    embed.add_field(
        name="Safe Rule",
        value="Changing this section only saves the selected Discord IDs/settings. It does not delete existing channels, roles, tickets, or messages.",
        inline=False,
    )



def _saved_or_missing_channel(guild: discord.Guild, cfg: Any, label: str, *keys: str, required: bool = True) -> str:
    value = None
    for key in keys:
        value = _cfg_value(cfg, key)
        if _safe_int(value, 0) > 0:
            break

    cid = _safe_int(value, 0)
    if cid <= 0:
        icon = "⚠️" if required else "—"
        return f"{icon} **{label}:** not saved"

    channel = guild.get_channel(cid)
    if channel is None:
        return f"⚠️ **{label}:** saved ID `{cid}` but not found in this server"

    return f"✅ **{label}:** {_mention(channel)}"


def _saved_or_missing_role(guild: discord.Guild, cfg: Any, label: str, *keys: str, required: bool = True) -> str:
    value = None
    for key in keys:
        value = _cfg_value(cfg, key)
        if _safe_int(value, 0) > 0:
            break

    rid = _safe_int(value, 0)
    if rid <= 0:
        icon = "⚠️" if required else "—"
        return f"{icon} **{label}:** not saved"

    role = guild.get_role(rid)
    if role is None:
        return f"⚠️ **{label}:** saved ID `{rid}` but not found in this server"

    return f"✅ **{label}:** {_mention(role)}"


async def _missing_items_preview_text(guild: discord.Guild) -> tuple[str, str]:
    """Return already-saved/missing checklist for the create-missing review page.

    Read-only. No config writes, no role/channel creation, no deletes.
    """

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return (
            "",
            f"⚠️ Could not load saved setup: `{type(e).__name__}: {str(e)[:220]}`",
        )

    lines = [
        _saved_or_missing_channel(guild, cfg, "Open ticket category", "ticket_category_id", "active_ticket_category_id", "open_ticket_category_id"),
        _saved_or_missing_channel(guild, cfg, "Archive ticket category", "ticket_archive_category_id", "ticket_closed_category_id", "archive_ticket_category_id", "closed_ticket_category_id"),
        _saved_or_missing_channel(guild, cfg, "Ticket panel channel", "ticket_panel_channel_id", "support_channel_id"),
        _saved_or_missing_role(guild, cfg, "Ticket staff role", "staff_role_id", "ticket_staff_role_id"),
        _saved_or_missing_channel(guild, cfg, "Transcript channel", "transcripts_channel_id", "transcript_channel_id", required=False),
        _saved_or_missing_channel(guild, cfg, "Verify channel", "verify_channel_id", "verification_channel_id", required=False),
        _saved_or_missing_role(guild, cfg, "Waiting/unverified role", "unverified_role_id", "waiting_role_id", required=False),
        _saved_or_missing_role(guild, cfg, "Approved/verified role", "verified_role_id", "approved_role_id", required=False),
        _saved_or_missing_channel(guild, cfg, "Mod/security log", "modlog_channel_id", "raidlog_channel_id", "raid_log_channel_id", required=False),
        _saved_or_missing_channel(guild, cfg, "Bot status channel", "status_channel_id", "bot_status_channel_id", "health_channel_id", required=False),
    ]

    saved = [line for line in lines if line.startswith("✅")]
    missing = [line for line in lines if not line.startswith("✅")]

    saved_text = "\n".join(saved[:8]) if saved else "Nothing obvious is saved yet."
    missing_text = "\n".join(missing[:8]) if missing else "✅ Core setup items are already saved. Build should not need to create core defaults."

    return saved_text[:1024], missing_text[:1024]


async def _build_main_setup_payload(
    guild: discord.Guild,
) -> tuple[discord.Embed, discord.ui.View]:
    """Return the one canonical Dank Shield setup home.

    Older setup buttons still call this compatibility owner.
    It deliberately delegates to the native guided product
    home instead of rebuilding the retired Solid dashboard.
    """

    from . import public_setup_recommend as recommend

    return await recommend._product_main_setup_payload(
        guild
    )


# Official /dank setup home owner. Startup guards must not replace this.


# ---------------------------------------------------------------------------
# reusable navigation
# ---------------------------------------------------------------------------



class SetupNavView(discord.ui.View):
    """Universal navigation for nested setup tools."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[Any],
    ) -> None:
        try:
            item_label = (
                getattr(item, "label", None)
                or getattr(item, "placeholder", None)
                or getattr(item, "custom_id", None)
                or "setup item"
            )
        except Exception:
            item_label = "setup item"

        await safe_interaction_error(
            interaction,
            title="Setup Action Failed",
            error=error,
            hint=(
                f"The **{item_label}** action failed safely. Nothing was changed. "
                "Press **Back to All Features**, **Setup Home**, or reopen `/dank setup`."
            ),
            view=self,
        )

    @discord.ui.button(
        label="Back to All Features",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_nested:features",
        row=4,
    )
    async def all_features(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        from . import public_setup_recommend as recommend
        await recommend._open_advanced_settings(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_nested:home",
        row=4,
    )
    async def setup_home(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        await _safe_defer_update(interaction)
        embed, view = await _build_main_setup_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_nested:close",
        row=4,
    )
    async def close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="Setup Closed",
            description=(
                "Nothing else was changed. Run `/dank setup` whenever "
                "you want to continue."
            ),
            color=discord.Color.dark_grey(),
            timestamp=now_utc(),
        )
        await _edit_or_followup(interaction, embed=embed, view=None)

BackToSetupView = SetupNavView


# ---------------------------------------------------------------------------
# main setup view
# ---------------------------------------------------------------------------


class SolidSetupView(discord.ui.View):
    """Dashboard-first /dank setup home.

    Home should only show the big decisions. Feature-specific tools live behind
    Configure Features so the first screen does not overwhelm new owners.
    """

    def __init__(self) -> None:
        super().__init__(timeout=900)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        try:
            item_label = getattr(item, "label", None) or getattr(item, "placeholder", None) or getattr(item, "custom_id", None) or "setup item"
        except Exception:
            item_label = "setup item"

        await safe_interaction_error(
            interaction,
            title="Setup Action Failed",
            error=error,
            hint=f"The **{item_label}** action failed safely. Nothing was changed. Reopen `/dank setup` or press Refresh.",
            view=self,
        )

    # Row 0 — inspect first
    @discord.ui.button(label="View Current Setup", emoji="📋", style=discord.ButtonStyle.primary, custom_id="stoney_solid:dashboard_current", row=0)
    async def current_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed = await _build_current_setup_embed(guild)
        await _edit_or_followup(interaction, embed=embed, view=SetupNavView())

    @discord.ui.button(label="Run Setup Check", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="stoney_solid:dashboard_health", row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed = await _build_health_embed(guild)
        await _edit_or_followup(interaction, embed=embed, view=SetupNavView())

    # Row 1 — choose setup path
    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="stoney_solid:dashboard_existing", row=1)
    async def choose_existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧩 Use My Existing Server",
            description=(
                "Pick what your server already uses. Names do not matter. Dank Shield saves IDs, not names.\n\n"
                "**Best for most servers:** use this before creating anything new."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Sections",
            value=(
                "🎫 **Ticket Basics** — open/archive ticket folders, staff role, ticket panel\n"
                "🎭 **Access Roles** — waiting, approved, full access, setup admin\n"
                "🎙️ **Verification Channels** — text verify, voice verify, staff queue\n"
                "🧾 **Logs + Status** — logs, transcripts, status\n"
                "⚙️ **Behavior Settings** — mode, ticket prefix, kick timer"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ChooseExistingView())

    @discord.ui.button(label="Review / Create Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_solid:dashboard_review_create", row=1)
    async def review_create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        saved_text, missing_text = await _missing_items_preview_text(guild)

        embed = discord.Embed(
            title="✨ Review / Create Missing Items",
            description="Review this before building. This page is read-only until you press **Build Missing Items**.",
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Already Saved / Found", value=saved_text, inline=False)
        embed.add_field(name="Missing / Needs Review", value=missing_text, inline=False)
        embed.add_field(
            name="What Build Missing Items Does",
            value=(
                "Creates safe default roles/channels/categories only when missing.\n"
                "Does not delete existing server items.\n"
                "Does not replace saved choices.\n"
                "If this page says everything important is saved, run **Setup Check** instead of building."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=BuildMissingItemsReviewView())

    @discord.ui.button(label="Custom Setup", emoji="🧩", style=discord.ButtonStyle.success, custom_id="stoney_solid:dashboard_custom_setup", row=2)
    async def custom_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await _safe_defer_update(interaction)
        try:
            from . import public_setup_fresh_choice
            await public_setup_fresh_choice._open_custom_service_picker(interaction)  # type: ignore[attr-defined]
        except Exception as e:
            await safe_interaction_error(
                interaction,
                title="Custom Setup Did Not Open",
                error=e,
                hint="Nothing was changed. Use Configure Features or Use My Existing Server while this is repaired.",
                view=self,
            )

    # Row 2 — extra setup work
    @discord.ui.button(label="Configure Features", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:dashboard_features", row=2)
    async def features(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧭 Configure Features",
            description=(
                "Pick the feature you want to adjust. These screens save IDs/settings only; they do not delete server items."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Best Order",
            value="Tickets → Verify → Protection → Logs + Status → Behavior Settings",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ConfigureFeaturesHubView())

    @discord.ui.button(label="Name Items Before Build", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:dashboard_customize", row=2)
    async def customize(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        try:
            from .public_setup_start import CustomizeSetupMenuView

            embed = discord.Embed(
                title="✏️ Name Items Before Build",
                description=(
                    "Use this before building if you want Dank Shield-created missing items to use your names.\n\n"
                    "**This does not create anything by itself.** It only opens naming forms."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Best Workflow",
                value="Name items here → Setup Home → Review / Create Missing Items.",
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=CustomizeSetupMenuView())
        except Exception as e:
            await safe_interaction_error(
                interaction,
                title="Name Editor Did Not Open",
                error=e,
                hint="Nothing was changed. Use **Use My Existing Server** to pick current roles/channels instead.",
                view=SetupNavView(),
            )

    # Row 3 — repair
    @discord.ui.button(label="Cleanup / Repair", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:dashboard_cleanup", row=3)
    async def cleanup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧹 Cleanup / Repair",
            description=(
                "Use cleanup only when setup got messy.\n\n"
                "**Safe rule:** cleanup should only remove things Dank Shield created or things you explicitly pick. "
                "It should not delete unrelated server channels, roles, tickets, or messages.\n\n"
                "Use `/dank cleanup` for cleanup tools, then return to `/dank setup`."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Recommended Order",
            value=(
                "1. View Current Setup.\n"
                "2. Run Setup Check.\n"
                "3. Repair only the listed problem.\n"
                "4. Use My Existing Server to remap anything wrong."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=SetupNavView())

    # Row 4 — navigation
    @discord.ui.button(label="Refresh Setup Home", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:dashboard_refresh", row=4)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_main_setup_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="stoney_solid:dashboard_close", row=4)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="Setup Closed",
            description="Nothing else was changed. Run `/dank setup` whenever you want to continue.",
            color=discord.Color.dark_grey(),
        )
        await _edit_or_followup(interaction, embed=embed, view=None)


class ConfigureFeaturesHubView(SetupNavView):
    @discord.ui.button(label="Tickets", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="stoney_solid:features_tickets", row=0)
    async def tickets(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🎫 Tickets",
            description="Ticket setup is split into routing and menu options so owners know exactly what they are changing.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Recommended", value="Start with **Ticket Basics**. Then use **Ticket Menu Options** for the choices members see.", inline=False)
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "ticket_basics")
        await interaction.response.edit_message(embed=embed, view=TicketSetupHubView())

    @discord.ui.button(label="Verify", emoji="✅", style=discord.ButtonStyle.primary, custom_id="stoney_solid:features_verify", row=0)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="✅ Verify",
            description="Configure roles/channels used for verification. Basic Verify is separate from Voice Verify and ID/web verification.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "access_roles")
        await interaction.response.edit_message(embed=embed, view=VerifySetupHubView())

    @discord.ui.button(label="Protection", emoji="🛡️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:features_protection", row=0)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        try:
            from . import public_protection_center
            await public_protection_center._refresh_panel(interaction, content="🛡️ Protection Center opened from setup.")
        except Exception as e:
            await safe_interaction_error(
                interaction,
                title="Protection Center Did Not Open",
                error=e,
                hint="Nothing was changed. Run `/dank protection` directly.",
                view=self,
            )

    @discord.ui.button(label="Logs + Status", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:features_logs", row=1)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧾 Logs + Status",
            description="Pick where logs, transcripts, and status messages go. Names do not matter.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "logs_status")
        await interaction.response.edit_message(embed=embed, view=LogsSetupHubView())

    @discord.ui.button(label="Ticket Menu Options", emoji="🗂️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:features_categories", row=1)
    async def categories(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Behavior Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:features_behavior", row=1)
    async def behavior(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="⚙️ Behavior Settings",
            description="Pick verification style, ticket prefix, kick timer, and other behavior settings.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "behavior")
        await interaction.response.edit_message(embed=embed, view=BehaviorSettingsView())


class BuildMissingItemsReviewView(SetupNavView):
    @discord.ui.button(label="Build Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_solid:build_confirm", row=0)
    async def build(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        try:
            from . import public_setup_defaults

            await public_setup_defaults._setup_defaults_callback(interaction)

            if interaction.guild is not None:
                created, skipped, error = await _seed_recommended_categories(interaction.guild, managed_only=True)
                if error:
                    await interaction.followup.send(
                        f"⚠️ Auto-build ran, but ticket menu options could not be checked: `{error}`",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                elif created:
                    await interaction.followup.send(
                        f"✅ Ticket menu options created: {', '.join(f'`{x}`' for x in created)}",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                elif skipped:
                    await interaction.followup.send(
                        "✅ Ticket menu options already exist. Nothing was overwritten.",
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
        except Exception as e:
            await safe_interaction_error(
                interaction,
                title="Build Missing Items Failed",
                error=e,
                hint="Nothing else was changed. Run **Setup Check**, fix the listed blocker, then try again.",
                view=self,
            )


class TicketSetupHubView(SetupNavView):
    @discord.ui.button(label="Ticket Basics", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="stoney_solid:ticket_hub_basics", row=0)
    async def ticket_basics(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🎫 Ticket Basics",
            description="Pick where tickets go. Use your exact server items. Names do not matter. Each save is checked first.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "ticket_basics")
        await interaction.response.edit_message(embed=embed, view=TicketBasicsPickerView())

    @discord.ui.button(label="Ticket Menu Options", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:ticket_hub_menu", row=0)
    async def ticket_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)


class VerifySetupHubView(SetupNavView):
    @discord.ui.button(label="Access Roles", emoji="🎭", style=discord.ButtonStyle.primary, custom_id="stoney_solid:verify_hub_roles", row=0)
    async def access_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🎭 Access Roles",
            description=(
                "Pick the exact roles your server uses. Names do not matter.\n"
                "Leave optional roles blank if your server does not use them."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Simple Meaning",
            value=(
                "• **New / waiting role**: role people have before approval, if used.\n"
                "• **Approved role**: role people get after passing verification, if used.\n"
                "• **Full access role**: extra member/resident role, if used.\n"
                "• **Server-control role**: people allowed to run setup/admin tools."
            ),
            inline=False,
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "access_roles")
        await interaction.response.edit_message(embed=embed, view=AccessRolesPickerView())

    @discord.ui.button(label="Verification Channels", emoji="🎙️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:verify_hub_channels", row=0)
    async def verify_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🎙️ Verification Channels",
            description="Pick the channels your verification flow uses. Leave anything unused blank.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "verification_channels")
        await interaction.response.edit_message(embed=embed, view=VerificationChannelsPickerView())

    @discord.ui.button(label="Behavior Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:verify_hub_behavior", row=1)
    async def behavior(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="⚙️ Behavior Settings",
            description="Pick verification style, ticket prefix, kick timer, and other behavior settings.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "behavior")
        await interaction.response.edit_message(embed=embed, view=BehaviorSettingsView())


# ---------------------------------------------------------------------------
# existing item setup
# ---------------------------------------------------------------------------


class ChooseExistingView(SetupNavView):
    @discord.ui.button(label="Ticket Basics", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="stoney_solid:existing_ticket", row=0)
    async def ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🎫 Ticket Basics",
            description="Pick where tickets go. Use your exact server items. Names do not matter. Each save is checked first.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "ticket_basics")
        await interaction.response.edit_message(embed=embed, view=TicketBasicsPickerView())

    @discord.ui.button(label="Access Roles", emoji="🎭", style=discord.ButtonStyle.primary, custom_id="stoney_solid:existing_roles", row=1)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🎭 Access Roles",
            description=(
                "Optional. Pick the exact roles your server uses. They can be named anything.\n"
                "If your server does not use one of these role steps, leave it blank."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Simple Meaning",
            value=(
                "• **New / waiting role**: role people have before approval, if your server uses one.\n"
                "• **Approved role**: role people get after passing verification, if used.\n"
                "• **Full access role**: extra member/resident role, if used.\n"
                "• **Server-control role**: people allowed to run setup/admin tools."
            ),
            inline=False,
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "access_roles")
        await interaction.response.edit_message(embed=embed, view=AccessRolesPickerView())

    @discord.ui.button(label="Verification Channels", emoji="🎙️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:existing_channels", row=2)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🎙️ Verification Channels",
            description="Optional. Pick the channels your verification flow uses. If your server does not use one, leave it blank.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "verification_channels")
        await interaction.response.edit_message(embed=embed, view=VerificationChannelsPickerView())

    @discord.ui.button(label="Logs + Status", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="stoney_solid:existing_logs", row=3)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧾 Logs + Status",
            description="Pick where logs and status messages go. Names do not matter.",
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "logs_status")
        await interaction.response.edit_message(embed=embed, view=LogsSetupHubView())

    @discord.ui.button(label="Behavior Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:existing_behavior", row=3)
    async def behavior(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="⚙️ Behavior Settings",
            description=(
                "Choose how strict setup should be. Keep this simple: pick the closest style, then save prefix/timer only if you need them."
            ),
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "behavior")
        await interaction.response.edit_message(embed=embed, view=BehaviorSettingsView())



class PostSaveSetupView(SetupNavView):
    def __init__(self, section: str) -> None:
        super().__init__()
        self.section = str(section or "current")

    @discord.ui.button(label="Continue This Section", emoji="➡️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:postsave_continue", row=0)
    async def continue_section(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return

        section = self.section
        embed = discord.Embed(
            title="Continue Setup",
            description="Continue editing this same section.",
            color=discord.Color.blurple(),
        )

        if section == "ticket_basics":
            embed.title = "🎫 Ticket Basics"
            embed.description = "Pick where tickets go. Use your exact server items. Names do not matter."
            if interaction.guild is not None:
                await _add_saved_setup_section(embed, interaction.guild, "ticket_basics")
            return await interaction.response.edit_message(embed=embed, view=TicketBasicsPickerView())

        if section == "access_roles":
            embed.title = "🎭 Access Roles"
            embed.description = "Pick the exact roles your server uses. Leave optional roles blank if unused."
            if interaction.guild is not None:
                await _add_saved_setup_section(embed, interaction.guild, "access_roles")
            return await interaction.response.edit_message(embed=embed, view=AccessRolesPickerView())

        if section == "verification_channels":
            embed.title = "🎙️ Verification Channels"
            embed.description = "Pick the channels your verification flow uses. Leave anything unused blank."
            if interaction.guild is not None:
                await _add_saved_setup_section(embed, interaction.guild, "verification_channels")
            return await interaction.response.edit_message(embed=embed, view=VerificationChannelsPickerView())

        if section == "logs_status":
            embed.title = "🧾 Logs + Status"
            embed.description = "Pick where logs and status messages go. Names do not matter."
            if interaction.guild is not None:
                await _add_saved_setup_section(embed, interaction.guild, "logs_status")
            return await interaction.response.edit_message(embed=embed, view=LogsStatusPickerView())

        if section == "behavior":
            embed.title = "⚙️ Behavior Settings"
            embed.description = "Pick verification style, ticket prefix, kick timer, and other behavior settings."
            if interaction.guild is not None:
                await _add_saved_setup_section(embed, interaction.guild, "behavior")
            return await interaction.response.edit_message(embed=embed, view=BehaviorSettingsView())

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_main_setup_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Run Setup Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:postsave_health", row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed = await _build_health_embed(guild)
        await _edit_or_followup(interaction, embed=embed, view=SetupNavView())


def _section_from_columns(columns: tuple[str, ...]) -> str:
    joined = " ".join(str(x) for x in columns).lower()

    if any(key in joined for key in ("ticket_category", "ticket_archive", "ticket_panel", "support_channel", "staff_role", "transcript")):
        return "ticket_basics"

    if any(key in joined for key in ("unverified_role", "verified_role", "resident_role", "server_control_role", "control_role", "perm_role")):
        return "access_roles"

    if any(key in joined for key in ("verify_channel", "verification_channel", "vc_verify", "voice_verify", "welcome_channel")):
        return "verification_channels"

    if any(key in joined for key in ("modlog", "raidlog", "join_log", "status_channel", "bot_status", "bans_channel", "blacklist")):
        return "logs_status"

    if any(key in joined for key in ("verification_mode", "ticket_prefix", "verify_kick_hours")):
        return "behavior"

    return "current"


async def _show_saved_section_screen(
    interaction: discord.Interaction,
    *,
    title: str,
    saved_line: str,
    section: str,
    warnings: list[str] | None = None,
) -> None:
    guild = interaction.guild
    embed = discord.Embed(
        title=title,
        description=(
            f"{saved_line}\n\n"
            "Dank Shield saved the Discord ID. Names can change later without breaking the saved setup."
        ),
        color=discord.Color.green(),
        timestamp=now_utc(),
    )

    if guild is not None:
        await _add_saved_setup_section(embed, guild, section)

    if warnings:
        embed.add_field(name="Warnings", value="\n".join(f"• {x}" for x in warnings)[:1024], inline=False)

    embed.add_field(
        name="Next",
        value="Continue this section, run Setup Check, or go back to Setup Home.",
        inline=False,
    )

    await _send_ephemeral(interaction, embed=embed, view=PostSaveSetupView(section))


class SaveRoleSelect(discord.ui.RoleSelect):
    def __init__(
        self,
        *,
        placeholder: str,
        columns: tuple[str, ...],
        require_manage: bool,
        also_same: tuple[str, ...] = (),
        row: int = 0,
    ) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, row=row)
        self.columns = columns
        self.also_same = also_same
        self.require_manage = require_manage

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        role = self.values[0]
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        blockers: list[str] = []
        warnings: list[str] = []
        if role.is_default():
            blockers.append("@everyone cannot be used here.")
        if role.managed:
            blockers.append(f"{role.mention} is managed by an integration/bot and cannot be used here.")
        bot_member = _bot_member(guild)
        manageable, reason = _can_manage_role(guild, bot_member, role)
        if self.require_manage and not manageable:
            blockers.append(reason)
        elif not manageable:
            warnings.append(f"Bot may not be able to manage {role.mention}: {reason}")

        if blockers:
            embed = discord.Embed(
                title="🚫 Role Not Saved",
                description="\n".join(f"• {x}" for x in blockers)
                + "\n\nWhat to do next: pick a different role, or move Dank Shield's bot role above that role and try again.",
                color=discord.Color.red(),
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        payload = {column: _snowflake(role) for column in self.columns + self.also_same}
        await _save_config(interaction, payload)
        await _show_saved_section_screen(
            interaction,
            title="✅ Saved Setup Role",
            saved_line=f"Saved {_mention(role)}.",
            section=_section_from_columns(self.columns + self.also_same),
            warnings=warnings,
        )


class SaveChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self,
        *,
        placeholder: str,
        columns: tuple[str, ...],
        channel_types: list[discord.ChannelType],
        also_same: tuple[str, ...] = (),
        row: int = 0,
        require_category_manage: bool = False,
        require_text: bool = False,
        require_files: bool = False,
    ) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, channel_types=channel_types, row=row)
        self.columns = columns
        self.also_same = also_same
        self.require_category_manage = require_category_manage
        self.require_text = require_text
        self.require_files = require_files

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        channel = self.values[0]
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        blockers: list[str] = []
        bot_member = _bot_member(guild)
        if bot_member is None:
            blockers.append("Bot member could not be resolved for permission checks.")
        elif isinstance(channel, discord.CategoryChannel):
            missing = _category_missing_perms(channel, bot_member)
            if missing:
                blockers.append(f"{channel.mention} is missing bot permissions: {', '.join(missing)}")
        elif isinstance(channel, discord.TextChannel):
            missing = _text_channel_missing_perms(channel, bot_member, need_files=self.require_files)
            if missing:
                blockers.append(f"{channel.mention} is missing bot permissions: {', '.join(missing)}")
        elif isinstance(channel, discord.VoiceChannel):
            perms = channel.permissions_for(bot_member)
            missing = []
            if not perms.view_channel:
                missing.append("View Channel")
            if not perms.connect:
                missing.append("Connect")
            if not perms.manage_channels:
                missing.append("Manage Channels")
            if missing:
                blockers.append(f"{channel.mention} is missing bot permissions: {', '.join(missing)}")

        if blockers:
            embed = discord.Embed(
                title="🚫 Channel Not Saved",
                description="\n".join(f"• {x}" for x in blockers)
                + "\n\nWhat to do next: pick a different channel, or fix Dank Shield's permissions in that channel and try again.",
                color=discord.Color.red(),
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        payload = {column: _snowflake(channel) for column in self.columns + self.also_same}
        await _save_config(interaction, payload)
        await _show_saved_section_screen(
            interaction,
            title="✅ Saved Setup Channel",
            saved_line=f"Saved {_mention(channel)}.",
            section=_section_from_columns(self.columns + self.also_same),
        )


class TicketBasicsPickerView(SetupNavView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(
            SaveChannelSelect(
                placeholder="Where open tickets go",
                columns=("ticket_category_id",),
                channel_types=[discord.ChannelType.category],
                row=0,
                require_category_manage=True,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Where closed tickets go",
                columns=("ticket_archive_category_id",),
                also_same=("ticket_closed_category_id",),
                channel_types=[discord.ChannelType.category],
                row=1,
                require_category_manage=True,
            )
        )
        self.add_item(
            SaveRoleSelect(
                placeholder="Staff role that handles tickets",
                columns=("staff_role_id",),
                also_same=("vc_staff_role_id",),
                require_manage=False,
                row=2,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Where users press Create Ticket",
                columns=("ticket_panel_channel_id",),
                also_same=("support_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=3,
                require_text=True,
            )
        )


class AccessRolesPickerView(SetupNavView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(
            SaveRoleSelect(
                placeholder="New / waiting role (optional, any name)",
                columns=("unverified_role_id",),
                require_manage=True,
                row=0,
            )
        )
        self.add_item(
            SaveRoleSelect(
                placeholder="Approved role (optional, any name)",
                columns=("verified_role_id",),
                require_manage=True,
                row=1,
            )
        )
        self.add_item(
            SaveRoleSelect(
                placeholder="Full access / member role (optional, any name)",
                columns=("resident_role_id",),
                require_manage=True,
                row=2,
            )
        )
        self.add_item(
            SaveRoleSelect(
                placeholder="Server-control / setup admin role",
                columns=("server_control_role_id",),
                also_same=("control_role_id", "perm_role_id"),
                require_manage=False,
                row=3,
            )
        )


VerificationRolesPickerView = AccessRolesPickerView


class VerificationChannelsPickerView(SetupNavView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(
            SaveChannelSelect(
                placeholder="Text channel for verification, if used",
                columns=("verify_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=0,
                require_text=True,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Voice verification channel, if used",
                columns=("vc_verify_channel_id",),
                channel_types=[discord.ChannelType.voice],
                row=1,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Staff VC queue/status channel, if used",
                columns=("vc_verify_queue_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=2,
                require_text=True,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Welcome/start channel, if used",
                columns=("welcome_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=3,
                require_text=True,
            )
        )



class LogsSetupHubView(SetupNavView):
    @discord.ui.button(label="Core Logs + Status", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="stoney_solid:logs_core", row=0)
    async def core(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧾 Core Logs + Status",
            description=(
                "Pick the channels used every day: moderation/security logs, ticket transcripts, join/leave logs, and bot status."
            ),
            color=discord.Color.blurple(),
        )
        if interaction.guild is not None:
            await _add_saved_setup_section(embed, interaction.guild, "logs_status")
        await interaction.response.edit_message(embed=embed, view=LogsStatusPickerView())

    @discord.ui.button(label="Advanced Logs", emoji="📚", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:logs_advanced", row=0)
    async def advanced(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return

        embed = discord.Embed(
            title="📚 Advanced Logs",
            description="Optional logs most servers do not need right away.",
            color=discord.Color.blurple(),
        )

        if interaction.guild is not None:
            try:
                cfg = await get_guild_config(interaction.guild.id, refresh=True)
                embed.add_field(
                    name="Currently Saved",
                    value=(
                        f"Bans / blacklist: {_channel_or_not_set(interaction.guild, _cfg_value(cfg, 'bans_channel_id') or _cfg_value(cfg, 'blacklist_channel_id'))}"
                    )[:1024],
                    inline=False,
                )
            except Exception as e:
                embed.add_field(name="Currently Saved", value=f"Could not load saved setup: `{type(e).__name__}`", inline=False)

        embed.add_field(
            name="Safe Rule",
            value="This only saves the selected Discord channel ID. It does not delete or move anything.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=AdvancedLogsPickerView())


class AdvancedLogsPickerView(SetupNavView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(
            SaveChannelSelect(
                placeholder="Bans / blacklist channel, if used",
                columns=("bans_channel_id",),
                also_same=("blacklist_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=0,
                require_text=True,
            )
        )


class LogsStatusPickerView(SetupNavView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(
            SaveChannelSelect(
                placeholder="Where moderation/security logs go",
                columns=("modlog_channel_id",),
                also_same=("raidlog_channel_id", "raid_log_channel_id", "force_verify_log_channel_id"),
                channel_types=[discord.ChannelType.text],
                row=0,
                require_text=True,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Where ticket transcripts go",
                columns=("transcripts_channel_id",),
                also_same=("transcript_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=1,
                require_text=True,
                require_files=True,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Where join/leave logs go",
                columns=("join_log_channel_id",),
                also_same=("join_exit_log_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=2,
                require_text=True,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Where bot status messages go",
                columns=("status_channel_id",),
                also_same=("bot_status_channel_id", "health_channel_id"),
                channel_types=[discord.ChannelType.text],
                row=3,
                require_text=True,
            )
        )


# ---------------------------------------------------------------------------
# behavior settings
# ---------------------------------------------------------------------------



async def _verification_timer_payload(guild: discord.Guild) -> tuple[discord.Embed, discord.ui.View]:
    cfg = None
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    wait_enabled = _setup_bool(_cfg_value(cfg, "verification_wait_timer_enabled"), False)
    wait_hours = max(1, min(720, _safe_int(_cfg_value(cfg, "verify_kick_hours"), 24)))
    idle_enabled = _setup_bool(_cfg_value(cfg, "verification_idle_kick_enabled"), False)
    idle_minutes = max(5, min(10080, _safe_int(_cfg_value(cfg, "verification_idle_kick_minutes"), 60)))

    wait_summary = {"active_join_grace": 0, "active_member_no_ticket": 0, "active_ticket_no_response": 0, "persisted_wait_rows": 0}
    try:
        from stoney_verify.commands_ext import kick_timers
        wait_summary = await kick_timers.verification_wait_timer_summary(guild.id)  # type: ignore[attr-defined]
    except Exception:
        pass

    idle_summary = {"active": 0, "persisted": 0}
    try:
        from stoney_verify.startup_guards import verification_idle_kick_feature as idle
        idle_summary = await idle.timer_summary(guild.id)  # type: ignore[attr-defined]
    except Exception:
        pass

    embed = discord.Embed(
        title="⏱️ Verification Timers",
        description=(
            "Control timers that can remove pending/unverified members. "
            "Nothing here changes roles/channels. Disable + Clear stops future starts and removes active timers."
        ),
        color=discord.Color.green() if wait_enabled else discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Main Wait Timer",
        value=(
            f"Status: `{'ON' if wait_enabled else 'OFF'}`\n"
            f"Delay: `{wait_hours}` hour(s)\n"
            "Applies to: pending/unverified members who make no verification progress.\n"
            "Stops automatically when they gain a safe role or open a verification ticket.\n"
            f"Active now: join grace `{wait_summary.get('active_join_grace', 0)}`, no-ticket `{wait_summary.get('active_member_no_ticket', 0)}`, ticket `{wait_summary.get('active_ticket_no_response', 0)}`, saved rows `{wait_summary.get('persisted_wait_rows', 0)}`"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Advanced No-Start Auto-Remove",
        value=(
            f"Status: `{'ON' if idle_enabled else 'OFF'}`\n"
            f"Delay: `{idle_minutes}` minute(s)\n"
            "Applies to: pending/unverified members who never start verification.\n"
            f"Active now: `{idle_summary.get('active', 0)}` active, `{idle_summary.get('persisted', 0)}` saved rows"
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Safe Removal",
        value="Use **Disable + Clear** to turn a timer off and remove active saved timers for this server.",
        inline=False,
    )
    return embed, VerificationTimerSettingsView()


async def _open_verification_timer_settings(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await _safe_defer_update(interaction)
    embed, view = await _verification_timer_payload(guild)
    await _edit_or_followup(interaction, embed=embed, view=view)


class VerificationTimerSettingsView(SetupNavView):
    @discord.ui.button(label="Enable Wait Timer", emoji="✅", style=discord.ButtonStyle.success, custom_id="stoney_solid:verify_timer_enable", row=0)
    async def enable_wait(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await _save_config(interaction, {"verification_wait_timer_enabled": True, "verify_kick_hours": str(24)})
        await _open_verification_timer_settings(interaction)

    @discord.ui.button(label="Disable + Clear Wait Timer", emoji="🛑", style=discord.ButtonStyle.danger, custom_id="stoney_solid:verify_timer_disable_clear", row=0)
    async def disable_wait(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _save_config(interaction, {"verification_wait_timer_enabled": False})
        try:
            from stoney_verify.commands_ext import kick_timers
            await kick_timers.clear_verification_wait_timers_for_guild(guild.id)  # type: ignore[attr-defined]
        except Exception:
            pass
        await _open_verification_timer_settings(interaction)

    @discord.ui.button(label="Change Wait Hours", emoji="⏱️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:verify_timer_hours", row=1)
    async def change_wait_hours(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(VerificationWaitTimerModal())

    @discord.ui.button(label="Clear Active Wait Timers", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:verify_timer_clear_active", row=1)
    async def clear_wait(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        try:
            from stoney_verify.commands_ext import kick_timers
            await kick_timers.clear_verification_wait_timers_for_guild(guild.id)  # type: ignore[attr-defined]
        except Exception:
            pass
        await _open_verification_timer_settings(interaction)

    @discord.ui.button(label="Advanced No-Start Timer", emoji="⚠️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:verify_idle_timer", row=2)
    async def idle_timer(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, _ = await _verification_timer_payload(guild)
        embed.title = "⚠️ Advanced No-Start Auto-Remove"
        embed.description = (
            "This optional timer removes pending/unverified members who never start verification at all. "
            "Most public servers should leave this OFF unless they really want automatic cleanup."
        )
        await _edit_or_followup(interaction, embed=embed, view=VerificationIdleTimerSettingsView())


class VerificationIdleTimerSettingsView(SetupNavView):
    @discord.ui.button(label="Enable No-Start Timer", emoji="✅", style=discord.ButtonStyle.success, custom_id="stoney_solid:idle_timer_enable", row=0)
    async def enable_idle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await _save_config(interaction, {"verification_idle_kick_enabled": True, "verification_idle_kick_minutes": 60})
        await _open_verification_timer_settings(interaction)

    @discord.ui.button(label="Disable + Clear No-Start", emoji="🛑", style=discord.ButtonStyle.danger, custom_id="stoney_solid:idle_timer_disable_clear", row=0)
    async def disable_idle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _save_config(interaction, {"verification_idle_kick_enabled": False})
        try:
            from stoney_verify.startup_guards import verification_idle_kick_feature as idle
            await idle.clear_guild_timers(guild.id)  # type: ignore[attr-defined]
        except Exception:
            pass
        await _open_verification_timer_settings(interaction)

    @discord.ui.button(label="Change No-Start Minutes", emoji="⏱️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:idle_timer_minutes", row=1)
    async def change_idle_minutes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(VerificationIdleTimerModal())


class VerificationWaitTimerModal(discord.ui.Modal, title="Verification Wait Timer"):
    hours = discord.ui.TextInput(
        label="Hours before removing if no progress",
        placeholder="24",
        required=True,
        max_length=4,
    )
    enable_now = discord.ui.TextInput(
        label="Enable now? yes/no",
        placeholder="yes",
        required=False,
        max_length=5,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        raw = _none_if_blank(self.hours.value)
        hours = _safe_int(raw, 24)
        if hours < 1 or hours > 720:
            embed = discord.Embed(title="🚫 Timer Not Saved", description="Use 1 to 720 hours. Example: `24`.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        enable_text = str(self.enable_now.value or "yes").strip().lower()
        enable = enable_text not in {"no", "n", "false", "off", "0"}
        await _save_config(interaction, {"verification_wait_timer_enabled": bool(enable), "verify_kick_hours": str(hours)})
        embed, view = await _verification_timer_payload(interaction.guild)  # type: ignore[arg-type]
        await _send_ephemeral(interaction, embed=embed, view=view)


class VerificationIdleTimerModal(discord.ui.Modal, title="No-Start Auto-Remove"):
    minutes = discord.ui.TextInput(
        label="Minutes before removing if no start",
        placeholder="60",
        required=True,
        max_length=5,
    )
    enable_now = discord.ui.TextInput(
        label="Enable now? yes/no",
        placeholder="yes",
        required=False,
        max_length=5,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        raw = _none_if_blank(self.minutes.value)
        minutes = _safe_int(raw, 60)
        if minutes < 5 or minutes > 10080:
            embed = discord.Embed(title="🚫 Timer Not Saved", description="Use 5 to 10080 minutes. Example: `60`.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        enable_text = str(self.enable_now.value or "yes").strip().lower()
        enable = enable_text not in {"no", "n", "false", "off", "0"}
        await _save_config(interaction, {"verification_idle_kick_enabled": bool(enable), "verification_idle_kick_minutes": int(minutes)})
        embed, view = await _verification_timer_payload(interaction.guild)  # type: ignore[arg-type]
        await _send_ephemeral(interaction, embed=embed, view=view)


class VerificationModeSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label="No access roles",
                value="none",
                description="Dank Shield will not require a waiting/approved/member role style.",
                emoji="🚫",
            ),
            discord.SelectOption(
                label="One approved role only",
                value="single_approved_role",
                description="Users only get one approved role after verification.",
                emoji="✅",
            ),
            discord.SelectOption(
                label="Waiting role → approved role",
                value="waiting_to_approved",
                description="Users start with one role, then switch to approved.",
                emoji="🔁",
            ),
            discord.SelectOption(
                label="Waiting → approved → full access",
                value="waiting_to_approved_to_member",
                description="Use all three role steps if your server wants that.",
                emoji="🎭",
            ),
            discord.SelectOption(
                label="Custom / staff controlled",
                value="custom",
                description="Use your saved role picks, but do not assume a fixed flow.",
                emoji="🧩",
            ),
        ]
        super().__init__(placeholder="Choose how your server handles access roles", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        mode = str(self.values[0])
        await _save_config(interaction, {"verification_mode": mode})
        await _show_saved_section_screen(
            interaction,
            title="✅ Saved Access Role Style",
            saved_line=f"Saved verification mode: `{mode}`.",
            section="behavior",
        )


class BehaviorSettingsView(SetupNavView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(VerificationModeSelect())

    @discord.ui.button(label="Verification Timers", emoji="⏱️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:behavior_verify_timers", row=1)
    async def verify_timers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_verification_timer_settings(interaction)

    @discord.ui.button(label="Set Prefix / Ticket Timer Hours", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:behavior_modal", row=1)
    async def modal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(BehaviorSettingsModal())

    @discord.ui.button(label="Clear Optional Access Roles", emoji="🧽", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:clear_access_roles", row=2)
    async def clear_access_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await _clear_config_keys(interaction, ("unverified_role_id", "verified_role_id", "resident_role_id"))
        await _show_saved_section_screen(
            interaction,
            title="✅ Optional Access Roles Cleared",
            saved_line="Cleared saved new/waiting, approved, and full-access role slots. No Discord roles were deleted.",
            section="access_roles",
        )


class BehaviorSettingsModal(discord.ui.Modal, title="Behavior Settings"):
    ticket_prefix = discord.ui.TextInput(
        label="Ticket channel prefix",
        placeholder="ticket",
        required=False,
        max_length=24,
    )
    verify_kick_hours = discord.ui.TextInput(
        label="Kick unverified users after hours",
        placeholder="24",
        required=False,
        max_length=4,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return

        payload: dict[str, Any] = {}
        prefix = _none_if_blank(self.ticket_prefix.value)
        if prefix:
            cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", prefix).strip("-")[:24]
            payload["ticket_prefix"] = cleaned or "ticket"

        kick_raw = _none_if_blank(self.verify_kick_hours.value)
        if kick_raw:
            hours = _safe_int(kick_raw, 24)
            if hours < 1 or hours > 720:
                embed = discord.Embed(
                    title="🚫 Timer Not Saved",
                    description="Use a number from 1 to 720 hours. Example: `24`.",
                    color=discord.Color.red(),
                )
                return await interaction.response.send_message(embed=embed, ephemeral=True)
            payload["verify_kick_hours"] = str(hours)

        if not payload:
            embed = discord.Embed(
                title="Nothing Changed",
                description="No values were entered. Use **Continue This Section** to return to Behavior Settings.",
                color=discord.Color.dark_grey(),
            )
            if interaction.guild is not None:
                await _add_saved_setup_section(embed, interaction.guild, "behavior")
            return await _send_ephemeral(interaction, embed=embed, view=PostSaveSetupView("behavior"))

        await _save_config(interaction, payload)
        saved_parts = []
        if "ticket_prefix" in payload:
            saved_parts.append(f"ticket prefix `{payload['ticket_prefix']}`")
        if "verify_kick_hours" in payload:
            saved_parts.append(f"verify kick timer `{payload['verify_kick_hours']}h`")
        saved_line = "Saved " + " and ".join(saved_parts) + "." if saved_parts else "Saved behavior settings."

        await _show_saved_section_screen(
            interaction,
            title="✅ Behavior Settings Saved",
            saved_line=saved_line,
            section="behavior",
        )


# ---------------------------------------------------------------------------
# ticket category / menu manager
# ---------------------------------------------------------------------------


async def _build_category_manager_payload(
    guild: discord.Guild,
    *,
    title: str = "🗂️ Ticket Menu Options",
) -> tuple[discord.Embed, "CategoryManagerView"]:
    load = await _category_load(guild)
    embed = discord.Embed(
        title=title,
        description=(
            "**This controls the choices users see when opening a ticket.**\n"
            "It does **not** move ticket channels, change ticket folders, or delete tickets.\n"
            "Use **Tickets → Ticket Basics** for open/archive categories and staff role."
        ),
        color=discord.Color.blurple() if not load.error else discord.Color.red(),
        timestamp=now_utc(),
    )

    if load.error:
        embed.add_field(name="Database Problem", value=load.error[:1024], inline=False)
        embed.add_field(
            name="What To Do Next",
            value="Confirm the `ticket_categories` table exists and Supabase is reachable, then restart or refresh.",
            inline=False,
        )
    else:
        embed.add_field(name="Member Preview", value=_category_member_preview_text(load.rows), inline=False)
        embed.add_field(name="Saved Ticket Choices", value=_category_list_text(load.rows), inline=False)
        embed.add_field(name="Safety Check", value=_category_governance_text(load.rows), inline=False)
        embed.add_field(
            name="Button Meaning",
            value=(
                "✨ **Create Missing Recommended Options** adds safe default choices only when missing.\n"
                "➕ **Add Custom Ticket Choice** adds one new user-facing choice.\n"
                "✏️ **Edit an Existing Choice** changes the label/keywords/type for one choice.\n"
                "⭐ **Choose Default Fallback** picks where unclear tickets go."
            ),
            inline=False,
        )

    return embed, CategoryManagerView(rows=load.rows, db_error=load.error)


def _category_options(rows: list[dict[str, Any]], *, placeholder: str) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for row in rows[:25]:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        name = str(row.get("name") or slug).strip()
        intake_type = str(row.get("intake_type") or "custom").strip()
        default = "Default • " if bool(row.get("is_default")) else ""
        options.append(
            discord.SelectOption(
                label=_short(name, 95) or slug,
                description=_short(f"{default}{slug} • {intake_type}", 100),
                value=slug[:100],
            )
        )
    if not options:
        options.append(discord.SelectOption(label="No options available", value="__none__", description=placeholder[:100]))
    return options


def _find_category(rows: list[dict[str, Any]], slug: str) -> Optional[dict[str, Any]]:
    slug_l = str(slug or "").strip().lower()
    for row in rows:
        if str(row.get("slug") or "").strip().lower() == slug_l:
            return row
    return None


def _category_table_write_sync(guild_id: int, row: dict[str, Any], *, set_default: bool = False) -> tuple[bool, str]:
    sb = get_supabase()
    if sb is None:
        return False, "Supabase is not available."

    slug = _slugify(row.get("slug") or row.get("name"))
    payload = {
        "guild_id": str(int(guild_id)),
        "name": str(row.get("name") or slug).strip()[:80],
        "display_name": str(row.get("name") or slug).strip()[:80],
        "slug": slug,
        "description": str(row.get("description") or "").strip()[:300],
        "intake_type": str(row.get("intake_type") or "custom").strip().lower(),
        "match_keywords": row.get("match_keywords") or [],
        "sort_order": _safe_int(row.get("sort_order"), 50),
        "is_default": bool(row.get("is_default")),
        "color": str(row.get("color") or "#45d483")[:16],
    }
    if payload["intake_type"] not in INTAKE_TYPE_OPTIONS:
        payload["intake_type"] = "custom"

    existing_res = (
        sb.table("ticket_categories")
        .select("*")
        .eq("guild_id", str(int(guild_id)))
        .eq("slug", slug)
        .limit(1)
        .execute()
    )
    existing = getattr(existing_res, "data", None) or []

    if set_default or payload["is_default"]:
        try:
            sb.table("ticket_categories").update({"is_default": False}).eq("guild_id", str(int(guild_id))).execute()
        except Exception:
            pass
        payload["is_default"] = True

    if existing:
        sb.table("ticket_categories").update(payload).eq("guild_id", str(int(guild_id))).eq("slug", slug).execute()
    else:
        sb.table("ticket_categories").insert(payload).execute()
    return True, slug


async def _write_category(guild: discord.Guild, row: dict[str, Any], *, set_default: bool = False) -> tuple[bool, str]:
    return await asyncio.to_thread(_category_table_write_sync, int(guild.id), dict(row), set_default=set_default)


async def _seed_recommended_categories(
    guild: discord.Guild,
    *,
    managed_only: bool = False,
) -> tuple[list[str], list[str], str]:
    load = await _category_load(guild)
    if load.error:
        return [], [], load.error

    from . import public_tickettool_parity_polish as ticket_menu

    if managed_only and load.rows and not ticket_menu._looks_like_legacy_managed_default_rows(load.rows):
        existing_slugs = [
            str(row.get("slug") or row.get("name") or "custom").strip().lower()
            for row in load.rows
        ]
        return [], existing_slugs, ""

    existing = {
        ticket_menu._canonical_category_key(row)
        for row in load.rows
    }
    created: list[str] = []
    skipped: list[str] = []
    has_default = any(bool(row.get("is_default")) for row in load.rows)

    for item in RECOMMENDED_CATEGORIES:
        slug = _slugify(item["slug"])
        key = ticket_menu._canonical_category_key(item)

        if key in existing:
            skipped.append(slug)
            continue

        payload = dict(item)
        payload["slug"] = slug
        payload["is_default"] = bool(item.get("is_default")) and not has_default

        ok, msg = await _write_category(
            guild,
            payload,
            set_default=bool(payload["is_default"]),
        )

        if not ok:
            return created, skipped, msg

        created.append(slug)
        existing.add(key)

        if payload["is_default"]:
            has_default = True

    return created, skipped, ""


class CategorySelect(discord.ui.Select):
    def __init__(self, rows: list[dict[str, Any]], *, action: str, placeholder: str, row: int) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=_category_options(rows, placeholder=placeholder), row=row)
        self.rows = rows
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        value = str(self.values[0])
        if value == "__none__":
            return await interaction.response.send_message("No ticket menu option was selected.", ephemeral=True)
        item = _find_category(self.rows, value)
        if item is None:
            return await interaction.response.send_message("That ticket menu option was not found. Press Refresh and try again.", ephemeral=True)

        if self.action == "edit":
            try:
                await interaction.response.send_modal(CategoryModal(existing=item))
            except Exception as e:
                embed = discord.Embed(
                    title="🚫 Edit Menu Did Not Open",
                    description=(
                        f"`{type(e).__name__}: {str(e)[:250]}`\n\n"
                        "Nothing was changed. Press **Refresh**, then try again."
                    ),
                    color=discord.Color.red(),
                )
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return

        if self.action == "default":
            await _safe_defer_update(interaction)
            ok, msg = await _write_category(guild, {**item, "is_default": True}, set_default=True)
            if not ok:
                embed = discord.Embed(title="🚫 Default Not Saved", description=msg, color=discord.Color.red())
                return await _edit_or_followup(interaction, embed=embed, view=CategoryManagerView(rows=self.rows))
            embed, view = await _build_category_manager_payload(guild, title="✅ Default Ticket Option Saved")
            await _edit_or_followup(interaction, embed=embed, view=view)
            return


class CategoryManagerView(SetupNavView):
    def __init__(self, *, rows: list[dict[str, Any]], db_error: str = "") -> None:
        super().__init__()
        self.rows = rows
        self.db_error = db_error

        # Only show edit/default dropdowns when real choices exist.
        # Showing fake empty dropdowns caused users to think setup was broken.
        if not db_error and rows:
            self.add_item(CategorySelect(rows, action="edit", placeholder="✏️ Edit an existing ticket choice", row=0))
            self.add_item(CategorySelect(rows, action="default", placeholder="⭐ Choose default fallback ticket choice", row=1))

        # Make the create button truthful after recommended choices already exist.
        try:
            from . import public_tickettool_parity_polish as ticket_menu

            existing = {
                ticket_menu._canonical_category_key(row)
                for row in rows
            }
            recommended = {
                ticket_menu._canonical_category_key(item)
                for item in RECOMMENDED_CATEGORIES
            }
            all_recommended_exist = bool(recommended) and recommended.issubset(existing)
            for child in list(getattr(self, "children", []) or []):
                custom_id = str(getattr(child, "custom_id", "") or "")
                if custom_id == "stoney_solid:cat_seed":
                    child.label = "Check Recommended Options" if all_recommended_exist else "Create Missing Recommended Options"
                    child.emoji = "✅" if all_recommended_exist else "✨"
                    child.style = discord.ButtonStyle.secondary if all_recommended_exist else discord.ButtonStyle.success
                elif custom_id == "stoney_solid:cat_add":
                    child.label = "Add Custom Ticket Choice"
                elif custom_id == "stoney_solid:cat_refresh":
                    child.label = "Refresh Menu Options"
        except Exception:
            pass

    @discord.ui.button(label="Create Recommended", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_solid:cat_seed", row=2)
    async def seed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        created, skipped, error = await _seed_recommended_categories(guild)
        if error:
            embed = discord.Embed(
                title="🚫 Recommended Options Not Created",
                description=f"{error}\n\nWhat to do next: check Supabase and try again.",
                color=discord.Color.red(),
            )
            return await _edit_or_followup(interaction, embed=embed, view=SetupNavView())
        embed, view = await _build_category_manager_payload(guild, title="✅ Ticket Menu Options Checked")
        embed.add_field(
            name="What Changed",
            value=(
                f"Created: {_line_list([f'`{x}`' for x in created], empty='Nothing new created.')}\n"
                f"Already existed: {_line_list([f'`{x}`' for x in skipped], empty='None')}\n\n"
                "No ticket channels, categories, roles, or existing tickets were changed."
            )[:1024],
            inline=False,
        )
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Add Custom Option", emoji="➕", style=discord.ButtonStyle.primary, custom_id="stoney_solid:cat_add", row=2)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        try:
            await interaction.response.send_modal(CategoryModal(existing=None))
        except Exception as e:
            await safe_interaction_error(
                interaction,
                title="Ticket Menu Editor Did Not Open",
                error=e,
                hint="Nothing was changed. Press **Refresh**, then try **Add Custom Option** again.",
            )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:cat_refresh", row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)


class CategoryModal(discord.ui.Modal, title="Ticket Menu Option"):
    def __init__(self, *, existing: Optional[dict[str, Any]]) -> None:
        super().__init__()
        self.existing = existing or {}

        self.name_input = discord.ui.TextInput(
            label="Name users/staff see",
            placeholder="Support",
            default=str(self.existing.get("name") or "")[:80],
            required=True,
            max_length=80,
        )
        self.slug_input = discord.ui.TextInput(
            label="Short ID / slug",
            placeholder="support",
            default=str(self.existing.get("slug") or "")[:50],
            required=False,
            max_length=50,
        )
        self.description_input = discord.ui.TextInput(
            label="Description",
            placeholder="General help and support tickets.",
            default=str(self.existing.get("description") or "")[:300],
            required=False,
            max_length=300,
            style=discord.TextStyle.paragraph,
        )
        self.keywords_input = discord.ui.TextInput(
            label="Keywords, comma separated",
            placeholder="help, support, issue",
            default=", ".join(str(x) for x in (self.existing.get("match_keywords") or [])[:8])[:200],
            required=False,
            max_length=200,
        )
        self.type_input = discord.ui.TextInput(
            label="Ticket type",
            placeholder="support, verification, appeal, report, question, bug, custom",
            default=str(self.existing.get("intake_type") or "custom")[:30],
            required=False,
            max_length=30,
        )

        self.add_item(self.name_input)
        self.add_item(self.slug_input)
        self.add_item(self.description_input)
        self.add_item(self.keywords_input)
        self.add_item(self.type_input)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await safe_interaction_error(
            interaction,
            title="Ticket Menu Editor Failed",
            error=error,
            hint="Nothing was changed. Press **Refresh**, then try the edit again.",
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

        await safe_defer(interaction, ephemeral=True)

        name = str(self.name_input.value or "").strip()
        slug = _slugify(self.slug_input.value or name)
        intake_type = str(self.type_input.value or "custom").strip().lower()
        if intake_type not in INTAKE_TYPE_OPTIONS:
            intake_type = "custom"
        sort_order = _safe_int(self.existing.get("sort_order"), 50)

        row = {
            **self.existing,
            "name": name,
            "display_name": name,
            "slug": slug,
            "description": str(self.description_input.value or "").strip(),
            "intake_type": intake_type,
            "match_keywords": _split_keywords(self.keywords_input.value),
            "sort_order": sort_order,
            "is_default": bool(self.existing.get("is_default", False)),
        }
        ok, msg = await _write_category(guild, row, set_default=bool(row.get("is_default")))
        if not ok:
            embed = discord.Embed(
                title="🚫 Ticket Option Not Saved",
                description=f"{msg}\n\nWhat to do next: check the values and try again.",
                color=discord.Color.red(),
            )
            return await safe_followup(interaction, embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        embed, view = await _build_category_manager_payload(guild, title="✅ Ticket Menu Option Saved")
        await _edit_or_followup(interaction, embed=embed, view=view)


# ---------------------------------------------------------------------------
# slash registration
# ---------------------------------------------------------------------------


async def _setup_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
    await safe_defer(interaction, ephemeral=True)
    embed, view = await _build_main_setup_payload(guild)
    await interaction.followup.send(
        embed=embed,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return

    try:
        existing = dank_group.get_command("setup")
    except Exception:
        existing = None

    if existing is not None:
        try:
            dank_group.remove_command("setup")
        except Exception:
            # If the command cannot be removed, keep startup safe and do not add a duplicate.
            _ATTACHED = True
            return

    command = discord.app_commands.Command(
        name="setup",
        description="Simple guided setup for this server.",
        callback=_setup_callback,
    )
    dank_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_setup_solid_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_solid: attached simple guided /dank setup flow")
    except Exception:
        pass


__all__ = [
    "register_public_setup_solid_commands",
    "SolidSetupView",
    "ChooseExistingView",
    "TicketBasicsPickerView",
    "AccessRolesPickerView",
    "VerificationRolesPickerView",
    "VerificationChannelsPickerView",
    "LogsStatusPickerView",
    "BehaviorSettingsView",
    "CategoryManagerView",
]
