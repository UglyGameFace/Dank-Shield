from __future__ import annotations

"""Owner-facing setup UI for understandable, selective configuration recovery."""

from math import ceil
from typing import Any, Iterable, Mapping, Optional

import discord

from .config_history import (
    TICKET_CATEGORIES_TABLE,
    get_config_version,
    list_config_versions,
)
from .config_history_selective import (
    CORE_DOMAIN,
    RESTORE_ALL,
    RESTORE_MISSING,
    RESTORE_SELECTED,
    TICKET_CHOICES_DOMAIN,
    create_scoped_manual_backup,
    plan_selective_restore,
    restore_config_version_selective,
)
from .discord_time import discord_timestamp_pair

_PICKER_PAGE_SIZE = 20


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


def _format_timestamp(value: Any) -> str:
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
        return f"Restore result • {domain}"
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
    return discord.SelectOption(
        label=f"#{version_id} • {_version_domain(row)}"[:100],
        value=str(version_id),
        description=_version_source(row)[:100],
        emoji="💾" if bool(row.get("is_manual")) else "🕘",
    )


def _item_labels(plan: Mapping[str, Any]) -> dict[str, str]:
    raw = plan.get("item_labels")
    if not isinstance(raw, Mapping):
        return {}
    return {
        _safe_str(key).lower(): _safe_str(value, _safe_str(key))
        for key, value in raw.items()
        if _safe_str(key)
    }


def _changed_items(plan: Mapping[str, Any]) -> list[str]:
    return [
        _safe_str(item).lower()
        for item in plan.get("changed_items", [])
        if _safe_str(item)
    ]


def _missing_items(plan: Mapping[str, Any]) -> list[str]:
    return [
        _safe_str(item).lower()
        for item in plan.get("missing_items", [])
        if _safe_str(item)
    ]


def _display_item(plan: Mapping[str, Any], item: str) -> str:
    labels = _item_labels(plan)
    return labels.get(_safe_str(item).lower(), _safe_str(item).replace("_", " ").title())


def _display_items(
    plan: Mapping[str, Any],
    items: Iterable[str],
    *,
    limit: int = 12,
) -> str:
    clean = [_safe_str(item).lower() for item in items if _safe_str(item)]
    if not clean:
        return "None"
    shown = [f"• **{_display_item(plan, item)}**" for item in clean[:limit]]
    if len(clean) > limit:
        shown.append(f"• …and **{len(clean) - limit} more**")
    return "\n".join(shown)[:1024]


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


async def _back_to_all_features(interaction: discord.Interaction) -> None:
    from .commands_ext import public_setup_recommend as recommend

    await recommend._open_advanced_settings(interaction)


async def _back_home(interaction: discord.Interaction) -> None:
    from .commands_ext import public_setup_recommend as recommend

    await recommend._home_edit(interaction)


