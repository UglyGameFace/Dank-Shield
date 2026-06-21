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


_ATTACHED = False

RECOMMENDED_CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "slug": "support",
        "name": "Support",
        "description": "General help and support tickets.",
        "intake_type": "support",
        "match_keywords": ["support", "help", "issue", "problem"],
        "is_default": True,
        "sort_order": 10,
    },
    {
        "slug": "verification",
        "name": "Verification Help",
        "description": "Help for users stuck during verification.",
        "intake_type": "verification",
        "match_keywords": ["verify", "verification", "pending", "unverified", "vc verify"],
        "is_default": False,
        "sort_order": 20,
    },
    {
        "slug": "appeal",
        "name": "Appeal",
        "description": "Appeals for moderation actions or access decisions.",
        "intake_type": "appeal",
        "match_keywords": ["appeal", "ban", "mute", "timeout", "blacklist"],
        "is_default": False,
        "sort_order": 30,
    },
    {
        "slug": "report",
        "name": "Report User",
        "description": "Report a member, message, scam, or rule violation.",
        "intake_type": "report",
        "match_keywords": ["report", "scam", "harass", "spam", "abuse"],
        "is_default": False,
        "sort_order": 40,
    },
    {
        "slug": "question",
        "name": "Question",
        "description": "General questions that do not need urgent staff escalation.",
        "intake_type": "question",
        "match_keywords": ["question", "ask", "how", "info"],
        "is_default": False,
        "sort_order": 50,
    },
    {
        "slug": "bug",
        "name": "Bug Report",
        "description": "Report a bot/server workflow bug.",
        "intake_type": "bug",
        "match_keywords": ["bug", "broken", "error", "not working"],
        "is_default": False,
        "sort_order": 60,
    },
    {
        "slug": "custom",
        "name": "Other",
        "description": "Anything that does not match another category.",
        "intake_type": "custom",
        "match_keywords": ["other", "custom", "misc"],
        "is_default": False,
        "sort_order": 70,
    },
)

