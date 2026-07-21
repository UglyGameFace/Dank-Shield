from __future__ import annotations

"""Owner-facing setup UI for configuration backups and version history."""

from typing import Any, Mapping

import discord

from .config_history import (
    TICKET_CATEGORIES_TABLE,
    changed_config_keys,
    changed_ticket_category_slugs,
    create_manual_backup,
    get_config_version,
    get_current_ticket_categories,
    list_config_versions,
    restore_config_version,
)
from .discord_time import discord_timestamp_pair
from .guild_config import get_guild_config


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("snapshot")
    return dict(value) if isinstance(value, Mapping) else {}


def _format_timestamp(value: Any) -> str:
    """Render one instant in each Discord viewer's own timezone."""

    return discord_timestamp_pair(
        value,
        absolute_style="f",
        fallback="Unknown time",
    )


def _version_domain(row: Mapping[str, Any]) -> str:
    if _safe_str(row.get("config_table")) == TICKET_CATEGORIES_TABLE:
        return "Ticket Choices"
    return "Core Settings"


def _version_source(row: Mapping[str, Any]) -> str:
    domain = _version_domain(row)
    source = _safe_str(row.get("source"), "automatic_change")

    if source == "pre_restore_backup":
        return f"Pre-restore safety backup • {domain}"
    if source == "config_history_restore":
        return f"Restore • {domain}"
    if bool(row.get("is_manual")):
        return f"Manual backup • {domain}"
    if source == "ticket_categories":
        mode = _safe_str(row.get("mode"), "change").replace("_", " ").title()
        return f"Ticket Choices • {mode}"
    if source == "migration_baseline":
        return f"Initial baseline • {domain}"
    return f"{source.replace('_', ' ').strip().title()[:60]} • {domain}"


def _version_option(row: Mapping[str, Any]) -> discord.SelectOption | None:
    version_id = _safe_int(row.get("version_id"), 0)
    if version_id <= 0:
        return None
    label = f"#{version_id} • {_version_domain(row)}"[:100]
    # Discord select-option descriptions are plain text and do not reliably
    # render <t:...> markup. Show the source here; the selected version and
    # history embed show the saved time localized for each viewer.
    description = _version_source(row)[:100]
    emoji = "💾" if bool(row.get("is_manual")) else "🕘"
    return discord.SelectOption(
        label=label,
        value=str(version_id),
        description=description,
        emoji=emoji,
    )


async def _require_setup_permission(interaction: discord.Interaction) -> bool:
    from .commands_ext.public_setup_group import _require_setup_permission as require

    return await require(interaction)


async def _safe_defer_update(interaction: discord.Interaction) -> None:
    from .commands_ext import public_setup_solid as solid

    await solid._safe_defer_update(interaction)