async def _close_setup(interaction: discord.Interaction) -> None:
    from .commands_ext import public_setup_recommend as recommend
    await recommend._close_setup(interaction)


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
            "A backup saves **Dank Shield's configuration**, not a copy of your Discord server. "
            "Use it to recover settings after an accidental change, restore a missing setup item, "
            "or bring back selected ticket choices without replacing newer settings you want to keep."
        ),
        color=discord.Color.red() if error else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="⚙️ Core Settings",
        value=(
            "Feature switches, timers and rules, protection settings, setup choices, welcome/log settings, "
            "and the saved IDs that tell Dank Shield which roles, channels, and categories to use."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎫 Ticket Choices",
        value=(
            "The choices members see when opening tickets, including category names, descriptions, ordering, "
            "and category-specific form questions saved with those choices."
        ),
        inline=False,
    )
    embed.add_field(
        name="🚫 What Is Not Backed Up",
        value=(
            "Discord messages, members, live ticket conversations, actual roles/channels/categories, files, and server ownership. "
            "A restore changes Dank Shield's saved configuration only; it does not clone, delete, or rebuild your Discord server."
        ),
        inline=False,
    )

    if saved_message:
        embed.add_field(name="✅ Last Action", value=saved_message[:1024], inline=False)
    if error:
        embed.add_field(name="History Unavailable", value=error[:1024], inline=False)
    elif versions:
        lines: list[str] = []
        for row in versions[:8]:
            version_id = _safe_int(row.get("version_id"), 0)
            if version_id <= 0:
                continue
            lines.append(
                f"**#{version_id} • {_version_domain(row)}**\n"
                f"{_version_source(row)}\n"
                f"{_format_timestamp(row.get('created_at'))}"
            )
        embed.add_field(
            name="Recent Saved Versions",
            value="\n\n".join(lines)[:1024] or "No saved versions yet.",
            inline=False,
        )
        embed.add_field(
            name="Restore Options",
            value=(
                "Open a version to choose **Missing Only**, **Exact Changes**, or **All Differences**. "
                "Nothing is restored until a separate preview and confirmation screen. "
                "The current configuration is always saved first as a safety backup."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="No Versions Yet",
            value=(
                "Press **Choose Backup Contents** and select Core Settings, Ticket Choices, or both. "
                "Automatic versions will also appear after future saved changes."
            ),
            inline=False,
        )

    embed.set_footer(
        text=f"Guild {guild.id} • newest 50 versions retained per configuration domain"
    )
    return embed


def _backup_contents_embed(selected_domains: Iterable[str]) -> discord.Embed:
    selected = set(selected_domains)
    embed = discord.Embed(
        title="💾 Choose What to Back Up",
        description=(
            "Select one or both configuration areas. Creating a backup does not change any setting; "
            "it only saves the current values so they can be reviewed or restored later."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Core Settings",
        value=(
            ("✅ Selected\n" if CORE_DOMAIN in selected else "⬜ Not selected\n")
            + "Features, timers, protection rules, welcome/log settings, setup choices, and saved Discord role/channel/category references."
        ),
        inline=False,
    )
    embed.add_field(
        name="Ticket Choices",
        value=(
            ("✅ Selected\n" if TICKET_CHOICES_DOMAIN in selected else "⬜ Not selected\n")
            + "Member-facing ticket categories and their category-specific form configuration."
        ),
        inline=False,
    )
    embed.add_field(
        name="Why Choose Separately?",
        value=(
            "A Core-only backup is useful before changing protection, roles, channels, or timers. "
            "A Ticket-Choices-only backup is useful before editing the ticket menu or forms."
        ),
        inline=False,
    )
    return embed


def _version_contents_text(plan: Mapping[str, Any]) -> str:
    domain = _safe_str(plan.get("domain"))
    saved_count = _safe_int(plan.get("saved_count"), 0)
    if domain == TICKET_CHOICES_DOMAIN:
        return (
            f"This version contains **{saved_count} saved ticket choice(s)**, including compatible category-stored form configuration."
        )

    sections = plan.get("core_sections")
    if not isinstance(sections, Mapping) or not sections:
        return f"This version contains **{saved_count} saved Core Setting(s)**."
    lines = [
        f"• **{_safe_str(section)}:** {len(list(keys or []))} setting(s)"
        for section, keys in sections.items()
    ]
    return (
        f"This version contains **{saved_count} saved Core Setting(s)** across:\n"
        + "\n".join(lines[:8])
    )[:1024]


def _version_detail_embed(
    version: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> discord.Embed:
    version_id = _safe_int(version.get("version_id"), 0)
    domain = _safe_str(plan.get("domain_label"), _version_domain(version))
    changed = _changed_items(plan)
    missing = _missing_items(plan)
    embed = discord.Embed(
        title=f"🕘 {domain} Version #{version_id}",
        description=(
            "Review what this saved version contains and choose how much to restore. "
            "A selective restore leaves every unselected newer setting or ticket choice untouched."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="What This Backup Contains",
        value=_version_contents_text(plan),
        inline=False,
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
    embed.add_field(
        name="Different From Current Configuration",
        value=(
            f"**{len(changed)} item(s)** are different.\n"
            f"**{len(missing)} item(s)** exist in this backup but are currently missing or blank.\n\n"
            + _display_items(plan, changed, limit=8)
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Choose the Safe Restore Mode",
        value=(
            "**Missing Only** — add back items that are absent or blank now; existing configured values stay untouched.\n"
            "**Choose Exact Changes** — manually pick individual settings or ticket choices.\n"
            "**All Differences** — make every currently different item match this saved version."
        ),
        inline=False,
    )
    embed.add_field(
        name="Restore Safety",
        value=(
            "The current configuration is backed up first. Restoring does **not** delete, recreate, or rename Discord roles or channels. "
            "Only Dank Shield's selected saved settings or ticket choices are changed."
        ),
        inline=False,
    )
    return embed


def _picker_page_count(plan: Mapping[str, Any]) -> int:
    return max(1, ceil(len(_changed_items(plan)) / _PICKER_PAGE_SIZE))


def _picker_bounds(plan: Mapping[str, Any], page: int) -> tuple[int, int, int]:
    pages = _picker_page_count(plan)
    safe_page = max(0, min(int(page), pages - 1))
    start = safe_page * _PICKER_PAGE_SIZE
    end = min(start + _PICKER_PAGE_SIZE, len(_changed_items(plan)))
    return safe_page, start, end


def _selective_picker_embed(
    plan: Mapping[str, Any],
    selected_items: Iterable[str],
    *,
    page: int,
) -> discord.Embed:
    selected = sorted({_safe_str(item).lower() for item in selected_items if _safe_str(item)})
    safe_page, start, end = _picker_bounds(plan, page)
    pages = _picker_page_count(plan)
    visible = _changed_items(plan)[start:end]
    embed = discord.Embed(
        title="🎯 Choose Exact Changes",
        description=(
            "Select only the settings or ticket choices you want from this saved version. "
            "Selections on other pages stay selected until you review them."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name=f"Page {safe_page + 1}/{pages}",
        value=_display_items(plan, visible, limit=_PICKER_PAGE_SIZE),
        inline=False,
    )
    embed.add_field(
        name=f"Selected — {len(selected)}",
        value=_display_items(plan, selected, limit=10),
        inline=False,
    )
    embed.add_field(
        name="Next",
        value="Press **Review Selected** to see the final preview. Nothing changes on this screen.",
        inline=False,
    )
    return embed


def _restore_mode_label(mode: str) -> str:
    return {
        RESTORE_MISSING: "Missing Only",
        RESTORE_SELECTED: "Exact Selected Changes",
        RESTORE_ALL: "All Differences",
    }.get(_safe_str(mode).lower(), "Selected Restore")


def _confirmation_embed(
    version: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    mode: str,
    selected_items: Iterable[str],
) -> discord.Embed:
    restore_mode = _safe_str(mode, RESTORE_ALL).lower()
    if restore_mode == RESTORE_ALL:
        items = _changed_items(plan)
    elif restore_mode == RESTORE_MISSING:
        items = _missing_items(plan)
    else:
        items = sorted({_safe_str(item).lower() for item in selected_items if _safe_str(item)})

    domain = _safe_str(plan.get("domain_label"), _version_domain(version))
    version_id = _safe_int(version.get("version_id"), 0)
    embed = discord.Embed(
        title=f"⚠️ Confirm {_restore_mode_label(restore_mode)}",
        description=(
            f"Restore **{len(items)} {domain} item(s)** from saved version **#{version_id}**?\n\n"
            "Dank Shield will first save the current configuration as a safety backup. "
            "Then it will change only the items listed below."
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Will Change",
        value=_display_items(plan, items, limit=15),
        inline=False,
    )
    embed.add_field(
        name="Will Stay Untouched",
        value=(
            "Every unlisted current setting or ticket choice, plus all Discord messages, members, roles, channels, and live ticket conversations."
        ),
        inline=False,
    )
    embed.add_field(
        name="Final Confirmation",
        value="Press **Confirm Restore** to continue, or **Cancel** to return without changing anything.",
        inline=False,
    )
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
        if version_id > 0:
            await open_config_version_detail(interaction, version_id)


class ConfigHistoryView(discord.ui.View):
    def __init__(self, versions: list[dict[str, Any]], *, unavailable: bool = False) -> None:
        super().__init__(timeout=900)
        if versions:
            self.add_item(ConfigHistorySelect(versions))
        self.choose_backup.disabled = bool(unavailable)
        self.refresh.disabled = bool(unavailable)

    @discord.ui.button(
        label="Choose Backup Contents",
        emoji="💾",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_config_history:choose_backup",
        row=1,
    )
    async def choose_backup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_backup_contents(interaction)

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
        label="Back to All Features",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:back",
        row=2,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_to_all_features(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:home",
        row=2,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)


    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:close",
        row=2,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class BackupDomainSelect(discord.ui.Select):
    def __init__(self, selected_domains: Iterable[str]) -> None:
        selected = set(selected_domains)
        super().__init__(
            placeholder="Choose one or both backup areas…",
            min_values=1,
            max_values=2,
            options=[
                discord.SelectOption(
                    label="Core Settings",
                    value=CORE_DOMAIN,
                    description="Features, rules, protection, setup, and saved Discord references",
                    emoji="⚙️",
                    default=CORE_DOMAIN in selected,
                ),
                discord.SelectOption(
                    label="Ticket Choices",
                    value=TICKET_CHOICES_DOMAIN,
                    description="Ticket menu categories and their saved form configuration",
                    emoji="🎫",
                    default=TICKET_CHOICES_DOMAIN in selected,
                ),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BackupContentsView):
            return
        view.selected_domains = set(self.values)
        view.rebuild_select()
        await interaction.response.edit_message(
            embed=_backup_contents_embed(view.selected_domains),
            view=view,
        )


class BackupContentsView(discord.ui.View):
    def __init__(self, selected_domains: Optional[Iterable[str]] = None) -> None:
        super().__init__(timeout=600)
        self.selected_domains = set(
            selected_domains or (CORE_DOMAIN, TICKET_CHOICES_DOMAIN)
        )
        self.add_item(BackupDomainSelect(self.selected_domains))

    def rebuild_select(self) -> None:
        for child in list(self.children):
            if isinstance(child, BackupDomainSelect):
                self.remove_item(child)
        self.add_item(BackupDomainSelect(self.selected_domains))

    @discord.ui.button(
        label="Create Selected Backup",
        emoji="💾",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_config_history:create_selected_backup",
        row=1,
    )
    async def create_selected_backup(
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
                "❌ This must be used inside a server.", ephemeral=True
            )
        await _safe_defer_update(interaction)
        try:
            backup = await create_scoped_manual_backup(
                int(guild.id),
                domains=sorted(self.selected_domains),
                actor_id=int(interaction.user.id),
                reason="Manual selected backup from /dank setup",
            )
            parts: list[str] = []
            for row in backup.get("backup_versions", []) or []:
                if not isinstance(row, Mapping):
                    continue
                version_id = _safe_int(row.get("version_id"), 0)
                if version_id > 0:
                    parts.append(f"**{_version_domain(row)} #{version_id}**")
            message = (
                "Created selected backup: " + " • ".join(parts) + "."
                if parts
                else "Created the selected configuration backup."
            )
            await open_config_history(
                interaction,
                saved_message=message,
                already_deferred=True,
            )
        except Exception as exc:
            await open_config_history(
                interaction,
                saved_message=f"Backup failed safely: `{type(exc).__name__}: {str(exc)[:240]}`",
                already_deferred=True,
            )

    @discord.ui.button(
        label="Back to History",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:backup_back",
        row=1,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_config_history(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:backup_home",
        row=2,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)


    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:backup_close",
        row=2,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class ConfigVersionDetailView(discord.ui.View):
    def __init__(self, version_id: int, plan: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(timeout=900)
        self.version_id = int(version_id)
        self.plan = dict(plan or {})
        self.restore_missing.disabled = not bool(_missing_items(self.plan))
        self.choose_changes.disabled = not bool(_changed_items(self.plan))
        self.restore_all.disabled = not bool(_changed_items(self.plan))

    @discord.ui.button(
        label="Restore Missing Only",
        emoji="➕",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_config_history:restore_missing",
        row=0,
    )
    async def restore_missing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_restore_confirmation(
            interaction,
            self.version_id,
            mode=RESTORE_MISSING,
            plan=self.plan,
        )

    @discord.ui.button(
        label="Choose Exact Changes",
        emoji="🎯",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_config_history:restore_choose",
        row=0,
    )
    async def choose_changes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_selective_restore_picker(
            interaction,
            self.version_id,
            plan=self.plan,
        )

    @discord.ui.button(
        label="Restore All Differences",
        emoji="↩️",
        style=discord.ButtonStyle.danger,
        custom_id="dank_setup_config_history:restore_all",
        row=1,
    )
    async def restore_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_restore_confirmation(
            interaction,
            self.version_id,
            mode=RESTORE_ALL,
            plan=self.plan,
        )

    @discord.ui.button(
        label="Back to History",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_back",
        row=2,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_config_history(interaction)

    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_home",
        row=3,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:detail_close",
        row=3,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class RestoreItemSelect(discord.ui.Select):
    def __init__(
        self,
        plan: Mapping[str, Any],
        selected_items: Iterable[str],
        *,
        page: int,
    ) -> None:
        selected = {_safe_str(item).lower() for item in selected_items if _safe_str(item)}
        safe_page, start, end = _picker_bounds(plan, page)
        visible = _changed_items(plan)[start:end]
        options = [
            discord.SelectOption(
                label=_display_item(plan, item)[:100],
                value=item,
                description=(
                    "Currently missing or blank"
                    if item in set(_missing_items(plan))
                    else "Different from the current value"
                )[:100],
                emoji="➕" if item in set(_missing_items(plan)) else "🔄",
                default=item in selected,
            )
            for item in visible
        ]
        super().__init__(
            placeholder=f"Choose changes on page {safe_page + 1}…",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
            row=0,
        )
        self.visible_items = set(visible)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SelectiveRestorePickerView):
            return
        view.selected_items.difference_update(self.visible_items)
        view.selected_items.update(_safe_str(value).lower() for value in self.values)
        view.rebuild_select()
        await interaction.response.edit_message(
            embed=_selective_picker_embed(
                view.plan,
                view.selected_items,
                page=view.page,
            ),
            view=view,
        )


class SelectiveRestorePickerView(discord.ui.View):
    def __init__(
        self,
        version_id: int,
        plan: Mapping[str, Any],
        *,
        selected_items: Optional[Iterable[str]] = None,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=900)
        self.version_id = int(version_id)
        self.plan = dict(plan)
        self.selected_items = {
            _safe_str(item).lower() for item in selected_items or () if _safe_str(item)
        }
        self.page, _start, _end = _picker_bounds(self.plan, page)
        self.add_item(
            RestoreItemSelect(
                self.plan,
                self.selected_items,
                page=self.page,
            )
        )
        self.previous.disabled = self.page <= 0
        self.next_page.disabled = self.page >= _picker_page_count(self.plan) - 1

    def rebuild_select(self) -> None:
        for child in list(self.children):
            if isinstance(child, RestoreItemSelect):
                self.remove_item(child)
        self.add_item(
            RestoreItemSelect(
                self.plan,
                self.selected_items,
                page=self.page,
            )
        )
        self.previous.disabled = self.page <= 0
        self.next_page.disabled = self.page >= _picker_page_count(self.plan) - 1

    async def _change_page(self, interaction: discord.Interaction, delta: int) -> None:
        self.page, _start, _end = _picker_bounds(self.plan, self.page + int(delta))
        self.rebuild_select()
        await interaction.response.edit_message(
            embed=_selective_picker_embed(
                self.plan,
                self.selected_items,
                page=self.page,
            ),
            view=self,
        )

    @discord.ui.button(
        label="Previous",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:picker_previous",
        row=1,
    )
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._change_page(interaction, -1)

    @discord.ui.button(
        label="Next",
        emoji="➡️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:picker_next",
        row=1,
    )
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._change_page(interaction, 1)

    @discord.ui.button(
        label="Review Selected",
        emoji="👀",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_config_history:picker_review",
        row=2,
    )
    async def review_selected(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        if not self.selected_items:
            return await interaction.response.send_message(
                "Choose at least one setting or ticket choice first.",
                ephemeral=True,
            )
        await open_restore_confirmation(
            interaction,
            self.version_id,
            mode=RESTORE_SELECTED,
            selected_items=sorted(self.selected_items),
            plan=self.plan,
        )

    @discord.ui.button(
        label="Back to Version",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:picker_back",
        row=2,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_config_version_detail(interaction, self.version_id)


    @discord.ui.button(
        label="Setup Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:picker_home",
        row=3,
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_home(interaction)

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_config_history:picker_close",
        row=3,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _close_setup(interaction)


class RestoreConfigConfirmView(discord.ui.View):
    def __init__(
        self,
        version_id: int,
        *,
        mode: str = RESTORE_ALL,
        selected_items: Optional[Iterable[str]] = None,
        plan: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(timeout=300)
        self.version_id = int(version_id)
        self.mode = _safe_str(mode, RESTORE_ALL).lower()
        self.selected_items = sorted(
            {_safe_str(item).lower() for item in selected_items or () if _safe_str(item)}
        )
        self.plan = dict(plan or {})

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
            result = await restore_config_version_selective(
                int(guild.id),
                self.version_id,
                mode=self.mode,
                selected_items=self.selected_items,
                actor_id=int(interaction.user.id),
                reason=(
                    f"Owner confirmed {_restore_mode_label(self.mode)} restore "
                    f"of config version {self.version_id}"
                ),
            )
            restored_id = _safe_int(
                result.get("restored_from_version_id"), self.version_id
            )
            restored_domain = (
                "Ticket Choices"
                if _safe_str(result.get("config_table")) == TICKET_CATEGORIES_TABLE
                else "Core Settings"
            )
            restored_count = _safe_int(result.get("restored_item_count"), 0)
            await open_config_history(
                interaction,
                saved_message=(
                    f"Restored **{restored_count} {restored_domain} item(s)** from version **#{restored_id}** using **{_restore_mode_label(self.mode)}**. "
                    "The configuration active immediately before restore was saved as a safety backup."
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


async def open_backup_contents(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await _safe_defer_update(interaction)
    selected = {CORE_DOMAIN, TICKET_CHOICES_DOMAIN}
    await _edit(
        interaction,
        embed=_backup_contents_embed(selected),
        view=BackupContentsView(selected),
    )


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
        plan = await plan_selective_restore(int(guild.id), int(version_id))
    except Exception as exc:
        return await open_config_history(
            interaction,
            saved_message=f"Could not open that version: `{type(exc).__name__}: {str(exc)[:240]}`",
            already_deferred=True,
        )

    await _edit(
        interaction,
        embed=_version_detail_embed(version, plan),
        view=ConfigVersionDetailView(int(version_id), plan),
    )


async def open_selective_restore_picker(
    interaction: discord.Interaction,
    version_id: int,
    *,
    plan: Optional[Mapping[str, Any]] = None,
    selected_items: Optional[Iterable[str]] = None,
    page: int = 0,
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
        restore_plan = dict(plan or await plan_selective_restore(int(guild.id), int(version_id)))
    except Exception as exc:
        return await open_config_history(
            interaction,
            saved_message=f"Could not prepare selective restore: `{type(exc).__name__}: {str(exc)[:240]}`",
            already_deferred=True,
        )

    view = SelectiveRestorePickerView(
        int(version_id),
        restore_plan,
        selected_items=selected_items,
        page=page,
    )
    await _edit(
        interaction,
        embed=_selective_picker_embed(
            restore_plan,
            view.selected_items,
            page=view.page,
        ),
        view=view,
    )


async def open_restore_confirmation(
    interaction: discord.Interaction,
    version_id: int,
    *,
    mode: str = RESTORE_ALL,
    selected_items: Optional[Iterable[str]] = None,
    plan: Optional[Mapping[str, Any]] = None,
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
        restore_plan = dict(plan or await plan_selective_restore(int(guild.id), int(version_id)))
    except Exception as exc:
        return await open_config_history(
            interaction,
            saved_message=f"Could not confirm that version: `{type(exc).__name__}: {str(exc)[:240]}`",
            already_deferred=True,
        )

    chosen = list(selected_items or ())
    await _edit(
        interaction,
        embed=_confirmation_embed(
            version,
            restore_plan,
            mode=mode,
            selected_items=chosen,
        ),
        view=RestoreConfigConfirmView(
            int(version_id),
            mode=mode,
            selected_items=chosen,
            plan=restore_plan,
        ),
    )


__all__ = [
    "BackupContentsView",
    "ConfigHistoryView",
    "ConfigVersionDetailView",
    "RestoreConfigConfirmView",
    "SelectiveRestorePickerView",
    "open_backup_contents",
    "open_config_history",
    "open_config_version_detail",
    "open_restore_confirmation",
    "open_selective_restore_picker",
]
