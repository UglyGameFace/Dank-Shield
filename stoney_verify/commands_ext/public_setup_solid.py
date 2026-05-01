from __future__ import annotations

"""Solid public /stoney setup flow.

This module is the public setup source of truth. It deliberately keeps owners in
one boring path:

/stoney setup

Everything that used to require hidden setup-* commands is exposed here through
buttons, selects, modals, validation, and a real health screen.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _category_missing_perms,
    _config_embed,
    _field_text,
    _require_setup_permission,
    _safe_str,
    _text_channel_missing_perms,
    _utc_iso,
    _can_manage_role,
    stoney_group,
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
        "match_keywords": ["verify", "verification", "unverified", "vc verify"],
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


async def _safe_defer_update(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=False)
    except Exception:
        pass


async def _safe_defer_modal(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _edit_or_followup(interaction: discord.Interaction, *, embed: discord.Embed, view: Optional[discord.ui.View] = None) -> None:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
        return
    except Exception:
        pass

    try:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception:
        pass


async def _save_config(interaction: discord.Interaction, payload: dict[str, Any]) -> None:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This must be used inside a server.")
    from .public_setup_config_writer import upsert_guild_config

    final = dict(payload)
    final.update(
        {
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
            "configured_at": _utc_iso(),
        }
    )
    await upsert_guild_config(guild.id, final)
    invalidate_guild_config(guild.id)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


async def _category_load(guild: discord.Guild) -> CategoryLoad:
    from . import ticket_category_admin as category_admin

    def _read_sync() -> CategoryLoad:
        sb = get_supabase()
        if sb is None:
            return CategoryLoad([], "Supabase is not available, so ticket routing categories cannot be checked.")
        try:
            res = (
                sb.table("ticket_categories")
                .select("*")
                .eq("guild_id", str(int(guild.id)))
                .execute()
            )
            rows_raw = getattr(res, "data", None) or []
            rows = [category_admin._normalize_category_row(x) for x in rows_raw if isinstance(x, dict)]
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
    intake_type = str(row.get("intake_type") or "general")
    default = " ⭐" if bool(row.get("is_default")) else ""
    keywords = row.get("match_keywords") or []
    keyword_text = ", ".join(str(x) for x in keywords[:4]) if keywords else "no keywords"
    order = row.get("sort_order")
    order_text = f" • sort `{order}`" if order is not None else ""
    return f"• **{_short(name, 48)}**{default} — `{slug}` • `{intake_type}`{order_text}\n  ↳ {_short(keyword_text, 90)}"


def _category_list_text(rows: list[dict[str, Any]], *, empty: str = "No ticket categories yet.") -> str:
    if not rows:
        return empty
    lines = [_category_line(row) for row in rows[:12]]
    if len(rows) > 12:
        lines.append(f"…and {len(rows) - 12} more")
    return "\n".join(lines)[:1024] or empty


def _category_governance_text(rows: list[dict[str, Any]]) -> str:
    try:
        from . import ticket_category_admin as category_admin

        warnings = category_admin._governance_warnings(rows)
    except Exception:
        warnings = []
    if not warnings:
        return "✅ Default and verification routing look safe."
    return "\n".join(f"• {item}" for item in warnings)[:1024]


async def _build_health_embed(guild: discord.Guild) -> discord.Embed:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        b, w, p = _build_setup_health(guild, cfg)
        blockers.extend([str(x).replace("/stoney setup-tickets", "/stoney setup") for x in b])
        warnings.extend([str(x).replace("/stoney setup-tickets", "/stoney setup") for x in w])
        ok.extend(p)
    except Exception as e:
        blockers.append(f"Could not load this server's saved config: {type(e).__name__}: {str(e)[:250]}")

    category_load = await _category_load(guild)
    if category_load.error:
        blockers.append(category_load.error)
    elif not category_load.rows:
        warnings.append("No ticket routing categories exist yet. Use `/stoney setup → Manage Ticket Categories → Create Recommended`.")
    else:
        ok.append(f"Ticket routing categories loaded: `{len(category_load.rows)}`.")
        try:
            from . import ticket_category_admin as category_admin

            for warning in category_admin._governance_warnings(category_load.rows):
                warnings.append(warning)
        except Exception:
            pass

    ready = not blockers
    embed = discord.Embed(
        title="🩺 Stoney Setup Health",
        description="✅ **Ready enough to test**" if ready else "🚫 **Needs fixes before public use**",
        color=discord.Color.green() if ready else discord.Color.red(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported."), inline=False)
    embed.set_footer(text=f"Guild {guild.id} • use /stoney setup to fix anything listed here")
    return embed


# ---------------------------------------------------------------------------
# main setup card
# ---------------------------------------------------------------------------


async def _build_main_setup_payload(guild: discord.Guild) -> tuple[discord.Embed, "SolidSetupView"]:
    cfg = None
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    embed = discord.Embed(
        title="🚀 Stoney Quick Setup",
        description=(
            "Everything starts here. Pick the setup path that matches this server.\n\n"
            "✨ **Auto-Fix Missing Defaults** creates missing default roles/channels/categories.\n"
            "✏️ **Customize Setup Names** lets you rename default items before creating them.\n"
            "🧩 **Choose Existing Items** maps your current roles/channels safely.\n"
            "🗂️ **Manage Ticket Categories** controls support/verification/appeal/report routing.\n"
            "🩺 **Run Health Check** shows blockers, warnings, and passing checks."
        ),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    if cfg is not None:
        embed.add_field(
            name="Current Config Snapshot",
            value=(
                f"Open tickets: {_mention(guild.get_channel(int(getattr(cfg, 'ticket_category_id', 0) or 0)) or 'Not set')}\n"
                f"Archive: {_mention(guild.get_channel(int(getattr(cfg, 'ticket_archive_category_id', 0) or 0)) or 'Not set')}\n"
                f"Staff role: {_mention(guild.get_role(int(getattr(cfg, 'staff_role_id', 0) or 0)) or 'Not set')}"
            )[:1024],
            inline=False,
        )
    return embed, SolidSetupView()


class BackToSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

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


class SolidSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Auto-Fix Missing Defaults", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_solid:auto", row=0)
    async def auto_fix(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        try:
            from . import public_setup_defaults

            await public_setup_defaults._setup_defaults_callback(interaction)
            if interaction.guild is not None:
                created, skipped, error = await _seed_recommended_categories(interaction.guild)
                if error:
                    await interaction.followup.send(f"⚠️ Defaults were handled, but recommended ticket categories could not be checked: `{error}`", ephemeral=True)
                elif created:
                    await interaction.followup.send(f"✅ Recommended ticket categories created: {', '.join(f'`{x}`' for x in created)}", ephemeral=True)
                elif skipped:
                    await interaction.followup.send("✅ Recommended ticket categories already exist.", ephemeral=True)
        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Auto-fix failed: `{type(e).__name__}: {str(e)[:250]}`", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Auto-fix failed: `{type(e).__name__}: {str(e)[:250]}`", ephemeral=True)

    @discord.ui.button(label="Customize Setup Names", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:customize", row=0)
    async def customize(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        # Reuse the existing modal pages, but keep entry through /stoney setup.
        try:
            from .public_setup_start import CustomizeSetupMenuView

            embed = discord.Embed(
                title="✏️ Customize Setup Names",
                description=(
                    "Discord modals can only show 5 text fields at a time, so setup names are split into simple pages.\n\n"
                    "Use this when you want Stoney to create roles/channels, but with your names instead of the defaults."
                ),
                color=discord.Color.blurple(),
            )
            await interaction.response.edit_message(embed=embed, view=CustomizeSetupMenuView())
        except Exception as e:
            await interaction.response.send_message(f"❌ Custom setup names failed to open: `{type(e).__name__}: {str(e)[:250]}`", ephemeral=True)

    @discord.ui.button(label="Choose Existing Items", emoji="🧩", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:existing", row=1)
    async def choose_existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧩 Choose Existing Items",
            description=(
                "Use this when the server already has its own roles/channels/categories.\n"
                "Each picker validates permissions before saving."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Sections",
            value=(
                "🎫 **Ticket Basics** — open/archive Discord categories, staff role, transcripts\n"
                "✅ **Verification Roles** — Unverified, Verified, Member\n"
                "🎙️ **Verification Channels** — verify text, support panel, VC verify, VC queue\n"
                "🧾 **Logs + Status** — modlog, join/leave log, bot status"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ChooseExistingView())

    @discord.ui.button(label="Manage Ticket Categories", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:categories", row=1)
    async def categories(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Use This Channel for Status", emoji="📌", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:status", row=2)
    async def status_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ Use this inside the text channel you want as the bot status channel.", ephemeral=True)
        await _safe_defer_update(interaction)
        await _save_config(interaction, {"status_channel_id": _snowflake(interaction.channel), "bot_status_channel_id": _snowflake(interaction.channel)})
        embed, view = await _build_main_setup_payload(interaction.guild)  # type: ignore[arg-type]
        embed.add_field(name="Saved", value=f"Bot status channel set to {interaction.channel.mention}.", inline=False)
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
        await _edit_or_followup(interaction, embed=embed, view=BackToSetupView())


# ---------------------------------------------------------------------------
# validated existing item setup
# ---------------------------------------------------------------------------


class ChooseExistingView(BackToSetupView):
    @discord.ui.button(label="Ticket Basics", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="stoney_solid:existing_ticket", row=0)
    async def ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="🎫 Ticket Basics", description="Pick the existing ticket items. Each save is validated first.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=TicketBasicsPickerView())

    @discord.ui.button(label="Verification Roles", emoji="✅", style=discord.ButtonStyle.primary, custom_id="stoney_solid:existing_roles", row=1)
    async def roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="✅ Verification Roles", description="Pick the roles Stoney should assign/remove during verification.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=VerificationRolesPickerView())

    @discord.ui.button(label="Verification Channels", emoji="🎙️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:existing_channels", row=2)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="🎙️ Verification Channels", description="Pick verification text/voice channels.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=VerificationChannelsPickerView())

    @discord.ui.button(label="Logs + Status", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="stoney_solid:existing_logs", row=3)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="🧾 Logs + Status", description="Pick logging/status channels.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=LogsStatusPickerView())


class SaveRoleSelect(discord.ui.RoleSelect):
    def __init__(self, *, placeholder: str, columns: tuple[str, ...], require_manage: bool, also_same: tuple[str, ...] = (), row: int = 0) -> None:
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
            embed = discord.Embed(title="🚫 Role Not Saved", description="\n".join(f"• {x}" for x in blockers), color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        payload = {column: _snowflake(role) for column in self.columns + self.also_same}
        await _save_config(interaction, payload)
        embed = discord.Embed(title="✅ Saved Setup Role", description=f"Saved {_mention(role)}.", color=discord.Color.green())
        if warnings:
            embed.add_field(name="Warnings", value="\n".join(f"• {x}" for x in warnings), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
            embed = discord.Embed(title="🚫 Channel Not Saved", description="\n".join(f"• {x}" for x in blockers), color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        payload = {column: _snowflake(channel) for column in self.columns + self.also_same}
        await _save_config(interaction, payload)
        await interaction.response.send_message(embed=discord.Embed(title="✅ Saved Setup Channel", description=f"Saved {_mention(channel)}.", color=discord.Color.green()), ephemeral=True)


class TicketBasicsPickerView(BackToSetupView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Open ticket category", columns=("ticket_category_id",), channel_types=[discord.ChannelType.category], row=0, require_category_manage=True))
        self.add_item(SaveChannelSelect(placeholder="Archive/closed ticket category", columns=("ticket_archive_category_id",), channel_types=[discord.ChannelType.category], row=1, require_category_manage=True))
        self.add_item(SaveRoleSelect(placeholder="Ticket staff role", columns=("staff_role_id",), also_same=("vc_staff_role_id",), require_manage=False, row=2))
        self.add_item(SaveChannelSelect(placeholder="Transcript text channel", columns=("transcripts_channel_id",), channel_types=[discord.ChannelType.text], row=3, require_text=True, require_files=True))


class VerificationRolesPickerView(BackToSetupView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveRoleSelect(placeholder="Unverified role", columns=("unverified_role_id",), require_manage=True, row=0))
        self.add_item(SaveRoleSelect(placeholder="Verified role", columns=("verified_role_id",), require_manage=True, row=1))
        self.add_item(SaveRoleSelect(placeholder="Member/Resident role", columns=("resident_role_id",), require_manage=True, row=2))


class VerificationChannelsPickerView(BackToSetupView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Verify text channel", columns=("verify_channel_id",), channel_types=[discord.ChannelType.text], row=0, require_text=True))
        self.add_item(SaveChannelSelect(placeholder="Support/ticket panel channel", columns=("ticket_panel_channel_id",), also_same=("support_channel_id",), channel_types=[discord.ChannelType.text], row=1, require_text=True))
        self.add_item(SaveChannelSelect(placeholder="VC verification voice channel", columns=("vc_verify_channel_id",), channel_types=[discord.ChannelType.voice], row=2))
        self.add_item(SaveChannelSelect(placeholder="VC queue/status text channel", columns=("vc_verify_queue_channel_id",), channel_types=[discord.ChannelType.text], row=3, require_text=True))


class LogsStatusPickerView(BackToSetupView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Modlog channel", columns=("modlog_channel_id",), also_same=("raidlog_channel_id", "force_verify_log_channel_id"), channel_types=[discord.ChannelType.text], row=0, require_text=True))
        self.add_item(SaveChannelSelect(placeholder="Join/leave log channel", columns=("join_log_channel_id",), channel_types=[discord.ChannelType.text], row=1, require_text=True))
        self.add_item(SaveChannelSelect(placeholder="Bot status channel", columns=("status_channel_id",), also_same=("bot_status_channel_id",), channel_types=[discord.ChannelType.text], row=2, require_text=True))


# ---------------------------------------------------------------------------
# category manager
# ---------------------------------------------------------------------------


async def _build_category_manager_payload(guild: discord.Guild, *, title: str = "🗂️ Manage Ticket Categories") -> tuple[discord.Embed, "CategoryManagerView"]:
    load = await _category_load(guild)
    embed = discord.Embed(
        title=title,
        description=(
            "These are logical routing/intake categories, not Discord channel categories.\n"
            "Use **Ticket Basics** for the actual Discord open/archive categories."
        ),
        color=discord.Color.blurple() if not load.error else discord.Color.red(),
        timestamp=now_utc(),
    )
    if load.error:
        embed.add_field(name="Database Problem", value=load.error[:1024], inline=False)
        embed.add_field(name="Fix", value="Confirm the `ticket_categories` table exists and Supabase is reachable, then restart or refresh.", inline=False)
    else:
        embed.add_field(name="Current Categories", value=_category_list_text(load.rows), inline=False)
        embed.add_field(name="Safety", value=_category_governance_text(load.rows), inline=False)
    return embed, CategoryManagerView(rows=load.rows, db_error=load.error)


def _category_options(rows: list[dict[str, Any]], *, placeholder: str) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for row in rows[:25]:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        name = str(row.get("name") or slug).strip()
        intake_type = str(row.get("intake_type") or "general").strip()
        default = "Default • " if bool(row.get("is_default")) else ""
        options.append(discord.SelectOption(label=_short(name, 95) or slug, description=_short(f"{default}{slug} • {intake_type}", 100), value=slug[:100]))
    if not options:
        options.append(discord.SelectOption(label="No categories available", value="__none__", description=placeholder[:100]))
    return options


async def _seed_recommended_categories(guild: discord.Guild) -> tuple[list[str], list[str], str]:
    from . import ticket_category_admin as category_admin

    load = await _category_load(guild)
    if load.error:
        return [], [], load.error
    existing = {category_admin._safe_str(row.get("slug")).lower() for row in load.rows}
    created: list[str] = []
    skipped: list[str] = []
    has_default = any(bool(row.get("is_default")) for row in load.rows)

    for item in RECOMMENDED_CATEGORIES:
        slug = category_admin._slugify(str(item["slug"]))
        if slug in existing:
            skipped.append(slug)
            continue
        payload = dict(item)
        payload["guild_id"] = str(guild.id)
        payload["slug"] = slug
        payload["is_default"] = bool(item.get("is_default")) and not has_default
        ok = await category_admin._insert_category(payload)
        if ok:
            created.append(slug)
            existing.add(slug)
            if payload["is_default"]:
                await category_admin._set_default(guild.id, slug)
                has_default = True
        else:
            return created, skipped, f"Database insert failed while creating `{slug}`."

    if not has_default:
        rows = (await _category_load(guild)).rows
        if rows:
            first_slug = category_admin._safe_str(rows[0].get("slug"))
            if first_slug:
                await category_admin._set_default(guild.id, first_slug)
    return created, skipped, ""


async def _delete_category_safely(guild: discord.Guild, slug: str) -> tuple[bool, str]:
    from . import ticket_category_admin as category_admin

    slug_clean = category_admin._slugify(slug)
    row = await category_admin._fetch_category_by_slug(guild.id, slug_clean)
    if not row:
        return False, f"Category `{slug_clean}` was not found."
    rows_before = await category_admin._fetch_categories(guild.id)
    if len(rows_before) <= 1:
        return False, "You cannot delete the only remaining ticket category. Create another category first."
    verification_rows = category_admin._verification_like_categories(rows_before)
    deleting_verification_like = any(category_admin._safe_str(x.get("slug")).lower() == slug_clean for x in verification_rows)
    if deleting_verification_like and len(verification_rows) <= 1:
        return False, "You cannot delete the only verification-like category. Create another verification category first."
    replacement_default = category_admin._choose_replacement_default(rows_before, slug_clean) if bool(row.get("is_default")) else None
    ok = await category_admin._delete_category(guild.id, slug_clean)
    if not ok:
        return False, f"Failed to delete `{slug_clean}`."
    if replacement_default is not None:
        replacement_slug = category_admin._safe_str(replacement_default.get("slug"))
        if replacement_slug:
            await category_admin._set_default(guild.id, replacement_slug)
            return True, f"Deleted `{slug_clean}`. Auto-promoted `{replacement_slug}` as the new default."
    return True, f"Deleted `{slug_clean}`."


class CategoryManagerView(BackToSetupView):
    def __init__(self, *, rows: list[dict[str, Any]], db_error: str = "") -> None:
        super().__init__()
        self.rows = rows
        self.db_error = db_error
        if db_error:
            for child in self.children:
                if getattr(child, "custom_id", "") != "stoney_solid:cat_refresh" and getattr(child, "custom_id", "") != "stoney_solid:back":
                    child.disabled = True
        elif not rows:
            try:
                self.edit_category.disabled = True
                self.set_default.disabled = True
                self.delete_category.disabled = True
            except Exception:
                pass

    @discord.ui.button(label="Create Recommended", emoji="🧱", style=discord.ButtonStyle.success, custom_id="stoney_solid:cat_seed", row=0)
    async def seed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        created, skipped, error = await _seed_recommended_categories(guild)
        embed, view = await _build_category_manager_payload(guild, title="🧱 Recommended Categories")
        if error:
            embed.add_field(name="Result", value=f"🚫 {error}", inline=False)
            embed.color = discord.Color.red()
        elif created:
            embed.add_field(name="Created", value=", ".join(f"`{x}`" for x in created), inline=False)
        else:
            embed.add_field(name="Result", value="✅ Recommended categories already exist.", inline=False)
        if skipped:
            embed.add_field(name="Already existed", value=", ".join(f"`{x}`" for x in skipped[:20]), inline=False)
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Add Category", emoji="➕", style=discord.ButtonStyle.primary, custom_id="stoney_solid:cat_add", row=0)
    async def add_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(AddTicketCategoryModal(existing_count=len(self.rows)))

    @discord.ui.button(label="Edit Category", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="stoney_solid:cat_edit", row=1)
    async def edit_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="edit")

    @discord.ui.button(label="Set Default", emoji="⭐", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:cat_default", row=1)
    async def set_default(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="default")

    @discord.ui.button(label="Delete Category", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="stoney_solid:cat_delete", row=2)
    async def delete_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="delete")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:cat_refresh", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)

    async def _open_select(self, interaction: discord.Interaction, *, action: str) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        load = await _category_load(guild)
        if load.error:
            embed, view = await _build_category_manager_payload(guild)
            return await _edit_or_followup(interaction, embed=embed, view=view)
        if not load.rows:
            embed, view = await _build_category_manager_payload(guild)
            embed.add_field(name="No Categories", value="Create recommended categories or add one manually first.", inline=False)
            return await _edit_or_followup(interaction, embed=embed, view=view)
        label = {"edit": "Edit a category", "default": "Choose the default category", "delete": "Delete a category"}.get(action, "Choose a category")
        embed = discord.Embed(title=f"🗂️ {label}", description="Pick a category from the dropdown below.", color=discord.Color.blurple())
        embed.add_field(name="Current Categories", value=_category_list_text(load.rows), inline=False)
        await _edit_or_followup(interaction, embed=embed, view=CategorySelectActionView(rows=load.rows, action=action))


class CategorySelectActionView(BackToSetupView):
    def __init__(self, *, rows: list[dict[str, Any]], action: str) -> None:
        super().__init__()
        self.add_item(CategoryActionSelect(rows=rows, action=action))


class CategoryActionSelect(discord.ui.Select):
    def __init__(self, *, rows: list[dict[str, Any]], action: str) -> None:
        self.rows = rows
        self.action = action
        placeholder = {"edit": "Choose a category to edit", "default": "Choose the default category", "delete": "Choose a category to delete"}.get(action, "Choose a category")
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=_category_options(rows, placeholder=placeholder), row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        slug = str(self.values[0])
        if slug == "__none__":
            return await interaction.response.send_message("❌ No categories are available yet.", ephemeral=True)
        from . import ticket_category_admin as category_admin

        if self.action == "edit":
            row = next((x for x in self.rows if str(x.get("slug")) == slug), None)
            if not row:
                return await interaction.response.send_message("❌ That category no longer exists. Refresh and try again.", ephemeral=True)
            return await interaction.response.send_modal(EditTicketCategoryModal(row=row))
        if self.action == "default":
            await _safe_defer_update(interaction)
            ok = await category_admin._set_default(guild.id, slug)
            embed, view = await _build_category_manager_payload(guild, title="⭐ Default Ticket Category Updated" if ok else "🚫 Default Update Failed")
            embed.add_field(name="Result", value=(f"`{slug}` is now the default category." if ok else f"Could not set `{slug}` as default."), inline=False)
            embed.color = discord.Color.green() if ok else discord.Color.red()
            return await _edit_or_followup(interaction, embed=embed, view=view)
        if self.action == "delete":
            row = next((x for x in self.rows if str(x.get("slug")) == slug), None)
            embed = discord.Embed(title="🗑️ Confirm Category Delete", description=f"Delete `{slug}`?\n\nThis does **not** delete old ticket channels. It only removes this routing/intake category.", color=discord.Color.red())
            if row:
                embed.add_field(name="Selected", value=_category_line(row), inline=False)
            return await interaction.response.edit_message(embed=embed, view=ConfirmDeleteCategoryView(slug=slug))


class ConfirmDeleteCategoryView(BackToSetupView):
    def __init__(self, *, slug: str) -> None:
        super().__init__()
        self.slug = slug

    @discord.ui.button(label="Yes, Delete Category", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="stoney_solid:cat_delete_yes", row=0)
    async def confirm_delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        ok, message = await _delete_category_safely(guild, self.slug)
        embed, view = await _build_category_manager_payload(guild, title="✅ Category Deleted" if ok else "🚫 Category Not Deleted")
        embed.add_field(name="Result", value=message, inline=False)
        embed.color = discord.Color.green() if ok else discord.Color.red()
        await _edit_or_followup(interaction, embed=embed, view=view)

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="stoney_solid:cat_delete_cancel", row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_or_followup(interaction, embed=embed, view=view)


class AddTicketCategoryModal(discord.ui.Modal):
    def __init__(self, *, existing_count: int = 0) -> None:
        super().__init__(title="Add Ticket Category")
        self.existing_count = int(existing_count)
        self.name_input = discord.ui.TextInput(label="Display name", placeholder="Support", max_length=120)
        self.slug_input = discord.ui.TextInput(label="Slug (optional)", placeholder="support", required=False, max_length=80)
        self.type_input = discord.ui.TextInput(label="Type", placeholder="support, verification, appeal, report, custom", default="support", max_length=40)
        self.keywords_input = discord.ui.TextInput(label="Routing keywords", placeholder="help, support, issue", required=False, max_length=300)
        self.description_input = discord.ui.TextInput(label="Description", placeholder="General help and support tickets", required=False, style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.name_input)
        self.add_item(self.slug_input)
        self.add_item(self.type_input)
        self.add_item(self.keywords_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_modal(interaction)
        from . import ticket_category_admin as category_admin

        name = category_admin._normalize_name(str(self.name_input.value or ""))
        slug = category_admin._slugify(str(self.slug_input.value or name))
        intake_type = category_admin._safe_str(str(self.type_input.value or "general"), "general").lower()
        if not name or not slug:
            return await _category_result(interaction, guild, "🚫 Category Not Created", "Name/slug is invalid.", ok=False)
        if intake_type not in category_admin._ALLOWED_INTAKE_TYPES:
            return await _category_result(interaction, guild, "🚫 Category Not Created", f"Invalid type. Use one of: {category_admin._human_intake_types()}", ok=False)
        if await category_admin._fetch_category_by_slug(guild.id, slug):
            return await _category_result(interaction, guild, "🚫 Category Not Created", f"Category `{slug}` already exists.", ok=False)
        rows = (await _category_load(guild)).rows
        payload = {
            "guild_id": str(guild.id),
            "slug": slug,
            "name": name,
            "description": category_admin._normalize_description(str(self.description_input.value or "")),
            "intake_type": intake_type,
            "match_keywords": category_admin._normalize_keywords(str(self.keywords_input.value or "")),
            "is_default": len(rows) == 0,
            "sort_order": (len(rows) + 1) * 10,
        }
        ok = await category_admin._insert_category(payload)
        if ok and payload["is_default"]:
            await category_admin._set_default(guild.id, slug)
        await _category_result(interaction, guild, "✅ Category Created" if ok else "🚫 Category Not Created", f"Created `{slug}`." if ok else "Database insert failed.", ok=ok)


class EditTicketCategoryModal(discord.ui.Modal):
    def __init__(self, *, row: dict[str, Any]) -> None:
        self.row = row
        slug = str(row.get("slug") or "")
        super().__init__(title=f"Edit {slug[:35]}")
        self.name_input = discord.ui.TextInput(label="Display name", default=str(row.get("name") or slug)[:120], required=False, max_length=120)
        self.type_input = discord.ui.TextInput(label="Type", default=str(row.get("intake_type") or "general")[:40], required=False, max_length=40)
        self.keywords_input = discord.ui.TextInput(label="Routing keywords", default=", ".join(str(x) for x in (row.get("match_keywords") or []))[:300], required=False, max_length=300)
        self.description_input = discord.ui.TextInput(label="Description", default=str(row.get("description") or "")[:500], required=False, style=discord.TextStyle.paragraph, max_length=500)
        self.sort_input = discord.ui.TextInput(label="Sort order", default=str(row.get("sort_order") if row.get("sort_order") is not None else ""), required=False, max_length=8)
        self.add_item(self.name_input)
        self.add_item(self.type_input)
        self.add_item(self.keywords_input)
        self.add_item(self.description_input)
        self.add_item(self.sort_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_modal(interaction)
        from . import ticket_category_admin as category_admin

        slug = category_admin._safe_str(self.row.get("slug"))
        patch: dict[str, Any] = {}
        name = category_admin._normalize_name(str(self.name_input.value or ""))
        if name:
            patch["name"] = name
        intake_type = category_admin._safe_str(str(self.type_input.value or "general"), "general").lower()
        if intake_type not in category_admin._ALLOWED_INTAKE_TYPES:
            return await _category_result(interaction, guild, "🚫 Category Not Updated", f"Invalid type. Use one of: {category_admin._human_intake_types()}", ok=False)
        patch["intake_type"] = intake_type
        patch["match_keywords"] = category_admin._normalize_keywords(str(self.keywords_input.value or ""))
        patch["description"] = category_admin._normalize_description(str(self.description_input.value or ""))
        sort_raw = str(self.sort_input.value or "").strip()
        if sort_raw:
            try:
                sort_value = int(sort_raw)
            except Exception:
                return await _category_result(interaction, guild, "🚫 Category Not Updated", "Sort order must be a number.", ok=False)
            sort_clean = category_admin._validated_sort_order(sort_value)
            if sort_clean is None:
                return await _category_result(interaction, guild, "🚫 Category Not Updated", f"Sort order must be between `{category_admin._MIN_SORT_ORDER}` and `{category_admin._MAX_SORT_ORDER}`.", ok=False)
            patch["sort_order"] = sort_clean
        ok = await category_admin._update_category(guild.id, slug, patch)
        await _category_result(interaction, guild, "✅ Category Updated" if ok else "🚫 Category Not Updated", f"Updated `{slug}`." if ok else "Database update failed.", ok=ok)


async def _category_result(interaction: discord.Interaction, guild: discord.Guild, title: str, message: str, *, ok: bool = True) -> None:
    embed, view = await _build_category_manager_payload(guild, title=title)
    embed.add_field(name="Result", value=message[:1024], inline=False)
    embed.color = discord.Color.green() if ok else discord.Color.red()
    try:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception:
        await _edit_or_followup(interaction, embed=embed, view=view)


# ---------------------------------------------------------------------------
# command registration
# ---------------------------------------------------------------------------


async def _setup_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)
    try:
        embed, view = await _build_main_setup_payload(guild)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Setup failed: `{type(e).__name__}: {str(e)[:300]}`", ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return
    try:
        existing = stoney_group.get_command("setup")
        if existing is not None:
            stoney_group.remove_command("setup")
    except Exception:
        pass
    stoney_group.add_command(discord.app_commands.Command(name="setup", description="Start the guided Stoney setup flow.", callback=_setup_callback))
    _ATTACHED = True


_attach()


def register_public_setup_solid_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    print("✅ public_setup_solid: attached hardened /stoney setup guided flow")


__all__ = ["register_public_setup_solid_commands"]
