from __future__ import annotations

from typing import Any, Iterable, Optional

import discord

from .common import safe_defer
from .public_setup_group import _require_setup_permission, stoney_group
from ..guild_config import get_guild_config, invalidate_guild_config


_ATTACHED = False

_REPLACEMENTS = {
    "/stoney setup-picker": "/stoney setup",
    "/stoney setup-assistant": "/stoney setup",
    "/stoney setup-defaults": "/stoney setup",
    "/stoney setup-find": "/stoney setup",
    "/stoney setup-logs": "/stoney setup",
    "/stoney setup-review": "/stoney setup",
    "/stoney setup-status": "/stoney setup",
    "/stoney setup-tickets": "/stoney setup",
    "/stoney setup-verify": "/stoney setup",
    "/stoney setup-verify-ids": "/stoney setup",
    "/stoney setup-access": "/stoney setup",
    "/stoney permission-check": "/stoney setup",
    "/stoney launch-check": "/stoney setup",
    "/stoney production-audit": "/stoney setup",
    "/stoney tickettool-check": "/stoney setup",
    "/stoney db-check": "/stoney setup",
    "`/stoney setup-picker`": "`/stoney setup`",
    "`/stoney setup-assistant`": "`/stoney setup`",
    "`/stoney setup-defaults`": "`/stoney setup`",
    "`/stoney setup-find`": "`/stoney setup`",
    "`/stoney setup-logs`": "`/stoney setup`",
    "`/stoney setup-review`": "`/stoney setup`",
    "`/stoney setup-status`": "`/stoney setup`",
    "`/stoney setup-tickets`": "`/stoney setup`",
    "`/stoney setup-verify`": "`/stoney setup`",
    "`/stoney setup-verify-ids`": "`/stoney setup`",
    "`/stoney setup-access`": "`/stoney setup`",
    "`/stoney permission-check`": "`/stoney setup`",
    "`/stoney launch-check`": "`/stoney setup`",
    "`/stoney production-audit`": "`/stoney setup`",
    "`/stoney tickettool-check`": "`/stoney setup`",
    "`/stoney db-check`": "`/stoney setup`",
}

_CUSTOMIZE_PAGES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "core",
        "Core Roles + Ticket Category",
        ("staff_role", "unverified_role", "verified_role", "resident_role", "ticket_category"),
    ),
    (
        "ticket_verify",
        "Tickets + Verification Rooms",
        ("archive_category", "verify_channel", "support_channel", "vc_verify_channel", "vc_queue_channel"),
    ),
    (
        "logs_status",
        "Logs + Status Channels",
        ("transcripts_channel", "modlog_channel", "join_log_channel", "status_channel"),
    ),
)


def _clean_text(value: Any) -> str:
    try:
        text = str(value or "")
    except Exception:
        return ""
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    text = text.replace("setup assistant", "quick setup")
    text = text.replace("Setup Assistant", "Quick Setup")
    return text


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
                    name=_clean_text(getattr(field, "name", ""))[:256] or "Status",
                    value=_clean_text(getattr(field, "value", ""))[:1024] or "—",
                    inline=bool(getattr(field, "inline", False)),
                )
        footer_text = getattr(getattr(embed, "footer", None), "text", "")
        if footer_text:
            embed.set_footer(text=_clean_text(footer_text))
    except Exception:
        pass
    return embed


def _mention(obj: Any) -> str:
    value = getattr(obj, "mention", None)
    if value:
        return str(value)
    name = getattr(obj, "name", None)
    return f"`{name or obj}`"


def _channel_id(value: Any) -> str:
    return str(int(getattr(value, "id", value)))


def _role_id(value: Any) -> str:
    return str(int(getattr(value, "id", value)))


def _specs_by_key(module: Any) -> dict[str, Any]:
    try:
        return {str(getattr(spec, "key", "")): spec for spec in getattr(module, "REPAIR_SPECS", ())}
    except Exception:
        return {}


def _spec_subset(module: Any, keys: Iterable[str]) -> list[Any]:
    specs = _specs_by_key(module)
    out: list[Any] = []
    for key in keys:
        spec = specs.get(str(key))
        if spec is not None:
            out.append(spec)
    return out


async def _current_missing_specs(guild: discord.Guild, module: Any) -> list[Any]:
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        row = await module._fetch_config_row(guild.id)
        return list(module._missing_repair_specs(guild, cfg, row))
    except Exception:
        return []


