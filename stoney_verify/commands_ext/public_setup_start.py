from __future__ import annotations

from typing import Any, Iterable, Optional

import discord

from .common import safe_defer
from .public_setup_group import _require_setup_permission, stoney_group
from ..guild_config import get_guild_config, invalidate_guild_config


_ATTACHED = False

_LEGACY_COMMAND_REPLACEMENTS = {
    "/dank setup-picker": "/dank setup",
    "/dank setup-assistant": "/dank setup",
    "/dank setup-defaults": "/dank setup",
    "/dank setup-find": "/dank setup",
    "/dank setup-logs": "/dank setup",
    "/dank setup-review": "/dank setup",
    "/dank setup-status": "/dank setup",
    "/dank setup-tickets": "/dank setup",
    "/dank setup-verify": "/dank setup",
    "/dank setup-verify-ids": "/dank setup",
    "/dank setup-access": "/dank setup",
    "/stoney permission-check": "/dank setup",
    "/dank launch-check": "/dank setup",
    "/stoney production-audit": "/dank setup",
    "/dank tickettool-check": "/dank setup",
    "/stoney db-check": "/dank setup",
    "`/dank setup-picker`": "`/dank setup`",
    "`/dank setup-assistant`": "`/dank setup`",
    "`/dank setup-defaults`": "`/dank setup`",
    "`/dank setup-find`": "`/dank setup`",
    "`/dank setup-logs`": "`/dank setup`",
    "`/dank setup-review`": "`/dank setup`",
    "`/dank setup-status`": "`/dank setup`",
    "`/dank setup-tickets`": "`/dank setup`",
    "`/dank setup-verify`": "`/dank setup`",
    "`/dank setup-verify-ids`": "`/dank setup`",
    "`/dank setup-access`": "`/dank setup`",
    "`/dank permission-check`": "`/dank setup`",
    "`/dank launch-check`": "`/dank setup`",
    "`/dank production-audit`": "`/dank setup`",
    "`/dank tickettool-check`": "`/dank setup`",
    "`/dank db-check`": "`/dank setup`",
}

_CUSTOMIZE_PAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("core", "Core Roles + Ticket Category", ("staff_role", "unverified_role", "verified_role", "resident_role", "ticket_category")),
    ("ticket_verify", "Tickets + Verification Rooms", ("archive_category", "verify_channel", "support_channel", "vc_verify_channel", "vc_queue_channel")),
    ("logs_status", "Logs + Status Channels", ("transcripts_channel", "modlog_channel", "join_log_channel", "status_channel")),
)


def _clean_text(value: Any) -> str:
    try:
        text = str(value or "")
    except Exception:
        return ""
    for old, new in _LEGACY_COMMAND_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text.replace("Setup Assistant", "Quick Setup").replace("setup assistant", "quick setup")


def _clean_embed(embed: discord.Embed) -> discord.Embed:
    try:
        if embed.title:
            embed.title = _clean_text(embed.title)
        if embed.description:
            embed.description = _clean_text(embed.description)[:4096]
        fields = list(getattr(embed, "fields", []) or [])
        if fields:
            embed.clear_fields()
            for field in fields:
                embed.add_field(
                    name=(_clean_text(getattr(field, "name", "")) or "Status")[:256],
                    value=(_clean_text(getattr(field, "value", "")) or "—")[:1024],
                    inline=bool(getattr(field, "inline", False)),
                )
        footer_text = getattr(getattr(embed, "footer", None), "text", "")
        if footer_text:
            embed.set_footer(text=_clean_text(footer_text))
    except Exception:
        pass
    return embed


def _mention(obj: Any) -> str:
    mention = getattr(obj, "mention", None)
    return str(mention) if mention else f"`{getattr(obj, 'name', obj)}`"


def _snowflake(value: Any) -> str:
    return str(int(getattr(value, "id", value)))