async def _edit(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    from .commands_ext import public_setup_solid as solid

    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _back_to_other_settings(interaction: discord.Interaction) -> None:
    from .commands_ext import public_setup_recommend as recommend

    await recommend._open_advanced_settings(interaction)


async def _back_home(interaction: discord.Interaction) -> None:
    from .commands_ext import public_setup_recommend as recommend

    await recommend._home_edit(interaction)


def _history_embed(
    guild: discord.Guild,
    versions: list[dict[str, Any]],
    *,
    saved_message: str = "",
    error: str = "",
) -> discord.Embed:
    embed = discord.Embed(
        title="💾 Backups & Version History",
        description=(
            "Dank Shield keeps versions of **Core Settings** and **Ticket Choices** so you can recover from a bad configuration change. "
            "Ticket-choice backups include category-stored form questions. This does not clone or recreate your Discord server."
        ),
        color=discord.Color.red() if error else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    if saved_message:
        embed.add_field(name="✅ Done", value=saved_message[:1024], inline=False)
    if error:
        embed.add_field(name="History Unavailable", value=error[:1024], inline=False)
    elif versions:
        lines: list[str] = []
        for row in versions[:10]:
            version_id = _safe_int(row.get("version_id"), 0)
            if version_id <= 0:
                continue
            lines.append(
                f"**#{version_id} • {_version_domain(row)}**\n"
                f"{_version_source(row)}\n"
                f"{_format_timestamp(row.get('created_at'))}"
            )
        embed.add_field(
            name="Recent Versions",
            value="\n\n".join(lines)[:1024] or "No saved versions yet.",
            inline=False,
        )
        embed.add_field(
            name="How to Restore",
            value=(
                "Choose a version from the menu. You will see exactly whether it is **Core Settings** or **Ticket Choices** before restoring. "
                "Restore always requires a separate confirmation and creates a safety backup first."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="No Versions Yet",
            value=(
                "Press **Create Backup** to save the current core settings and ticket choices now. "
                "Automatic versions will also appear after future saved changes."
            ),
            inline=False,
        )
    embed.set_footer(text=f"Guild {guild.id} • newest 50 versions retained per configuration domain")
    return embed


class ConfigHistorySelect(discord.ui.Select):
    def __init__(self, versions: list[dict[str, Any]]) -> None:
        options = [
            option
            for row in versions[:25]
            if (option := _version_option(row)) is not None
        ]
        super().__init__(
            placeholder="Choose a saved version to inspect…",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        version_id = _safe_int(self.values[0] if self.values else 0, 0)
        if version_id <= 0:
            return
        await open_config_version_detail(interaction, version_id)


class ConfigHistoryView(discord.ui.View):
    def __init__(self, versions: list[dict[str, Any]], *, unavailable: bool = False) -> None:
        super().__init__(timeout=900)
        if versions:
            self.add_item(ConfigHistorySelect(versions))
        self.create_backup.disabled = bool(unavailable)
        self.refresh.disabled = bool(unavailable)

    @discord.ui.button(
        label="Create Backup",
        emoji="💾",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_config_history:create_backup",
        row=1,
    )
    async def create_backup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.", ephemeral=True
            )
        await _safe_defer_update(interaction)
        try:
            backup = await create_manual_backup(
                int(guild.id),
                actor_id=int(interaction.user.id),
                reason="Manual backup from /dank setup",
            )
            versions = [
                row
                for row in (backup.get("backup_versions") or [])
                if isinstance(row, Mapping)
            ]
            parts: list[str] = []
            for row in versions:
                version_id = _safe_int(row.get("version_id"), 0)
                if version_id > 0:
                    parts.append(f"**{_version_domain(row)} #{version_id}**")
            message = (
                "Created backup set: " + " • ".join(parts) + "."
                if parts
                else "Created a configuration backup."
            )
            await open_config_history(
                interaction,
                saved_message=message,
                already_deferred=True,
            )
        except Exception as exc:
            await open_config_history(
                interaction,
                saved_message=f"Backup failed: `{type(exc).__name__}: {str(exc)[:240]}`",
                already_deferred=True,
            )

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:refresh",
        row=1,
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_config_history(interaction)

    @discord.ui.button(
        label="Back to Other Settings",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:back",
        row=2,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_to_other_settings(interaction)

    @discord.ui.button(
        label="Back Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:home",
        row=2,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)


class ConfigVersionDetailView(discord.ui.View):
    def __init__(self, version_id: int) -> None:
        super().__init__(timeout=900)
        self.version_id = int(version_id)

    @discord.ui.button(
        label="Restore This Version",
        emoji="↩️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_config_history:restore",
        row=0,
    )
    async def restore(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_restore_confirmation(interaction, self.version_id)

    @discord.ui.button(
        label="Back to History",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_config_history(interaction)

    @discord.ui.button(
        label="Back to Other Settings",
        emoji="⚙️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_settings",
        row=1,
    )
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_to_other_settings(interaction)


class RestoreConfigConfirmView(discord.ui.View):
    def __init__(self, version_id: int) -> None:
        super().__init__(timeout=300)
        self.version_id = int(version_id)

    @discord.ui.button(
        label="Confirm Restore",
        emoji="⚠️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_config_history:confirm_restore",
        row=0,
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.", ephemeral=True
            )
        await _safe_defer_update(interaction)
        try:
            result = await restore_config_version(
                int(guild.id),
                self.version_id,
                actor_id=int(interaction.user.id),
                reason=f"Owner confirmed restore of config version {self.version_id}",
            )
            restored_id = _safe_int(
                result.get("restored_from_version_id"), self.version_id
            )
            restored_domain = (
                "Ticket Choices"
                if _safe_str(result.get("config_table")) == TICKET_CATEGORIES_TABLE
                else "Core Settings"
            )
            await open_config_history(
                interaction,
                saved_message=(
                    f"Restored **{restored_domain} version #{restored_id}**. "
                    "The configuration that was active immediately before restore was saved as a safety backup."
                ),
                already_deferred=True,
            )
        except Exception as exc:
            await open_config_history(
                interaction,
                saved_message=f"Restore failed safely: `{type(exc).__name__}: {str(exc)[:240]}`",
                already_deferred=True,
            )

    @discord.ui.button(
        label="Cancel",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:cancel_restore",
        row=0,
    )
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_config_version_detail(interaction, self.version_id)


async def open_config_history(
    interaction: discord.Interaction,
    *,
    saved_message: str = "",
    already_deferred: bool = False,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.", ephemeral=True
        )
    if not already_deferred:
        await _safe_defer_update(interaction)

    try:
        versions = await list_config_versions(int(guild.id), limit=25)
        embed = _history_embed(guild, versions, saved_message=saved_message)
        view = ConfigHistoryView(versions)
    except Exception as exc:
        versions = []
        embed = _history_embed(
            guild,
            versions,
            saved_message=saved_message,
            error=f"{type(exc).__name__}: {str(exc)[:600]}",
        )
        view = ConfigHistoryView([], unavailable=True)

    await _edit(interaction, embed=embed, view=view)


async def open_config_version_detail(
    interaction: discord.Interaction,
    version_id: int,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.", ephemeral=True
        )
    await _safe_defer_update(interaction)

    try:
        version = await get_config_version(int(guild.id), int(version_id))
        snapshot = _snapshot(version)
        is_ticket_choices = (
            _safe_str(version.get("config_table")) == TICKET_CATEGORIES_TABLE
        )
        if is_ticket_choices:
            current_rows = await get_current_ticket_categories(int(guild.id))
            changed = changed_ticket_category_slugs(snapshot, current_rows)
        else:
            current = await get_guild_config(int(guild.id), refresh=True)
            changed = changed_config_keys(snapshot, current)
    except Exception as exc:
        return await open_config_history(
            interaction,
            saved_message=f"Could not open that version: `{type(exc).__name__}: {str(exc)[:240]}`",
            already_deferred=True,
        )

    domain = _version_domain(version)
    embed = discord.Embed(
        title=f"🕘 {domain} Version #{int(version_id)}",
        description=(
            "Review this version before restoring it. Nothing changes until you press **Restore This Version** and then confirm again."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Saved",
        value=_format_timestamp(version.get("created_at")),
        inline=False,
    )
    embed.add_field(name="Source", value=_version_source(version), inline=False)
    reason = _safe_str(version.get("reason"))
    if reason:
        embed.add_field(name="Reason", value=reason[:1024], inline=False)

    if is_ticket_choices:
        embed.add_field(
            name="Different From Current Ticket Choices",
            value=(
                f"**{len(changed)} ticket choice(s)** differ.\n"
                + (
                    ", ".join(f"`{slug}`" for slug in changed[:20])
                    if changed
                    else "This version matches the current functional ticket choices."
                )
            )[:1024],
            inline=False,
        )
        safety_text = (
            "Restoring this version changes Dank Shield's saved **ticket choices and category-stored form configuration** only. "
            "It does **not** delete or recreate Discord roles/channels. Current ticket choices are backed up first."
        )
    else:
        embed.add_field(
            name="Different From Current Core Settings",
            value=(
                f"**{len(changed)} setting(s)** differ.\n"
                + (
                    ", ".join(f"`{key}`" for key in changed[:20])
                    if changed
                    else "This version matches the current functional core configuration."
                )
            )[:1024],
            inline=False,
        )
        safety_text = (
            "Restoring this version changes Dank Shield's saved **core settings and Discord ID references** only. "
            "It does **not** delete or recreate Discord roles/channels. Current core settings are backed up first."
        )

    embed.add_field(name="Restore Safety", value=safety_text, inline=False)
    await _edit(
        interaction,
        embed=embed,
        view=ConfigVersionDetailView(int(version_id)),
    )


async def open_restore_confirmation(
    interaction: discord.Interaction,
    version_id: int,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.", ephemeral=True
        )
    await _safe_defer_update(interaction)

    try:
        version = await get_config_version(int(guild.id), int(version_id))
    except Exception as exc:
        return await open_config_history(
            interaction,
            saved_message=f"Could not confirm that version: `{type(exc).__name__}: {str(exc)[:240]}`",
            already_deferred=True,
        )

    domain = _version_domain(version)
    is_ticket_choices = (
        _safe_str(version.get("config_table")) == TICKET_CATEGORIES_TABLE
    )
    what_changes = (
        "the selected saved ticket choices and category-stored form configuration"
        if is_ticket_choices
        else "the selected saved core settings and Discord ID references"
    )
    current_backup = (
        "current ticket choices"
        if is_ticket_choices
        else "current core settings"
    )

    embed = discord.Embed(
        title=f"⚠️ Confirm {domain} Restore",
        description=(
            f"Restore saved **{domain} version #{int(version_id)}**?\n\n"
            f"Dank Shield will first save the {current_backup} as a safety backup. "
            f"Then it will restore {what_changes}.\n\n"
            "**No Discord roles or channels are deleted, recreated, or renamed by this restore.**"
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="This requires confirmation",
        value="Press **Confirm Restore** to continue, or **Cancel** to go back without changing anything.",
        inline=False,
    )
    await _edit(
        interaction,
        embed=embed,
        view=RestoreConfigConfirmView(int(version_id)),
    )


__all__ = [
    "ConfigHistoryView",
    "ConfigVersionDetailView",
    "RestoreConfigConfirmView",
    "open_config_history",
    "open_config_version_detail",
    "open_restore_confirmation",
]