async def _has_missing_specs(guild: discord.Guild, module: Any) -> bool:
    return bool(await _current_missing_specs(guild, module))


async def _save_config(interaction: discord.Interaction, payload: dict[str, Any]) -> None:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This must be used inside a server.")
    from .public_setup_config_writer import upsert_guild_config

    payload = dict(payload)
    payload.update(
        {
            "configured_by_id": str(interaction.user.id),
            "configured_by_name": str(interaction.user),
        }
    )
    await upsert_guild_config(guild.id, payload)
    invalidate_guild_config(guild.id)


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
        "🧩 **Choose Existing Items** lets you map your own roles/channels with dropdowns.\n\n"
        f"{embed.description or ''}"
    )[:4096]
    embed = _clean_embed(embed)
    has_missing = await _has_missing_specs(guild, public_setup_assistant)
    return embed, StoneySetupView(has_missing=has_missing)


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
                return embed, StoneySetupView(has_missing=await _has_missing_specs(guild, module))

            module._build_assistant_payload = cleaned_payload

        module._STONEY_SETUP_CLEANERS_INSTALLED = True
    except Exception as e:
        try:
            print(f"⚠️ public_setup_start cleaner install failed: {repr(e)}")
        except Exception:
            pass


class BackToSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Back to Setup", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:back")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        embed, view = await _build_main_setup_payload(guild)
        await interaction.response.edit_message(embed=embed, view=view)


class StoneySetupView(discord.ui.View):
    def __init__(self, *, has_missing: bool) -> None:
        super().__init__(timeout=900)
        self.has_missing = bool(has_missing)
        if not self.has_missing:
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
                "Pick a setup section below. Each picker saves immediately and stays inside `/stoney setup`."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Sections",
            value=(
                "🎫 **Ticket Basics** — active/archive categories, staff role, transcripts\n"
                "✅ **Verification Roles** — Unverified, Verified, Member\n"
                "🎙️ **Verification Channels** — verify text, support panel, VC verify, VC queue\n"
                "🧾 **Logs + Status** — modlog, join/leave log, bot-status"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ChooseExistingMenuView())

    @discord.ui.button(label="Use This Channel for Status", emoji="📌", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:use_status_channel", row=2)
    async def use_status_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ Use this inside the text channel you want as the bot status channel.", ephemeral=True)
        await _save_config(
            interaction,
            {
                "status_channel_id": _channel_id(interaction.channel),
                "bot_status_channel_id": _channel_id(interaction.channel),
            },
        )
        embed, view = await _build_main_setup_payload(interaction.guild)  # type: ignore[arg-type]
        embed.add_field(name="Saved", value=f"Bot status channel set to {interaction.channel.mention}.", inline=False)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Run Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:health", row=2)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        embed, view = await _build_main_setup_payload(guild, title="🩺 Stoney Setup Health")
        await interaction.response.edit_message(embed=embed, view=view)


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
        value = _role_id(role)
        payload = {column: value for column in self.columns + self.also_same}
        await _save_config(interaction, payload)
        embed = discord.Embed(title="✅ Saved Setup Role", description=f"Saved {_mention(role)}.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SaveChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, placeholder: str, columns: tuple[str, ...], channel_types: list[discord.ChannelType], also_same: tuple[str, ...] = (), row: int = 0) -> None:
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, channel_types=channel_types, row=row)
        self.columns = columns
        self.also_same = also_same

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        channel = self.values[0]
        value = _channel_id(channel)
        payload = {column: value for column in self.columns + self.also_same}
        await _save_config(interaction, payload)
        embed = discord.Embed(title="✅ Saved Setup Channel", description=f"Saved {_mention(channel)}.", color=discord.Color.green())
        await interaction.response.send_message(embed=embed, ephemeral=True)


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
    if existing is not None:
        _ATTACHED = True
        return
    stoney_group.add_command(
        discord.app_commands.Command(
            name="setup",
            description="Start the guided Stoney setup flow.",
            callback=_setup_callback,
        )
    )
    _ATTACHED = True


_attach()


def register_public_setup_start_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_start: attached /stoney setup quick-start command")
    except Exception:
        pass


__all__ = ["register_public_setup_start_commands"]