def _short(value: Any, limit: int = 90) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _spec_subset(module: Any, keys: Iterable[str]) -> list[Any]:
    try:
        by_key = {str(getattr(spec, "key", "")): spec for spec in getattr(module, "REPAIR_SPECS", ())}
        return [by_key[key] for key in keys if key in by_key]
    except Exception:
        return []


async def _current_missing_specs(guild: discord.Guild, module: Any) -> list[Any]:
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        row = await module._fetch_config_row(guild.id)
        return list(module._missing_repair_specs(guild, cfg, row))
    except Exception:
        return []


async def _save_config(interaction: discord.Interaction, payload: dict[str, Any]) -> None:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This must be used inside a server.")
    from .public_setup_config_writer import upsert_guild_config

    final_payload = dict(payload)
    final_payload.update({"configured_by_id": str(interaction.user.id), "configured_by_name": str(interaction.user)})
    await upsert_guild_config(guild.id, final_payload)
    invalidate_guild_config(guild.id)


async def _safe_defer_update(interaction: discord.Interaction) -> None:
    """Acknowledge a component press immediately so slow DB/config reads do not time out."""
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


async def _edit_setup_message(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    """Edit the current setup card after either a normal response or deferred component update."""
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


def _install_cleaners(module: Any) -> None:
    try:
        if getattr(module, "_STONEY_SETUP_CLEANERS_INSTALLED", False):
            return

        original_health = getattr(module, "_health_embed", None)
        if callable(original_health):
            def cleaned_health(guild: discord.Guild, cfg: Any):
                return _clean_embed(original_health(guild, cfg))
            module._health_embed = cleaned_health

        original_payload = getattr(module, "_build_assistant_payload", None)
        if callable(original_payload):
            module._STONEY_ORIGINAL_BUILD_ASSISTANT_PAYLOAD = original_payload

            async def cleaned_payload(guild: discord.Guild):
                embed, _old_view = await original_payload(guild)
                embed = _clean_embed(embed)
                has_missing = bool(await _current_missing_specs(guild, module))
                return embed, StoneySetupView(has_missing=has_missing)

            module._build_assistant_payload = cleaned_payload

        module._STONEY_SETUP_CLEANERS_INSTALLED = True
    except Exception as e:
        try:
            print(f"⚠️ public_setup_start cleaner install failed: {repr(e)}")
        except Exception:
            pass


async def _build_main_setup_payload(guild: discord.Guild, *, title: str = "🚀 Stoney Quick Setup") -> tuple[discord.Embed, "StoneySetupView"]:
    from . import public_setup_assistant

    _install_cleaners(public_setup_assistant)
    original_payload = getattr(public_setup_assistant, "_STONEY_ORIGINAL_BUILD_ASSISTANT_PAYLOAD", None)
    if callable(original_payload):
        embed, _old_view = await original_payload(guild)
    else:
        embed, _old_view = await public_setup_assistant._build_assistant_payload(guild)

    embed = _clean_embed(embed)
    embed.title = title
    embed.description = (
        "This is the main setup screen. Pick the easiest path below:\n\n"
        "✨ **Auto-Fix Missing Defaults** creates only missing default roles/channels.\n"
        "✏️ **Customize Setup Names** lets you name every default group before creating it.\n"
        "🧩 **Choose Existing Items** lets you map your own roles/channels with dropdowns.\n"
        "🗂️ **Manage Ticket Categories** lets you add/edit/delete routing categories without memorizing commands.\n\n"
        f"{embed.description or ''}"
    )[:4096]
    return _clean_embed(embed), StoneySetupView(has_missing=bool(await _current_missing_specs(guild, public_setup_assistant)))


async def _category_rows(guild: discord.Guild) -> list[dict[str, Any]]:
    from . import ticket_category_admin as category_admin

    return await category_admin._fetch_categories(guild.id)


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
    text = "\n".join(lines)
    return text[:1024] or empty


def _category_governance_text(rows: list[dict[str, Any]]) -> str:
    try:
        from . import ticket_category_admin as category_admin

        warnings = category_admin._governance_warnings(rows)
    except Exception:
        warnings = []
    if not warnings:
        return "✅ Default and verification routing look safe."
    return "\n".join(f"• {item}" for item in warnings)[:1024]


async def _build_category_manager_payload(guild: discord.Guild, *, title: str = "🗂️ Manage Ticket Categories") -> tuple[discord.Embed, "CategoryManagerView"]:
    rows = await _category_rows(guild)
    embed = discord.Embed(
        title=title,
        description=(
            "These are logical routing/intake categories, not Discord channel categories.\n"
            "They control what users can pick and how tickets route to staff."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Current Categories", value=_category_list_text(rows), inline=False)
    embed.add_field(name="Safety", value=_category_governance_text(rows), inline=False)
    embed.add_field(
        name="Tip",
        value=(
            "Use **Ticket Basics** in `/dank setup → Choose Existing Items` for the actual Discord open/archive categories.\n"
            "Use this manager for support/verification/appeal/report-style routing categories."
        ),
        inline=False,
    )
    return embed, CategoryManagerView(rows=rows)


def _category_options(rows: list[dict[str, Any]], *, placeholder: str) -> list[discord.SelectOption]:
    options: list[discord.SelectOption] = []
    for row in rows[:25]:
        slug = str(row.get("slug") or "").strip()
        if not slug:
            continue
        name = str(row.get("name") or slug).strip()
        intake_type = str(row.get("intake_type") or "general").strip()
        default = "Default • " if bool(row.get("is_default")) else ""
        options.append(
            discord.SelectOption(
                label=_short(name, 95) or slug,
                description=_short(f"{default}{slug} • {intake_type}", 100),
                value=slug[:100],
            )
        )
    if not options:
        options.append(discord.SelectOption(label="No categories available", value="__none__", description=placeholder[:100]))
    return options


async def _send_category_result(interaction: discord.Interaction, *, guild: discord.Guild, title: str, message: str, ok: bool = True) -> None:
    embed, view = await _build_category_manager_payload(guild, title=title)
    embed.add_field(name="Result", value=message[:1024] or "Done.", inline=False)
    embed.color = discord.Color.green() if ok else discord.Color.red()
    try:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception:
        try:
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception:
            pass


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

    suffix = ""
    if replacement_default is not None:
        replacement_slug = category_admin._safe_str(replacement_default.get("slug"))
        if replacement_slug:
            await category_admin._set_default(guild.id, replacement_slug)
            suffix = f" Auto-promoted `{replacement_slug}` as the new default."

    return True, f"Deleted `{slug_clean}`.{suffix}"


class BackToSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Back to Setup", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_main_setup_payload(guild)
        await _edit_setup_message(interaction, embed=embed, view=view)


class StoneySetupView(discord.ui.View):
    def __init__(self, *, has_missing: bool) -> None:
        super().__init__(timeout=900)
        if not bool(has_missing):
            try:
                self.auto_fix.disabled = True
            except Exception:
                pass

    @discord.ui.button(label="Auto-Fix Missing Defaults", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_setup:auto_fix", row=0)
    async def auto_fix(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("❌ This must be used inside a server.", ephemeral=True)
        from . import public_setup_assistant

        _install_cleaners(public_setup_assistant)
        specs = await _current_missing_specs(guild, public_setup_assistant)
        if not specs:
            embed, view = await _build_main_setup_payload(guild)
            return await interaction.followup.send("✅ Nothing missing right now.", embed=embed, view=view, ephemeral=True)
        await public_setup_assistant._repair_specs(interaction, specs)

    @discord.ui.button(label="Customize Setup Names", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="stoney_setup:customize", row=0)
    async def customize(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="✏️ Customize Setup Names",
            description=(
                "Discord modals can only show 5 text fields at a time, so setup names are split into simple pages.\n\n"
                "Use this when you want Stoney to create channels/roles, but **not** with the default names."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Pages",
            value=(
                "**Core Roles + Ticket Category** — staff/member roles and active ticket category\n"
                "**Tickets + Verification Rooms** — archive/support/verify/VC items\n"
                "**Logs + Status Channels** — transcripts, modlog, join log, bot status"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=CustomizeSetupMenuView())

    @discord.ui.button(label="Choose Existing Items", emoji="🧩", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:choose_existing", row=1)
    async def choose_existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="🧩 Choose Existing Items",
            description=(
                "Use this when the server already has roles/channels and you do **not** want Stoney to create new defaults.\n\n"
                "Pick a setup section below. Each picker saves immediately and stays inside `/dank setup`."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Sections",
            value=(
                "🎫 **Ticket Basics** — active/archive Discord categories, staff role, transcripts\n"
                "✅ **Verification Roles** — Unverified, Verified, Member\n"
                "🎙️ **Verification Channels** — verify text, support panel, VC verify, VC queue\n"
                "🧾 **Logs + Status** — modlog, join/leave log, bot-status"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ChooseExistingMenuView())

    @discord.ui.button(label="Manage Ticket Categories", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="stoney_setup:ticket_categories", row=1)
    async def ticket_categories(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_setup_message(interaction, embed=embed, view=view)

    @discord.ui.button(label="Use This Channel for Status", emoji="📌", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:use_status_channel", row=2)
    async def use_status_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ Use this inside the text channel you want as the bot status channel.", ephemeral=True)
        await _safe_defer_update(interaction)
        await _save_config(interaction, {"status_channel_id": _snowflake(interaction.channel), "bot_status_channel_id": _snowflake(interaction.channel)})
        embed, view = await _build_main_setup_payload(interaction.guild)  # type: ignore[arg-type]
        embed.add_field(name="Saved", value=f"Bot status channel set to {interaction.channel.mention}.", inline=False)
        await _edit_setup_message(interaction, embed=embed, view=view)

    @discord.ui.button(label="Run Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:health", row=2)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_main_setup_payload(guild, title="🩺 Dank Shield Setup Health")
        await _edit_setup_message(interaction, embed=embed, view=view)


class CustomizeSetupMenuView(BackToSetupView):
    @discord.ui.button(label="Core Roles + Ticket Category", emoji="1️⃣", style=discord.ButtonStyle.primary, custom_id="stoney_setup:custom_core", row=0)
    async def core(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_modal(interaction, "core")

    @discord.ui.button(label="Tickets + Verification Rooms", emoji="2️⃣", style=discord.ButtonStyle.primary, custom_id="stoney_setup:custom_ticket_verify", row=1)
    async def ticket_verify(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_modal(interaction, "ticket_verify")

    @discord.ui.button(label="Logs + Status Channels", emoji="3️⃣", style=discord.ButtonStyle.primary, custom_id="stoney_setup:custom_logs", row=2)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_modal(interaction, "logs_status")

    async def _open_modal(self, interaction: discord.Interaction, page_key: str) -> None:
        if not await _require_setup_permission(interaction):
            return
        from . import public_setup_assistant

        _install_cleaners(public_setup_assistant)
        page = next((item for item in _CUSTOMIZE_PAGES if item[0] == page_key), None)
        if page is None:
            return await interaction.response.send_message("❌ Unknown customization page.", ephemeral=True)
        _key, label, keys = page
        specs = _spec_subset(public_setup_assistant, keys)
        if not specs:
            return await interaction.response.send_message("❌ No customizable fields were found for this page.", ephemeral=True)
        modal = public_setup_assistant.CustomMissingNamesModal(specs)
        modal.title = label[:45]
        await interaction.response.send_modal(modal)


class ChooseExistingMenuView(BackToSetupView):
    @discord.ui.button(label="Ticket Basics", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="stoney_setup:existing_ticket", row=0)
    async def ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="🎫 Choose Existing Ticket Items", description="Pick your existing ticket items. Each dropdown saves immediately.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=TicketBasicsPickerView())

    @discord.ui.button(label="Verification Roles", emoji="✅", style=discord.ButtonStyle.primary, custom_id="stoney_setup:existing_verify_roles", row=1)
    async def verify_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="✅ Choose Existing Verification Roles", description="Pick your existing verification/member roles. Each dropdown saves immediately.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=VerificationRolesPickerView())

    @discord.ui.button(label="Verification Channels", emoji="🎙️", style=discord.ButtonStyle.primary, custom_id="stoney_setup:existing_verify_channels", row=2)
    async def verify_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="🎙️ Choose Existing Verification Channels", description="Pick your existing verify/support/VC channels. Each dropdown saves immediately.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=VerificationChannelsPickerView())

    @discord.ui.button(label="Logs + Status", emoji="🧾", style=discord.ButtonStyle.primary, custom_id="stoney_setup:existing_logs", row=3)
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(title="🧾 Choose Existing Logs + Status", description="Pick your existing log/status channels. Each dropdown saves immediately.", color=discord.Color.blurple())
        await interaction.response.edit_message(embed=embed, view=LogsStatusPickerView())


class SaveRoleSelect(discord.ui.RoleSelect):
    def __init__(self, *, placeholder: str, columns: tuple[str, ...], also_same: tuple[str, ...] = (), row: int = 0) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, row=row)
        self.columns = columns
        self.also_same = also_same

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        role = self.values[0]
        payload = {column: _snowflake(role) for column in self.columns + self.also_same}
        await _save_config(interaction, payload)
        await interaction.response.send_message(embed=discord.Embed(title="✅ Saved Setup Role", description=f"Saved {_mention(role)}.", color=discord.Color.green()), ephemeral=True)


class SaveChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, placeholder: str, columns: tuple[str, ...], channel_types: list[discord.ChannelType], also_same: tuple[str, ...] = (), row: int = 0) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, channel_types=channel_types, row=row)
        self.columns = columns
        self.also_same = also_same

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        channel = self.values[0]
        payload = {column: _snowflake(channel) for column in self.columns + self.also_same}
        await _save_config(interaction, payload)
        await interaction.response.send_message(embed=discord.Embed(title="✅ Saved Setup Channel", description=f"Saved {_mention(channel)}.", color=discord.Color.green()), ephemeral=True)


class TicketBasicsPickerView(BackToSetupView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Open ticket category", columns=("ticket_category_id",), channel_types=[discord.ChannelType.category], row=0))
        self.add_item(SaveChannelSelect(placeholder="Archive/closed ticket category", columns=("ticket_archive_category_id",), channel_types=[discord.ChannelType.category], row=1))
        self.add_item(SaveRoleSelect(placeholder="Ticket staff role", columns=("staff_role_id",), also_same=("vc_staff_role_id",), row=2))
        self.add_item(SaveChannelSelect(placeholder="Transcript text channel", columns=("transcripts_channel_id",), channel_types=[discord.ChannelType.text], row=3))


class VerificationRolesPickerView(BackToSetupView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveRoleSelect(placeholder="Unverified role", columns=("unverified_role_id",), row=0))
        self.add_item(SaveRoleSelect(placeholder="Verified role", columns=("verified_role_id",), row=1))
        self.add_item(SaveRoleSelect(placeholder="Member/Resident role", columns=("resident_role_id",), row=2))


class VerificationChannelsPickerView(BackToSetupView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Verify text channel", columns=("verify_channel_id",), channel_types=[discord.ChannelType.text], row=0))
        self.add_item(SaveChannelSelect(placeholder="Support/ticket panel channel", columns=("ticket_panel_channel_id",), also_same=("support_channel_id",), channel_types=[discord.ChannelType.text], row=1))
        self.add_item(SaveChannelSelect(placeholder="VC verification voice channel", columns=("vc_verify_channel_id",), channel_types=[discord.ChannelType.voice], row=2))
        self.add_item(SaveChannelSelect(placeholder="VC queue/status text channel", columns=("vc_verify_queue_channel_id",), channel_types=[discord.ChannelType.text], row=3))


class LogsStatusPickerView(BackToSetupView):
    def __init__(self) -> None:
        super().__init__()
        self.add_item(SaveChannelSelect(placeholder="Modlog channel", columns=("modlog_channel_id",), also_same=("raidlog_channel_id", "force_verify_log_channel_id"), channel_types=[discord.ChannelType.text], row=0))
        self.add_item(SaveChannelSelect(placeholder="Join/leave log channel", columns=("join_log_channel_id",), channel_types=[discord.ChannelType.text], row=1))
        self.add_item(SaveChannelSelect(placeholder="Bot status channel", columns=("status_channel_id",), also_same=("bot_status_channel_id",), channel_types=[discord.ChannelType.text], row=2))


class CategoryManagerView(BackToSetupView):
    def __init__(self, *, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self.rows = rows
        if not rows:
            try:
                self.edit_category.disabled = True
                self.set_default.disabled = True
                self.delete_category.disabled = True
            except Exception:
                pass

    @discord.ui.button(label="Add Category", emoji="➕", style=discord.ButtonStyle.success, custom_id="stoney_setup:category_add", row=0)
    async def add_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(AddTicketCategoryModal(existing_count=len(self.rows)))

    @discord.ui.button(label="Edit Category", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="stoney_setup:category_edit", row=0)
    async def edit_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="edit")

    @discord.ui.button(label="Set Default", emoji="⭐", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:category_default", row=1)
    async def set_default(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="default")

    @discord.ui.button(label="Delete Category", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="stoney_setup:category_delete", row=1)
    async def delete_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._open_select(interaction, action="delete")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:category_refresh", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_setup_message(interaction, embed=embed, view=view)

    async def _open_select(self, interaction: discord.Interaction, *, action: str) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        rows = await _category_rows(guild)
        if not rows:
            embed, view = await _build_category_manager_payload(guild)
            embed.add_field(name="No Categories", value="Create a category first.", inline=False)
            return await _edit_setup_message(interaction, embed=embed, view=view)
        label = {"edit": "Edit a category", "default": "Choose the default category", "delete": "Delete a category"}.get(action, "Choose a category")
        embed = discord.Embed(
            title=f"🗂️ {label}",
            description="Pick a category from the dropdown below.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Current Categories", value=_category_list_text(rows), inline=False)
        await _edit_setup_message(interaction, embed=embed, view=CategorySelectActionView(rows=rows, action=action))


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

        if self.action == "edit":
            row = next((x for x in self.rows if str(x.get("slug")) == slug), None)
            if not row:
                return await interaction.response.send_message("❌ That category no longer exists. Refresh and try again.", ephemeral=True)
            return await interaction.response.send_modal(EditTicketCategoryModal(row=row))

        if self.action == "default":
            await _safe_defer_update(interaction)
            from . import ticket_category_admin as category_admin

            ok = await category_admin._set_default(guild.id, slug)
            embed, view = await _build_category_manager_payload(guild, title="⭐ Default Ticket Category Updated" if ok else "🚫 Default Update Failed")
            embed.add_field(name="Result", value=(f"`{slug}` is now the default category." if ok else f"Could not set `{slug}` as default."), inline=False)
            embed.color = discord.Color.green() if ok else discord.Color.red()
            return await _edit_setup_message(interaction, embed=embed, view=view)

        if self.action == "delete":
            row = next((x for x in self.rows if str(x.get("slug")) == slug), None)
            embed = discord.Embed(
                title="🗑️ Confirm Category Delete",
                description=(
                    f"You are about to delete `{slug}`.\n\n"
                    "This does **not** delete old ticket channels. It only removes this routing/intake category."
                ),
                color=discord.Color.red(),
            )
            if row:
                embed.add_field(name="Selected", value=_category_line(row), inline=False)
            return await interaction.response.edit_message(embed=embed, view=ConfirmDeleteCategoryView(slug=slug))


class ConfirmDeleteCategoryView(BackToSetupView):
    def __init__(self, *, slug: str) -> None:
        super().__init__()
        self.slug = slug

    @discord.ui.button(label="Yes, Delete Category", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="stoney_setup:category_delete_confirm", row=0)
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
        await _edit_setup_message(interaction, embed=embed, view=view)

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:category_delete_cancel", row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await _safe_defer_update(interaction)
        embed, view = await _build_category_manager_payload(guild)
        await _edit_setup_message(interaction, embed=embed, view=view)


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
        if not name:
            return await _send_category_result(interaction, guild=guild, title="🚫 Category Not Created", message="Category name cannot be empty.", ok=False)
        if not slug:
            return await _send_category_result(interaction, guild=guild, title="🚫 Category Not Created", message="Category slug is invalid.", ok=False)
        if intake_type not in category_admin._ALLOWED_INTAKE_TYPES:
            return await _send_category_result(interaction, guild=guild, title="🚫 Category Not Created", message=f"Invalid type. Use one of: {category_admin._human_intake_types()}", ok=False)
        if await category_admin._fetch_category_by_slug(guild.id, slug):
            return await _send_category_result(interaction, guild=guild, title="🚫 Category Not Created", message=f"Category `{slug}` already exists.", ok=False)

        rows = await category_admin._fetch_categories(guild.id)
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
        await _send_category_result(interaction, guild=guild, title="✅ Category Created" if ok else "🚫 Category Not Created", message=(f"Created `{slug}`." if ok else "Database insert failed."), ok=ok)


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
            return await _send_category_result(interaction, guild=guild, title="🚫 Category Not Updated", message=f"Invalid type. Use one of: {category_admin._human_intake_types()}", ok=False)
        patch["intake_type"] = intake_type
        patch["match_keywords"] = category_admin._normalize_keywords(str(self.keywords_input.value or ""))
        patch["description"] = category_admin._normalize_description(str(self.description_input.value or ""))
        sort_raw = str(self.sort_input.value or "").strip()
        if sort_raw:
            try:
                sort_value: Optional[int] = int(sort_raw)
            except Exception:
                return await _send_category_result(interaction, guild=guild, title="🚫 Category Not Updated", message="Sort order must be a number.", ok=False)
            sort_clean = category_admin._validated_sort_order(sort_value)
            if sort_clean is None:
                return await _send_category_result(interaction, guild=guild, title="🚫 Category Not Updated", message=f"Sort order must be between `{category_admin._MIN_SORT_ORDER}` and `{category_admin._MAX_SORT_ORDER}`.", ok=False)
            patch["sort_order"] = sort_clean
        ok = await category_admin._update_category(guild.id, slug, patch)
        await _send_category_result(interaction, guild=guild, title="✅ Category Updated" if ok else "🚫 Category Not Updated", message=(f"Updated `{slug}`." if ok else "Database update failed."), ok=ok)


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
        await interaction.followup.send(f"❌ Setup failed: `{repr(e)[:300]}`", ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return
    try:
        existing = stoney_group.get_command("setup")
    except Exception:
        existing = None
    if existing is None:
        stoney_group.add_command(discord.app_commands.Command(name="setup", description="Start the guided Dank Shield setup flow.", callback=_setup_callback))
    _ATTACHED = True


_attach()


def register_public_setup_start_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    print("✅ public_setup_start: attached /dank setup quick-start command")


__all__ = ["register_public_setup_start_commands"]