INTAKE_TYPE_OPTIONS: tuple[str, ...] = (
    "support",
    "verification",
    "appeal",
    "report",
    "question",
    "bug",
    "custom",
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


async def _build_health_embed(guild: discord.Guild) -> discord.Embed:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

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
        warnings.append("No ticket menu options exist yet. Press Ticket Menu Options → Create Recommended.")
    else:
        ok.append(f"Ticket menu options loaded: `{len(category_load.rows)}`.")
        governance = _category_governance_text(category_load.rows)
        if governance.startswith("•") or governance.startswith("⚠️"):
            warnings.append(governance)

    ready = not blockers
    embed = discord.Embed(
        title="🩺 Setup Health Check",
        description=(
            "✅ **Ready enough to test.**" if ready else "🚫 **Fix the blockers first.**"
        ),
        color=discord.Color.green() if ready else discord.Color.red(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported."), inline=False)
    embed.add_field(
        name="What To Press Next",
        value=(
            "If there are blockers, press **Back to Setup** and use **Use My Existing Server** to pick the exact missing item.\n"
            "If there are no blockers, test creating a ticket and test your verification flow."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /dank setup")
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


async def _build_main_setup_payload(guild: discord.Guild) -> tuple[discord.Embed, "SolidSetupView"]:
    cfg = None
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    embed = discord.Embed(
        title="🚀 Dank Shield Setup",
        description=(
            "Setup is one step at a time. Use the buttons below. You can go back any time.\n\n"
            "✨ **Auto-Build Missing Items** creates only missing defaults. It does not replace saved choices.\n"
            "✏️ **Name Items Before Build** lets you choose names before Dank Shield creates anything.\n"
            "🧩 **Use My Existing Server** lets you pick the exact roles/channels you already use. Names do not matter.\n"
            "🗂️ **Ticket Menu Options** controls the support choices users see when opening tickets.\n"
            "🩺 **Run Health Check** tells you what is ready and what needs fixing.\n\n"
            "**Safe rule:** setup saves choices. It does not delete your channels, roles, tickets, or messages."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    if cfg is not None:
        embed.add_field(
            name="Current Setup Snapshot",
            value=(
                f"Open tickets: {_channel_or_not_set(guild, _cfg_value(cfg, 'ticket_category_id'))}\n"
                f"Closed tickets: {_channel_or_not_set(guild, _cfg_value(cfg, 'ticket_archive_category_id'))}\n"
                f"Staff role: {_role_or_not_set(guild, _cfg_value(cfg, 'staff_role_id'))}\n"
                f"Access mode: `{_safe_str(_cfg_value(cfg, 'verification_mode'), 'not chosen')}`"
            )[:1024],
            inline=False,
        )
    embed.add_field(
        name="Best Starting Choice",
        value=(
            "• New server? Press **Auto-Build Missing Items**.\n"
            "• Existing server? Press **Use My Existing Server**.\n"
            "• Unsure? Press **Run Health Check**."
        ),
        inline=False,
    )
    return embed, SolidSetupView()


# ---------------------------------------------------------------------------
# reusable navigation
# ---------------------------------------------------------------------------


class SetupNavView(discord.ui.View):
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
            hint=f"The **{item_label}** action failed safely. Nothing was changed. Press **Refresh** or reopen `/dank setup`.",
            view=self,
        )

    @discord.ui.button(label="Back to Setup", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_main_setup_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="View Current Setup", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:summary", row=4)
    async def summary(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed = await _build_current_setup_embed(guild)
        await _edit_or_followup(interaction, embed=embed, view=SetupNavView())

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, custom_id="stoney_solid:close", row=4)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="Setup Closed",
            description="Nothing else was changed. Run `/dank setup` whenever you want to continue.",
            color=discord.Color.dark_grey(),
        )
        await _edit_or_followup(interaction, embed=embed, view=None)


BackToSetupView = SetupNavView


# ---------------------------------------------------------------------------
# main setup view
# ---------------------------------------------------------------------------


class SolidSetupView(SetupNavView):
    def __init__(self) -> None:
        super().__init__()

    @discord.ui.button(label="Auto-Build Missing Items", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_solid:auto", row=0)
    async def auto_fix(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        try:
            from . import public_setup_defaults

            await public_setup_defaults._setup_defaults_callback(interaction)
            if interaction.guild is not None:
                created, skipped, error = await _seed_recommended_categories(interaction.guild)
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
            embed = discord.Embed(
                title="❌ Auto-Build Failed",
                description=(
                    f"`{type(e).__name__}: {str(e)[:300]}`\n\n"
                    "What to do next: run **Health Check**, fix the listed permission problem, then try again."
                ),
                color=discord.Color.red(),
            )
            await _send_ephemeral(interaction, embed=embed, view=SetupNavView())

    @discord.ui.button(label="Name Items Before Build", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:customize", row=0)
    async def customize(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        try:
            from .public_setup_start import CustomizeSetupMenuView

            embed = discord.Embed(
                title="✏️ Name Items Before Build",
                description=(
                    "Use this before Auto-Build if you want Dank Shield to create missing items with your own names.\n\n"
                    "Names are split into small pages so the form is easier to read. Nothing is created until you submit a page."
                ),
                color=discord.Color.blurple(),
            )
            await interaction.response.edit_message(embed=embed, view=CustomizeSetupMenuView())
        except Exception as e:
            embed = discord.Embed(
                title="❌ Name Editor Did Not Open",
                description=(
                    f"`{type(e).__name__}: {str(e)[:300]}`\n\n"
                    "What to do next: use **Use My Existing Server** to pick your current roles/channels instead."
                ),
                color=discord.Color.red(),
            )
            await _send_ephemeral(interaction, embed=embed, view=SetupNavView())

    @discord.ui.button(label="Use My Existing Server", emoji="🧩", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:existing", row=1)
    async def choose_existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧩 Use My Existing Server",
            description=(
                "Pick what your server already uses. Names do not matter. Dank Shield saves IDs, not names.\n"
                "Only pick the items your server uses. Leave anything else alone."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Sections",
            value=(
                "🎫 **Ticket Basics** — where tickets open/close, staff role, transcripts\n"
                "🎭 **Access Roles** — optional roles for new/waiting, approved, full access\n"
                "🎙️ **Verification Channels** — optional text/voice verification channels\n"
                "🧾 **Logs + Status** — modlog, join/leave log, bot status\n"
                "⚙️ **Behavior Settings** — verification style, ticket prefix, kick timer"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ChooseExistingView())

    @discord.ui.button(label="Ticket Menu Options", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:categories", row=1)
    async def categories(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Set This as Status Channel", emoji="📌", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:status", row=2)
    async def status_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ Use this inside the text channel you want as the bot status channel.", ephemeral=True)
        await _safe_defer_update(interaction)
        await _save_config(
            interaction,
            {
                "status_channel_id": _snowflake(interaction.channel),
                "bot_status_channel_id": _snowflake(interaction.channel),
            },
        )
        embed, view = await _build_main_setup_payload(interaction.guild)  # type: ignore[arg-type]
        embed.add_field(
            name="Saved",
            value=f"Bot status channel set to {interaction.channel.mention}.\n\nNext: Run Health Check.",
            inline=False,
        )
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Run Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:health", row=2)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed = await _build_health_embed(guild)
        await _edit_or_followup(interaction, embed=embed, view=SetupNavView())

    @discord.ui.button(label="Start Over / Cleanup", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:cleanup", row=3)
    async def cleanup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧹 Start Over / Cleanup",
            description=(
                "Use cleanup when setup got messy.\n\n"
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
                "2. Cleanup only the wrong item.\n"
                "3. Come back here.\n"
                "4. Use My Existing Server to pick the correct item.\n"
                "5. Run Health Check."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=SetupNavView())


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
        await interaction.response.edit_message(embed=embed, view=LogsStatusPickerView())

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
        await interaction.response.edit_message(embed=embed, view=BehaviorSettingsView())


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
        embed = discord.Embed(
            title="✅ Saved Setup Role",
            description=(
                f"Saved {_mention(role)}.\n\n"
                "Names do not matter. Dank Shield saved the role ID.\n\n"
                "Next: pick another item, press Back to Setup, or Run Health Check."
            ),
            color=discord.Color.green(),
        )
        if warnings:
            embed.add_field(name="Warnings", value="\n".join(f"• {x}" for x in warnings), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


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
        embed = discord.Embed(
            title="✅ Saved Setup Channel",
            description=(
                f"Saved {_mention(channel)}.\n\n"
                "Names do not matter. Dank Shield saved the channel ID.\n\n"
                "Next: pick another item, press Back to Setup, or Run Health Check."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


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
                placeholder="Where join/leave logs go",
                columns=("join_log_channel_id",),
                also_same=("join_exit_log_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=1,
                require_text=True,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Where bot status messages go",
                columns=("status_channel_id",),
                also_same=("bot_status_channel_id", "health_channel_id"),
                channel_types=[discord.ChannelType.text],
                row=2,
                require_text=True,
            )
        )
        self.add_item(
            SaveChannelSelect(
                placeholder="Bans / blacklist channel, if used",
                columns=("bans_channel_id",),
                also_same=("blacklist_channel_id",),
                channel_types=[discord.ChannelType.text],
                row=3,
                require_text=True,
            )
        )


# ---------------------------------------------------------------------------
# behavior settings
# ---------------------------------------------------------------------------


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
        embed = discord.Embed(
            title="✅ Saved Access Role Style",
            description=(
                f"Saved mode: `{mode}`\n\n"
                "Next: pick custom roles if needed, then Run Health Check."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class BehaviorSettingsView(SetupNavView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(VerificationModeSelect())

    @discord.ui.button(label="Set Prefix / Timer", emoji="⏱️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:behavior_modal", row=1)
    async def modal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(BehaviorSettingsModal())

    @discord.ui.button(label="Clear Optional Access Roles", emoji="🧽", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:clear_access_roles", row=1)
    async def clear_access_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await _clear_config_keys(interaction, ("unverified_role_id", "verified_role_id", "resident_role_id"))
        embed = discord.Embed(
            title="✅ Optional Access Roles Cleared",
            description=(
                "Cleared the saved new/waiting, approved, and full-access role slots.\n\n"
                "This did not delete any Discord roles. It only removed Dank Shield's saved choices."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


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
                description="No values were entered. Press Back to Setup to continue.",
                color=discord.Color.dark_grey(),
            )
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await _save_config(interaction, payload)
        embed = discord.Embed(
            title="✅ Behavior Settings Saved",
            description="Saved your settings. Next: Run Health Check.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
            "These are the choices users see when they open a ticket, like Support, Appeal, or Report.\n"
            "This is not where ticket channels are stored. Use **Ticket Basics** for open/closed ticket folders."
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
        embed.add_field(name="Current Ticket Menu Options", value=_category_list_text(load.rows), inline=False)
        embed.add_field(name="Safety", value=_category_governance_text(load.rows), inline=False)
        embed.add_field(
            name="Plain Meaning",
            value=(
                "• **Name** is what staff/users see.\n"
                "• **Keywords** help the bot recommend the right option.\n"
                "• **Default** is used when Dank Shield is unsure."
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


async def _seed_recommended_categories(guild: discord.Guild) -> tuple[list[str], list[str], str]:
    load = await _category_load(guild)
    if load.error:
        return [], [], load.error
    existing = {str(row.get("slug") or "").strip().lower() for row in load.rows}
    created: list[str] = []
    skipped: list[str] = []
    has_default = any(bool(row.get("is_default")) for row in load.rows)

    for item in RECOMMENDED_CATEGORIES:
        slug = _slugify(item["slug"])
        if slug in existing:
            skipped.append(slug)
            continue
        payload = dict(item)
        payload["slug"] = slug
        payload["is_default"] = bool(item.get("is_default")) and not has_default
        ok, msg = await _write_category(guild, payload, set_default=bool(payload["is_default"]))
        if not ok:
            return created, skipped, msg
        created.append(slug)
        existing.add(slug)
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
        if not db_error:
            self.add_item(CategorySelect(rows, action="edit", placeholder="Edit a ticket menu option", row=0))
            self.add_item(CategorySelect(rows, action="default", placeholder="Pick the default ticket option", row=1))

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
        embed.add_field(name="Created", value=_line_list([f"`{x}`" for x in created], empty="Nothing new created."), inline=False)
        embed.add_field(name="Already Existed", value=_line_list([f"`{x}`" for x in skipped], empty="None"), inline=False)
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
