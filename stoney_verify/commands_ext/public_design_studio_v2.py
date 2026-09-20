from __future__ import annotations

"""Consolidated public Dank Design Studio.

This is the one public workflow owner for Server Design. The production front
door is ``/dank home`` → **Server Design**. The historical Studio module remains
a compatibility backend for mature exact-item editors and saved rule controls
while all public navigation, batch preview/apply, Smart Repair, and Undo are
owned here.
"""

import time
from collections.abc import Mapping
from typing import Any

import discord

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.services import server_design_apply_service as apply_service
from stoney_verify.services import server_design_plan_service as plans
from stoney_verify.services import server_design_repair_confidence as repair_confidence
from stoney_verify.services import server_design_rule_service as rule_service

studio = legacy.studio
_COMPATIBILITY_BRIDGE_INSTALLED = False


def _safe_str(value: Any, default: str = "") -> str:
    return legacy._safe_str(value, default)  # type: ignore[attr-defined]


def _safe_int(value: Any, default: int = 0) -> int:
    return legacy._safe_int(value, default)  # type: ignore[attr-defined]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


_require_design_permission = legacy._require_design_permission  # type: ignore[attr-defined]
_load_design_options = legacy._load_design_options  # type: ignore[attr-defined]


def _rule_counts(options: Mapping[str, Any]) -> dict[str, int]:
    return legacy._lock_count(options)  # type: ignore[attr-defined]


def _layout_override_count(options: Mapping[str, Any]) -> int:
    counts = _rule_counts(options)
    return (
        int(counts.get("global", 0))
        + int(counts.get("categories", 0))
        + int(counts.get("channels", 0))
        + int(counts.get("manual_names", 0))
    )


class DesignView(discord.ui.View):
    """Shared safe component error boundary for consolidated Studio screens."""

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        try:
            print(f"⚠️ Dank Design v2 component failed: {type(error).__name__}: {error}")
        except Exception:
            pass
        try:
            await legacy.safe_send_interaction(  # type: ignore[attr-defined]
                interaction,
                content=(
                    "❌ Dank Design stopped because something unexpected happened. "
                    "Reopen `/dank home`, choose **Server Design**, and build a fresh preview before trying the action again."
                ),
                ephemeral=True,
                action_name="design.v2.component_error",
            )
        except Exception:
            pass


def _home_embed(guild: discord.Guild, options: Mapping[str, Any] | None = None) -> discord.Embed:
    options = options or {}
    theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
    counts = _rule_counts(options)
    active_font = _design_server_font(options)
    embed = discord.Embed(
        title="🎨 Dank Design Studio",
        description=(
            "Pick **one job** below. The home screen never changes a Discord name.\n\n"
            "**Server-wide design, separator changes, Smart Repair, and Custom Format use:** "
            "Choose → Preview → **Apply Reviewed Changes**.\n"
            "**Only one action is immediate:** Edit One Category / Channel → **Rename**. "
            "That item screen clearly says it applies immediately."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current server design settings",
        value=(
            f"Theme: **{getattr(theme, 'label', 'Gothic Clean')}**\n"
            f"Font: **{studio.font_label(active_font)}**\n"
            f"Strength: **{_safe_int(options.get('strength'), 4)}/5**\n"
            f"Separator: **{legacy._separator_choice_label(rule_service.effective_draft_separator(options, theme_separator=_safe_str(getattr(theme, 'channel_separator', 'none'), 'none')))}**\n"  # type: ignore[attr-defined]
            f"Global rule: **{'On' if counts.get('global') else 'Off'}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Saved narrow rules",
        value=(
            f"Categories: **{counts.get('categories', 0)}**\n"
            f"Channels: **{counts.get('channels', 0)}**\n"
            f"Exact names: **{counts.get('manual_names', 0)}**\n"
            f"Exact protection: **{counts.get('protection_items', 0)}**\n"
            f"Name protection: **{counts.get('protection_names', 0)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Choose what you want to do",
        value=(
            "🌐 **Design Entire Server** — choose theme, font, strength, separator, and category frame with live examples.\n"
            "✏️ **Edit One Category / Channel** — rename or style one exact item.\n"
            "🩺 **Fix Inconsistent Names** — scan first, then build a safe Smart Repair preview.\n"
            "🔐 **Saved Rules & Protection** — manage what future previews enforce; this does not rename anything by itself.\n"
            "↩️ **Undo Last Apply** — review and restore the previous names from the latest applied batch."
        ),
        inline=False,
    )
    embed.add_field(
        name="What Dank Design never redesigns",
        value="Permissions, roles, topics, channel order, ticket placement, slowmode, NSFW settings, verification, or category placement.",
        inline=False,
    )
    embed.set_footer(text="Names only • Preview before batch changes • Narrow saved rules always win")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


async def _go_home(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=False)
    options = await _load_design_options(int(guild.id))
    await interaction.edit_original_response(embed=_home_embed(guild, options), view=DesignHomeView(options))


async def _store_preview(
    interaction: discord.Interaction,
    items: list[dict[str, Any]],
    options: Mapping[str, Any],
    *,
    mode: str,
    title: str,
) -> None:
    guild = interaction.guild
    assert guild is not None
    created_at = legacy._store_pending(  # type: ignore[attr-defined]
        int(guild.id),
        int(interaction.user.id),
        {"items": list(items), "options": dict(options), "mode": mode},
    )
    has_blockers = any(item.get("status") == "failed" for item in items)
    has_changes = any(item.get("status") == "changed" for item in items)
    preview_embed = legacy._preview_embed(guild, items, title=title)  # type: ignore[attr-defined]
    if mode == "preview_server_v2":
        theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
        font = _design_server_font(options)
        separator_id = _design_server_separator(options)
        frame = _design_server_category_frame(options)
        preview_embed.insert_field_at(
            0,
            name="Selected server style",
            value=(
                f"Theme: **{getattr(theme, 'label', 'Gothic Clean')}**\n"
                f"Font: **{studio.font_label(font)}** · `{studio.font_preview(font)}`\n"
                f"Strength: **{max(1, min(5, _safe_int(options.get('strength'), 4)))}/5**\n"
                f"Separator: **{legacy._separator_choice_label(separator_id)}**\n"  # type: ignore[attr-defined]
                f"Categories: **{legacy._category_frame_choice_label(frame)}**\n"  # type: ignore[attr-defined]
                "Saved category/channel/exact rules still win for their own items."
            )[:1024],
            inline=False,
        )
    await interaction.edit_original_response(
        embed=preview_embed,
        view=ReviewedPreviewView(can_apply=not has_blockers and has_changes, pending_created_at=created_at),
    )


def _design_server_font(options: Mapping[str, Any]) -> str:
    theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
    explicit = _safe_str(options.get("font"), "").lower().replace("-", "_")
    if explicit in studio.DESIGN_FONT_STYLES:
        return explicit
    fallback = _safe_str(getattr(theme, "font", "normal"), "normal").lower().replace("-", "_")
    return fallback if fallback in studio.DESIGN_FONT_STYLES else "normal"


def _font_override_active(options: Mapping[str, Any]) -> bool:
    return _safe_str(options.get("font"), "").lower().replace("-", "_") in studio.DESIGN_FONT_STYLES


def _design_server_category_frame(options: Mapping[str, Any]) -> str:
    return plans.effective_server_category_frame_id(options)


def _category_frame_override_active(options: Mapping[str, Any]) -> bool:
    return _safe_str(options.get("category_frame_id"), "") in studio.CATEGORY_FRAMES_BY_ID


def _design_server_examples(options: Mapping[str, Any]) -> tuple[str, str]:
    theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
    font = _design_server_font(options)
    separator = _design_server_separator(options)
    strength = max(1, min(5, _safe_int(options.get("strength"), 4)))
    frame = _design_server_category_frame(options)

    category = studio.build_styled_name(
        "the-420-lobby",
        kind="category",
        theme_id=_safe_str(getattr(theme, "id", "gothic_clean"), "gothic_clean"),
        strength=strength,
        icon_mode="replace_missing",
        protection_rules={},
        protection_mode="full",
        separator_id=separator,
        category_frame_id=frame,
        font=font,
        emoji_override="🍃",
        exact_match=True,
    )
    channel = studio.build_styled_name(
        "general-chat",
        kind="text",
        theme_id=_safe_str(getattr(theme, "id", "gothic_clean"), "gothic_clean"),
        strength=strength,
        icon_mode="replace_missing",
        protection_rules={},
        protection_mode="full",
        separator_id=separator,
        category_frame_id=frame,
        font=font,
        emoji_override="💬",
        exact_match=True,
    )
    return _safe_str(category.after, "the-420-lobby"), _safe_str(channel.after, "general-chat")

class DesignServerThemeSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        choices: list[discord.SelectOption] = []
        for theme in studio.THEMES[:25]:
            font = studio.font_label(getattr(theme, "font", "normal"))
            frame = legacy._category_frame_choice_label(getattr(theme, "category_frame", "plain"))  # type: ignore[attr-defined]
            choices.append(
                discord.SelectOption(
                    label=theme.label[:100],
                    value=theme.id,
                    default=theme.id == current,
                    description=f"{studio.font_preview(getattr(theme, 'font', 'normal'))} • {font} • {frame}"[:100],
                )
            )
        super().__init__(placeholder="1) Choose the server theme", min_values=1, max_values=1, options=choices, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        options["theme_id"] = self.values[0]
        options.pop("font", None)
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.edit_original_response(embed=_design_server_embed(guild, options), view=DesignServerView(options))


class DesignServerFontSelect(discord.ui.Select):
    def __init__(self, options: Mapping[str, Any]) -> None:
        theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
        theme_font = _safe_str(getattr(theme, "font", "normal"), "normal").lower().replace("-", "_")
        explicit = _safe_str(options.get("font"), "").lower().replace("-", "_")
        choices: list[discord.SelectOption] = [
            discord.SelectOption(
                label=f"Theme Default · {studio.font_label(theme_font)}"[:100],
                value="__theme__",
                description=f"{studio.font_preview(theme_font)} · follows the selected theme"[:100],
                default=explicit not in studio.DESIGN_FONT_STYLES,
            )
        ]
        for style in studio.DESIGN_FONT_STYLES:
            decorative = style in getattr(studio, "RISKY_FONTS", set())
            note = "Decorative · preview recommended" if decorative else "Readable"
            choices.append(
                discord.SelectOption(
                    label=(f"⚠️ {studio.font_label(style)}" if decorative else studio.font_label(style))[:100],
                    value=style,
                    description=f"{studio.font_preview(style)} · {note}"[:100],
                    default=explicit == style,
                )
            )
        super().__init__(
            placeholder="2) Choose the server font",
            min_values=1,
            max_values=1,
            options=choices[:25],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        selected = _safe_str(self.values[0], "__theme__").lower().replace("-", "_")
        if selected == "__theme__":
            options.pop("font", None)
        elif selected in studio.DESIGN_FONT_STYLES:
            options["font"] = selected
        else:
            raise ValueError("Unsupported Dank Design font selection.")
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.edit_original_response(embed=_design_server_embed(guild, options), view=DesignServerView(options))

class DesignServerStrengthSelect(discord.ui.Select):
    LABELS = {
        1: ("1 — Icons only", "Icon/base cleanup only."),
        2: ("2 — Layout", "Adds the selected channel separator."),
        3: ("3 — Font + layout", "Adds the selected font style."),
        4: ("4 — Full theme (recommended)", "Adds category frames where applicable."),
        5: ("5 — Exact normalization", "Strictly normalizes the complete selected theme."),
    }

    def __init__(self, current: int) -> None:
        choices = [
            discord.SelectOption(label=label, value=str(value), default=value == current, description=description)
            for value, (label, description) in self.LABELS.items()
        ]
        super().__init__(placeholder="3) Choose how much styling to use", min_values=1, max_values=1, options=choices, row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        options["strength"] = max(1, min(5, _safe_int(self.values[0], 4)))
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.edit_original_response(embed=_design_server_embed(guild, options), view=DesignServerView(options))


class DesignServerSeparatorSelect(discord.ui.Select):
    def __init__(self, options: Mapping[str, Any]) -> None:
        theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
        theme_options = dict(options)
        theme_options.pop("separator_id", None)
        theme_separator = plans.theme_default_separator_id(theme_options)
        explicit = _safe_str(options.get("separator_id"), "")
        selected = _design_server_separator(options)
        choices: list[discord.SelectOption] = [
            discord.SelectOption(
                label=f"Theme Default · {legacy._separator_choice_label(theme_separator)}"[:100],  # type: ignore[attr-defined]
                value="__theme__",
                description=f"{studio.separator_preview(theme_separator)} · follows the selected theme"[:100],
                default=not bool(explicit),
            )
        ]
        for option in legacy._style_change_separator_options(selected):  # type: ignore[attr-defined]
            option.default = bool(explicit and str(option.value) == selected)
            choices.append(option)

        super().__init__(
            placeholder="4) Choose the channel separator",
            min_values=1,
            max_values=1,
            options=choices[:25],
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        selected = _safe_str(self.values[0], "__theme__")
        if selected == "__theme__":
            options.pop("separator_id", None)
        else:
            if selected not in studio.SEPARATORS_BY_ID:
                raise ValueError("Unsupported Dank Design separator selection.")
            options["separator_id"] = selected
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.edit_original_response(embed=_design_server_embed(guild, options), view=DesignServerView(options))


def _design_server_separator(options: Mapping[str, Any]) -> str:
    return plans.effective_server_separator_id(options)


def _separator_override_active(options: Mapping[str, Any]) -> bool:
    return bool(_safe_str(options.get("separator_id"), ""))


def _category_frame_groups() -> tuple[tuple[str, tuple[str, ...]], ...]:
    groups: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for label, frame_ids in tuple(getattr(studio, "CATEGORY_FRAME_GROUPS", tuple()) or tuple()):
        usable = tuple(
            frame_id for frame_id in frame_ids
            if frame_id in studio.CATEGORY_FRAMES_BY_ID and frame_id not in seen
        )
        if not usable:
            continue
        groups.append((_safe_str(label, "Frames"), usable))
        seen.update(usable)

    # Never hide a newly registered canonical frame just because someone forgot
    # to add it to the curated grouping metadata.
    ungrouped = tuple(frame.id for frame in studio.CATEGORY_FRAMES if frame.id not in seen)
    if ungrouped:
        groups.append(("More", ungrouped))
    return tuple(groups)


def _category_frame_page_for(options: Mapping[str, Any]) -> int:
    if not _category_frame_override_active(options):
        return 0
    selected = _design_server_category_frame(options)
    for page, (_label, frame_ids) in enumerate(_category_frame_groups()):
        if selected in frame_ids:
            return page
    return 0


class DesignServerCategoryFrameSelect(discord.ui.Select):
    def __init__(self, options: Mapping[str, Any], *, page: int = 0) -> None:
        groups = _category_frame_groups()
        page = max(0, min(int(page), max(0, len(groups) - 1)))
        _group_label, frame_ids = groups[page] if groups else ("Frames", tuple())

        theme_options = dict(options)
        theme_options.pop("category_frame_id", None)
        theme_frame = plans.theme_default_category_frame_id(theme_options)
        explicit = _safe_str(options.get("category_frame_id"), "")
        override_active = _category_frame_override_active(options)
        selected = _design_server_category_frame(options)
        choices: list[discord.SelectOption] = [
            discord.SelectOption(
                label=f"Theme Default · {legacy._category_frame_choice_label(theme_frame)}"[:100],  # type: ignore[attr-defined]
                value="__theme__",
                description=(
                    f"{studio.category_frame_preview(theme_frame, emoji='🍃', name='the-420-lobby')} · "
                    "follows the selected theme"
                )[:100],
                default=not override_active,
            )
        ]
        for frame_id in frame_ids:
            frame = studio.CATEGORY_FRAMES_BY_ID[frame_id]
            choices.append(
                discord.SelectOption(
                    label=legacy._category_frame_choice_label(frame.id)[:100],  # type: ignore[attr-defined]
                    value=frame.id,
                    description=(
                        f"Result: {studio.category_frame_preview(frame.id, emoji='🍃', name='the-420-lobby')}"
                    )[:100],
                    default=bool(override_active and explicit and frame.id == selected),
                )
            )

        super().__init__(
            placeholder="Choose the server category frame",
            min_values=1,
            max_values=1,
            options=choices[:25],
            row=0,
        )
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        selected = _safe_str(self.values[0], "__theme__")
        if selected == "__theme__":
            options.pop("category_frame_id", None)
        else:
            if selected not in studio.CATEGORY_FRAMES_BY_ID:
                raise ValueError("Unsupported Dank Design category frame selection.")
            options["category_frame_id"] = selected
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.edit_original_response(embed=_design_server_embed(guild, options), view=DesignServerView(options))


def _design_server_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
    separator_id = _design_server_separator(options)
    font = _design_server_font(options)
    strength = max(1, min(5, _safe_int(options.get("strength"), 4)))
    frame = _design_server_category_frame(options)
    narrow_count = _layout_override_count(options)
    category_example, channel_example = _design_server_examples(options)
    font_source = "custom override" if _font_override_active(options) else "theme default"
    separator_source = "custom override" if _separator_override_active(options) else "theme default"
    frame_source = "custom override" if _category_frame_override_active(options) else "theme default"

    embed = discord.Embed(
        title="🌐 Design Entire Server",
        description=(
            "Build the server-wide look in one screen. **Nothing is renamed while you choose options.** "
            "The examples update with your saved draft; Preview shows the exact live-server changes before Apply."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Theme", value=f"**{getattr(theme, 'label', 'Gothic Clean')}**", inline=True)
    embed.add_field(
        name="Font",
        value=f"**{studio.font_label(font)}** · {font_source}\n`{studio.font_preview(font)}`",
        inline=True,
    )
    embed.add_field(name="Strength", value=f"**{strength}/5**", inline=True)
    embed.add_field(
        name="Separator",
        value=f"**{legacy._separator_choice_label(separator_id)}** · {separator_source}",  # type: ignore[attr-defined]
        inline=True,
    )
    embed.add_field(
        name="Category frame",
        value=f"**{legacy._category_frame_choice_label(frame)}** · {frame_source}",  # type: ignore[attr-defined]
        inline=True,
    )
    embed.add_field(
        name="Style example",
        value=f"Category: `{category_example}`\nChannel: `{channel_example}`",
        inline=False,
    )
    if strength < 3 and font != "normal":
        embed.add_field(
            name="ℹ️ Font is selected but not active yet",
            value="Strength **3+** enables font styling. Raise Strength when you want the selected font applied.",
            inline=False,
        )
    if strength < 4:
        embed.add_field(
            name="ℹ️ Category frame is selected but not active yet",
            value="Strength **4+** enables category-frame styling. You can choose the frame now and raise Strength when you want it applied.",
            inline=False,
        )
    if narrow_count:
        embed.add_field(
            name="⚠️ Old design overrides are active",
            value=(
                f"**{narrow_count}** saved layout/name override(s) can make some channels ignore this server-wide style. "
                "Use **Clean Redesign** to clear those old design exceptions **without removing protection rules**."
            ),
            inline=False,
        )
    embed.set_footer(text="Choose → Preview → Apply • Theme Default keeps Font, Separator, and Category Frame tied to the selected theme")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


def _category_frame_picker_embed(
    guild: discord.Guild,
    options: Mapping[str, Any],
    *,
    page: int = 0,
) -> discord.Embed:
    groups = _category_frame_groups()
    total_pages = max(1, len(groups))
    page = max(0, min(int(page), total_pages - 1))
    group_label, frame_ids = groups[page] if groups else ("Frames", tuple())
    selected = _design_server_category_frame(options)
    selected_label = legacy._category_frame_choice_label(selected)  # type: ignore[attr-defined]

    embed = _design_server_embed(guild, options)
    embed.title = f"🖼️ Choose Category Frame · {group_label}"
    embed.description = (
        f"Browse **{len(studio.CATEGORY_FRAMES)} category frames** across {total_pages} style groups. "
        "**Theme Default** follows the selected theme; an explicit frame stays selected when the theme changes. "
        "Nothing is renamed until you return, Preview, and Apply."
    )
    embed.add_field(
        name=f"Frame group {page + 1}/{total_pages}",
        value=(
            f"**{group_label}** · {len(frame_ids)} choices on this page\n"
            f"Current: **{selected_label}**"
        ),
        inline=False,
    )
    return embed


class DesignServerCategoryFrameView(DesignView):
    def __init__(self, options: Mapping[str, Any], *, page: int = 0) -> None:
        super().__init__(timeout=900)
        self.options = dict(options)
        groups = _category_frame_groups()
        self.total_pages = max(1, len(groups))
        self.page = max(0, min(int(page), self.total_pages - 1))
        self.add_item(DesignServerCategoryFrameSelect(options, page=self.page))
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:category_frame_prev", row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        page = max(0, self.page - 1)
        await interaction.response.edit_message(
            embed=_category_frame_picker_embed(guild, self.options, page=page),
            view=DesignServerCategoryFrameView(self.options, page=page),
        )

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:category_frame_next", row=1)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        page = min(self.total_pages - 1, self.page + 1)
        await interaction.response.edit_message(
            embed=_category_frame_picker_embed(guild, self.options, page=page),
            view=DesignServerCategoryFrameView(self.options, page=page),
        )

    @discord.ui.button(label="Back to Server Design", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:category_frame_back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_design_server_embed(guild, self.options),
            view=DesignServerView(self.options),
        )


def _clean_redesign_embed(options: Mapping[str, Any]) -> discord.Embed:
    count = _layout_override_count(options)
    embed = discord.Embed(
        title="🧹 Start a Clean Redesign?",
        description=(
            f"This will clear **{count}** saved layout/name override(s) that can make a server-wide redesign look inconsistent.\n\n"
            "**It does not rename anything now.** Your selected Theme, Font, Strength, Separator, and Category Frame stay selected. "
            "Protection rules stay intact, and permissions, roles, topics, channel order, tickets, and verification are untouched."
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="What gets cleared",
        value="Global format lock • Category layout rules • Channel layout rules • Exact manual-name overrides",
        inline=False,
    )
    embed.add_field(
        name="What stays",
        value="Server draft • Exact-item protection • Name protection • Every non-design server setting",
        inline=False,
    )
    embed.set_footer(text="Confirm clears saved design exceptions only • Preview still required before any rename")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


class CleanRedesignConfirmView(DesignView):
    def __init__(self, options: Mapping[str, Any]) -> None:
        super().__init__(timeout=300)
        self.options = dict(options)

    @discord.ui.button(label="Clear Saved Design Overrides", emoji="🧹", style=discord.ButtonStyle.danger, custom_id="dank_design_v2:clean_redesign_confirm", row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        updated = rule_service.reset_layout_overrides(options)
        legacy._clear_format_editor_drafts(int(guild.id))  # type: ignore[attr-defined]
        await legacy._save_options(interaction, updated)  # type: ignore[attr-defined]
        embed = _design_server_embed(guild, updated)
        embed.title = "✅ Clean Redesign Ready"
        embed.description = (
            "Old saved layout/name exceptions were cleared. **Protection rules were kept.** "
            "Choose the server Theme, Font, Strength, Separator, and Category Frame you want, then preview before applying."
        )
        await interaction.edit_original_response(embed=embed, view=DesignServerView(updated))

    @discord.ui.button(label="Cancel", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:clean_redesign_cancel", row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_design_server_embed(guild, self.options),
            view=DesignServerView(self.options),
        )


class DesignServerView(DesignView):
    def __init__(self, options: Mapping[str, Any]) -> None:
        super().__init__(timeout=900)
        self.options = dict(options)
        self.add_item(DesignServerThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(DesignServerFontSelect(options))
        self.add_item(DesignServerStrengthSelect(_safe_int(options.get("strength"), 4)))
        self.add_item(DesignServerSeparatorSelect(options))
        self.clean_redesign.disabled = _layout_override_count(options) == 0

    @discord.ui.button(label="Frame", emoji="🖼️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:category_frame", row=4)
    async def category_frame(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        page = _category_frame_page_for(self.options)
        await interaction.response.edit_message(
            embed=_category_frame_picker_embed(guild, self.options, page=page),
            view=DesignServerCategoryFrameView(self.options, page=page),
        )

    @discord.ui.button(label="Preview Server", emoji="👁️", style=discord.ButtonStyle.success, custom_id="dank_design_v2:server_preview", row=4)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_saved_design_plan(guild, options)
        await _store_preview(interaction, items, plan_options, mode="preview_server_v2", title="👁️ Server Design Preview")

    @discord.ui.button(label="Preview Separator", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:separator_only", row=4)
    async def separator_only(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        selected = _design_server_separator(options)
        items = legacy._build_channel_separator_style_change_plan(  # type: ignore[attr-defined]
            guild,
            options,
            separator_id=selected,
        )
        has_blockers = any(item.get("status") == "failed" for item in items)
        has_changes = any(item.get("status") == "changed" for item in items)
        created_at = legacy._store_pending(  # type: ignore[attr-defined]
            int(guild.id),
            int(interaction.user.id),
            {
                "items": items,
                "options": dict(options),
                "mode": "style_change_separator",
                "style_change_dimension": "channel_separator",
                "separator_id": selected,
            },
        )
        await interaction.edit_original_response(
            embed=legacy._style_change_preview_embed(guild, items, separator_id=selected),  # type: ignore[attr-defined]
            view=LegacyStyleChangePreviewView(
                can_apply=not has_blockers and has_changes,
                has_blockers=has_blockers,
                pending_created_at=created_at,
            ),
        )

    @discord.ui.button(label="Clean Redesign", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:clean_redesign", row=4)
    async def clean_redesign(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(
            embed=_clean_redesign_embed(self.options),
            view=CleanRedesignConfirmView(self.options),
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:server_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


def _edit_one_embed() -> discord.Embed:
    embed = discord.Embed(
        title="✏️ Edit One Category / Channel",
        description=(
            "Choose **Category** or **Channel**, then pick the exact item.\n\n"
            "On the item screen, **Rename** is the only immediate name change. "
            "**Preview Fixes** and **Custom Format** show a preview and require **Apply Reviewed Changes**."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Rule priority",
        value="Exact name → Channel rule → Category rule → Global rule → Server design. Narrow rules never get silently replaced by broader ones.",
        inline=False,
    )
    return embed


class EditOneItemView(DesignView):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Category", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit_category", row=0)
    async def category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=legacy._category_editor_embed(guild, page=0),
            view=legacy.CategoryEditorPickerView(guild, page=0),
        )  # type: ignore[attr-defined]

    @discord.ui.button(label="Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit_channel", row=0)
    async def channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=legacy._channel_editor_embed(guild, page=0),
            view=legacy.ChannelEditorPickerView(guild, page=0),
        )  # type: ignore[attr-defined]

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:edit_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


def _review_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🩺 Fix Inconsistent Names",
        description=(
            "**Scan Saved Design** is read-only and tells you what disagrees with your saved rules.\n"
            "**Build Smart Repair Preview** learns the established style inside each category, keeps saved narrow rules authoritative, "
            "and blocks Apply when confidence is not high enough."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Scan first • Preview second • Nothing is renamed until Apply Reviewed Changes")
    return embed


def _scan_embed(guild: discord.Guild, options: Mapping[str, Any], items: list[dict[str, Any]]) -> discord.Embed:
    counts = legacy._consistency_summary(items)  # type: ignore[attr-defined]
    embed = discord.Embed(
        title="🩺 Saved Design Scan",
        description=(
            "**Read-only scan. Nothing was renamed.** This compares visible names with your current saved design and narrow rules."
        ),
        color=discord.Color.orange() if counts.get("failed") or counts.get("needs_fix") else discord.Color.green(),
    )
    embed.add_field(
        name="Results",
        value=(
            f"Already matching: **{counts.get('matches', 0)}**\n"
            f"Would change under saved design: **{counts.get('needs_fix', 0)}**\n"
            f"Protected/skipped: **{counts.get('protected', 0)}**\n"
            f"Blocked: **{counts.get('failed', 0)}**\n"
            f"Notes: **{counts.get('notes', 0)}**"
        ),
        inline=True,
    )
    rule_counts = _rule_counts(options)
    embed.add_field(
        name="Saved authority in this scan",
        value=(
            f"Global: **{'On' if rule_counts.get('global') else 'Off'}**\n"
            f"Category rules: **{rule_counts.get('categories', 0)}**\n"
            f"Channel rules: **{rule_counts.get('channels', 0)}**\n"
            f"Exact names: **{rule_counts.get('manual_names', 0)}**"
        ),
        inline=True,
    )
    changed = [item for item in items if item.get("status") == "changed"]
    if changed:
        lines = [f"• `{_safe_str(item.get('before'))}` → `{_safe_str(item.get('after'))}`"[:220] for item in changed[:8]]
        embed.add_field(name="Sample differences", value="\n".join(lines)[:1024], inline=False)
    embed.add_field(
        name="Next step",
        value="Use **Build Smart Repair Preview** only if you want Dank Design to prepare a safe repair plan. Otherwise Back leaves everything exactly as it is.",
        inline=False,
    )
    embed.set_footer(text="Read-only • Saved rules remain authoritative")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


def _repair_preview_embed(
    guild: discord.Guild,
    items: list[dict[str, Any]],
    options: Mapping[str, Any],
    analysis: Mapping[str, Any],
) -> discord.Embed:
    counts = legacy._consistency_summary(items)  # type: ignore[attr-defined]
    confidence = options.get("__repair_confidence_result") if isinstance(options.get("__repair_confidence_result"), Mapping) else {}
    embed = discord.Embed(
        title="🧭 Smart Repair Preview",
        description=(
            "**Nothing has been renamed.** Smart Repair analyzed each category independently instead of flattening the whole server to one guessed style. "
            "Saved exact/channel/category/global rules still win."
        ),
        color=discord.Color.green() if bool(confidence.get("apply_allowed")) else discord.Color.orange(),
    )
    embed.add_field(
        name="Repair plan",
        value=(
            f"Already matching: **{counts.get('matches', 0)}**\n"
            f"Ready repairs: **{counts.get('needs_fix', 0)}**\n"
            f"Protected/skipped: **{counts.get('protected', 0)}**\n"
            f"Blocked: **{counts.get('failed', 0)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Repair confidence",
        value=repair_confidence.confidence_summary_text(confidence) if confidence else "No confidence result was produced. Apply is blocked.",
        inline=True,
    )
    changed = [item for item in items if item.get("status") == "changed"]
    if changed:
        lines = [f"• `{_safe_str(item.get('before'))}` → `{_safe_str(item.get('after'))}`"[:220] for item in changed[:8]]
        embed.add_field(name="Will repair", value="\n".join(lines)[:1024], inline=False)
    blocked_lines = list(confidence.get("blocked_lines") or []) if isinstance(confidence, Mapping) else []
    review_lines = list(confidence.get("review_lines") or []) if isinstance(confidence, Mapping) else []
    if blocked_lines:
        embed.add_field(name="Apply blocked for safety", value="\n".join(str(line) for line in blocked_lines[:6])[:1024], inline=False)
    if review_lines:
        embed.add_field(name="Needs manual review", value="\n".join(str(line) for line in review_lines[:6])[:1024], inline=False)
    profiles = analysis.get("profiles") if isinstance(analysis.get("profiles"), Mapping) else {}
    if profiles:
        embed.add_field(name="Detection", value=f"Category-aware profiles analyzed: **{len(profiles)}**", inline=False)
    embed.set_footer(text="Apply is enabled only when the plan is fully reviewable and confidence is high")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


class ReviewRepairView(DesignView):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Scan Saved Design", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:doctor", row=0)
    async def doctor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_saved_design_plan(guild, options)
        await interaction.edit_original_response(embed=_scan_embed(guild, plan_options, items), view=ReviewRepairView())

    @discord.ui.button(label="Build Smart Repair Preview", emoji="🧭", style=discord.ButtonStyle.success, custom_id="dank_design_v2:drift", row=0)
    async def drift(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, analysis = await plans.build_drift_repair_plan(guild, options)
        created_at = legacy._store_pending(  # type: ignore[attr-defined]
            int(guild.id),
            int(interaction.user.id),
            {"items": items, "options": dict(plan_options), "mode": "consistency_check_v2"},
        )
        has_blockers = any(item.get("status") == "failed" for item in items)
        has_changes = any(item.get("status") == "changed" for item in items)
        await interaction.edit_original_response(
            embed=_repair_preview_embed(guild, items, plan_options, analysis),
            view=ReviewedPreviewView(can_apply=not has_blockers and has_changes, pending_created_at=created_at),
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:review_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


def _saved_rules_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    counts = _rule_counts(options)
    embed = discord.Embed(
        title="🔐 Saved Rules & Protection",
        description="These settings control what **future previews** enforce. Saving, removing, or changing a rule here does **not** rename a Discord item by itself.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current rules",
        value=(
            f"Global: **{'On' if counts.get('global') else 'Off'}**\n"
            f"Category: **{counts.get('categories', 0)}**\n"
            f"Channel: **{counts.get('channels', 0)}**\n"
            f"Exact names: **{counts.get('manual_names', 0)}**\n"
            f"Exact protection: **{counts.get('protection_items', 0)}**\n"
            f"Name protection: **{counts.get('protection_names', 0)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Which tool does what",
        value=(
            "**Layout Rules** = inspect or add global/category/channel visual rules.\n"
            "**Remove One Rule** = remove exactly one listed saved rule or clean deleted-item rows.\n"
            "**Protection** = manage exact-item and normalized-name protection.\n"
            "For a clean server-wide redesign, use **Design Entire Server → Clean Redesign**; it clears old layout/name exceptions but keeps protection."
        ),
        inline=False,
    )
    embed.set_footer(text="Narrower rules always win • Protection is separate • No rename happens on this screen")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


class SavedRulesView(DesignView):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Layout Rules", emoji="🔒", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:layout_rules", row=0)
    async def layout_rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        await interaction.edit_original_response(embed=legacy._format_locks_embed(guild, options), view=legacy.FormatLocksView())  # type: ignore[attr-defined]

    @discord.ui.button(label="Remove One Rule", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:unlock", row=0)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        await interaction.edit_original_response(embed=legacy._format_lock_manager_embed(guild, options, page=0), view=legacy.LockManagerView(guild, options, page=0))  # type: ignore[attr-defined]

    @discord.ui.button(label="Protection", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:protection", row=1)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        await interaction.edit_original_response(embed=legacy._protection_manager_embed(guild, options), view=legacy.ProtectionManagerView())  # type: ignore[attr-defined]

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:rules_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


class DesignHomeView(DesignView):
    """Five explicit workflows. No style controls are mixed into the home."""

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Design Entire Server", emoji="🌐", style=discord.ButtonStyle.success, custom_id="dank_design_v2:server", row=0)
    async def design_server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        await interaction.edit_original_response(embed=_design_server_embed(guild, options), view=DesignServerView(options))

    @discord.ui.button(label="Edit One Category / Channel", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit", row=0)
    async def edit_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_edit_one_embed(), view=EditOneItemView())

    @discord.ui.button(label="Fix Inconsistent Names", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:review", row=1)
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_review_embed(), view=ReviewRepairView())

    @discord.ui.button(label="Saved Rules & Protection", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:rules", row=1)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        await interaction.edit_original_response(embed=_saved_rules_embed(guild, options), view=SavedRulesView())

    @discord.ui.button(label="Undo Last Apply", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design_v2:rollback", row=2)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_undo(interaction)


def _snapshot_matches(snapshot: Mapping[str, Any] | None, created_at: float) -> bool:
    if not isinstance(snapshot, Mapping):
        return False
    return _safe_float(snapshot.get("created_at"), -1.0) == _safe_float(created_at, -2.0)


def _remember_snapshot(guild_id: int, payload: Mapping[str, Any]) -> None:
    guild_key = legacy._guild_key(int(guild_id))  # type: ignore[attr-defined]
    rows = legacy._LAST_SNAPSHOTS.setdefault(guild_key, [])  # type: ignore[attr-defined]
    rows.append(dict(payload))
    legacy._LAST_SNAPSHOTS[guild_key] = rows[-10:]  # type: ignore[attr-defined]


async def _store_durable_snapshot(guild_id: int, user_id: int, prepared: list[apply_service.PreparedRename]) -> dict[str, Any]:
    created_at = time.time()
    rows = apply_service.snapshot_rows(prepared, user_id=user_id, timestamp=created_at)
    payload = {
        "created_at": created_at,
        "items": rows,
        "admin_id": str(int(user_id)),
        "durable": True,
    }
    await legacy._persist_rollback_snapshot(int(guild_id), payload)  # type: ignore[attr-defined]
    _remember_snapshot(int(guild_id), payload)
    return payload


async def _store_snapshot_with_memory_fallback(
    guild_id: int,
    user_id: int,
    prepared: list[apply_service.PreparedRename],
) -> tuple[dict[str, Any] | None, bool]:
    if not prepared:
        return None, False
    try:
        return await _store_durable_snapshot(guild_id, user_id, prepared), True
    except Exception as exc:
        created_at = time.time()
        payload = {
            "created_at": created_at,
            "items": apply_service.snapshot_rows(prepared, user_id=user_id, timestamp=created_at),
            "admin_id": str(int(user_id)),
            "durable": False,
            "persistence_error": type(exc).__name__,
        }
        _remember_snapshot(guild_id, payload)
        try:
            print(
                "⚠️ Dank Design durable Undo snapshot failed; keeping successful live names "
                f"with memory-only Undo guild={int(guild_id)} error={type(exc).__name__}"
            )
        except Exception:
            pass
        return payload, False


async def _store_residual_snapshot(
    guild_id: int,
    user_id: int,
    prepared: list[apply_service.PreparedRename],
) -> tuple[dict[str, Any] | None, bool]:
    return await _store_snapshot_with_memory_fallback(guild_id, user_id, prepared)


async def _pop_snapshot_if_current(guild_id: int, created_at: float) -> bool:
    latest = await legacy._latest_rollback_snapshot(int(guild_id))  # type: ignore[attr-defined]
    if not _snapshot_matches(latest, created_at):
        return False
    assert latest is not None
    if latest.get("durable") is False:
        guild_key = legacy._guild_key(int(guild_id))  # type: ignore[attr-defined]
        rows = list(legacy._LAST_SNAPSHOTS.get(guild_key) or [])  # type: ignore[attr-defined]
        if rows and _snapshot_matches(rows[-1], created_at):
            rows.pop()
            legacy._LAST_SNAPSHOTS[guild_key] = rows[-10:]  # type: ignore[attr-defined]
            return True
        return False
    popped = await legacy._pop_latest_rollback_snapshot(int(guild_id))  # type: ignore[attr-defined]
    return _snapshot_matches(popped, created_at)


def _undo_preview_embed(snapshot: Mapping[str, Any]) -> discord.Embed:
    items = list(snapshot.get("items") or [])
    lines = [
        f"• `{_safe_str(item.get('new_name') or item.get('after'))}` → `{_safe_str(item.get('old_name') or item.get('before'))}`"[:220]
        for item in items[-10:]
    ]
    embed = discord.Embed(
        title="↩️ Undo Last Apply",
        description=(
            "**Read-only preview. Nothing has been restored yet.**\n\n"
            "Undo is also preflighted as one batch. If any current name no longer matches the saved Apply snapshot, Undo changes nothing and keeps the snapshot."
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(name="Items in latest Apply", value=f"**{len(items)}**", inline=True)
    embed.add_field(name="Snapshot storage", value="Durable" if snapshot.get("durable") is not False else "Emergency memory-only", inline=True)
    embed.add_field(name="Will restore", value="\n".join(lines)[:1024] or "No restorable names.", inline=False)
    embed.set_footer(text="Confirm Undo only after reviewing these exact names")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


async def _open_undo(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=False)
    latest = await legacy._latest_rollback_snapshot(int(guild.id))  # type: ignore[attr-defined]
    if not latest:
        await legacy.safe_send_interaction(  # type: ignore[attr-defined]
            interaction,
            content="No applied Dank Design batch is available to undo.",
            ephemeral=True,
            action_name="design.v2.undo.none",
        )
        return
    created_at = _safe_float(latest.get("created_at"), 0.0)
    await interaction.edit_original_response(embed=_undo_preview_embed(latest), view=UndoConfirmView(snapshot_created_at=created_at))


class DoneView(DesignView):
    def __init__(self, *, can_rollback: bool) -> None:
        super().__init__(timeout=900)
        self.rollback.disabled = not can_rollback

    @discord.ui.button(label="Undo Latest Apply", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design_v2:done_rollback", row=0)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_undo(interaction)

    @discord.ui.button(label="Back to Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:done_home", row=0)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


class UndoConfirmView(DesignView):
    def __init__(self, *, snapshot_created_at: float) -> None:
        super().__init__(timeout=900)
        self.snapshot_created_at = float(snapshot_created_at)

    @discord.ui.button(label="Confirm Undo Last Apply", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design_v2:undo_confirm", row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        lock = legacy._lock_for(int(guild.id))  # type: ignore[attr-defined]
        if lock.locked():
            await interaction.response.send_message("⏳ A Dank Design job is already running for this server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        latest = await legacy._latest_rollback_snapshot(int(guild.id))  # type: ignore[attr-defined]
        if not _snapshot_matches(latest, self.snapshot_created_at):
            await interaction.edit_original_response(
                content="❌ This Undo preview is obsolete. Open Undo Last Apply again.",
                embed=None,
                view=DoneView(can_rollback=bool(latest)),
            )
            return

        async with lock:
            latest = await legacy._latest_rollback_snapshot(int(guild.id))  # type: ignore[attr-defined]
            if not _snapshot_matches(latest, self.snapshot_created_at):
                await interaction.edit_original_response(content="❌ The latest Apply snapshot changed before Undo started. Nothing was changed.", embed=None, view=DoneView(can_rollback=True))
                return
            assert latest is not None
            items = list(latest.get("items") or [])
            ready, errors = await apply_service.preflight_undo(guild, items, name_limit=studio.DISCORD_NAME_LIMIT)
            if errors:
                embed = discord.Embed(
                    title="❌ Undo Blocked Before Any Rename",
                    description="**Nothing was changed and the Undo snapshot was kept.** At least one current name no longer matches the Apply snapshot.",
                    color=discord.Color.orange(),
                )
                embed.add_field(name="What changed", value="\n".join(f"• {line}" for line in errors[:8])[:1024], inline=False)
                await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=True))
                return

            result = await apply_service.undo_prepared(
                guild,
                ready,
                user_id=int(interaction.user.id),
                delay_seconds=studio.DEFAULT_DELAY_SECONDS,
            )
            if not result.ok:
                residual = list(result.residual or [])
                embed = discord.Embed(
                    title="⚠️ Undo Stopped Safely",
                    description=(
                        f"{result.failure}\n\n"
                        + (
                            f"The Undo attempt could not fully restore its own partial work. **{len(residual)}** row(s) need attention. The original Undo snapshot was kept."
                            if residual
                            else f"Automatically restored **{result.restored_count}** partial Undo change(s). **The server is back to its pre-Undo names and the snapshot was kept.**"
                        )
                    ),
                    color=discord.Color.orange(),
                )
                if result.rollback_failures:
                    embed.add_field(name="Attention", value="\n".join(f"• {line}" for line in result.rollback_failures[:8])[:1024], inline=False)
                await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=True))
                return

            popped = await _pop_snapshot_if_current(int(guild.id), self.snapshot_created_at)
            next_snapshot = await legacy._latest_rollback_snapshot(int(guild.id))  # type: ignore[attr-defined]
            embed = discord.Embed(
                title="↩️ Undo Complete",
                description=f"Restored **{len(result.applied)}** item(s). Failed **0**. The latest Apply snapshot was {'removed' if popped else 'left in history for safety'}.",
                color=discord.Color.green() if popped else discord.Color.orange(),
            )
            if not popped:
                embed.add_field(name="History note", value="The names were restored, but history cleanup did not complete. Reopening Undo is safe because preflight will refuse stale rows.", inline=False)
            await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(next_snapshot)))

    @discord.ui.button(label="Cancel", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:undo_cancel", row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


async def _persist_separator_settings(
    interaction: discord.Interaction,
    payload: Mapping[str, Any],
    applied: list[apply_service.PreparedRename],
) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None
    chosen = _safe_str(payload.get("separator_id"), "")
    if not chosen:
        raise RuntimeError("Separator preview did not contain a selected separator.")
    previous = await _load_design_options(int(guild.id))
    updated = rule_service.persist_separator_choice(
        previous,
        separator_id=chosen,
        applied_rows=applied,
    )
    await legacy._save_options(interaction, updated)  # type: ignore[attr-defined]
    return previous


async def _return_from_reviewed_preview(
    interaction: discord.Interaction,
    pending_created_at: float | None,
) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None

    key = legacy._key(int(guild.id), int(interaction.user.id))  # type: ignore[attr-defined]
    payload = legacy._PENDING.get(key) or {}  # type: ignore[attr-defined]
    if not legacy._pending_matches(payload, pending_created_at):  # type: ignore[attr-defined]
        await _go_home(interaction)
        return

    mode = _safe_str(payload.get("mode"), "")
    if mode in {"preview_server_v2", "style_change_separator"}:
        await interaction.response.defer(ephemeral=True, thinking=False)
        options = await _load_design_options(int(guild.id))
        await interaction.edit_original_response(
            embed=_design_server_embed(guild, options),
            view=DesignServerView(options),
        )
        return

    if mode == "consistency_check_v2":
        await interaction.response.edit_message(embed=_review_embed(), view=ReviewRepairView())
        return

    if mode == "category_editor":
        category_id = _safe_int(payload.get("category_id"), 0)
        category = guild.get_channel(category_id) if category_id > 0 else None
        if isinstance(category, discord.CategoryChannel):
            await interaction.response.edit_message(
                embed=legacy._category_action_embed(category),  # type: ignore[attr-defined]
                view=legacy.CategoryEditorActionView(category_id),  # type: ignore[attr-defined]
            )
        else:
            await interaction.response.edit_message(
                embed=legacy._category_editor_embed(guild, page=0),  # type: ignore[attr-defined]
                view=legacy.CategoryEditorPickerView(guild, page=0),  # type: ignore[attr-defined]
            )
        return

    if mode == "channel_editor":
        channel_id = _safe_int(payload.get("channel_id"), 0)
        channel = guild.get_channel(channel_id) if channel_id > 0 else None
        if channel is not None:
            parent = getattr(channel, "category", None)
            category_id = _safe_int(getattr(parent, "id", 0), 0) or None
            await interaction.response.edit_message(
                embed=legacy._channel_action_embed(channel),  # type: ignore[attr-defined]
                view=legacy.ChannelEditorActionView(channel_id, category_id=category_id),  # type: ignore[attr-defined]
            )
        else:
            await interaction.response.edit_message(
                embed=legacy._channel_editor_embed(guild, page=0),  # type: ignore[attr-defined]
                view=legacy.ChannelEditorPickerView(guild, page=0),  # type: ignore[attr-defined]
            )
        return

    if mode in {"category_exact_format", "channel_exact_format"}:
        target_id = _safe_int(payload.get("target_id"), 0)
        if target_id > 0:
            scope = "category" if mode.startswith("category_") else "channel"
            await legacy._open_exact_format_editor(  # type: ignore[attr-defined]
                interaction,
                scope=scope,
                target_id=target_id,
            )
            return

    await _go_home(interaction)


class ReviewedPreviewView(DesignView):
    """One transactional preview/apply owner for every active Studio batch flow."""

    def __init__(self, *, can_apply: bool, pending_created_at: float | None = None) -> None:
        super().__init__(timeout=900)
        self.pending_created_at = pending_created_at
        self.apply.disabled = not can_apply

    @discord.ui.button(label="Apply Reviewed Changes", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_design_v2:apply", row=0)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        key = legacy._key(int(guild.id), int(interaction.user.id))  # type: ignore[attr-defined]
        payload = legacy._PENDING.get(key) or {}  # type: ignore[attr-defined]

        if not legacy._pending_matches(payload, self.pending_created_at):  # type: ignore[attr-defined]
            await interaction.response.send_message("❌ This preview is obsolete. Build a fresh preview before applying.", ephemeral=True)
            return
        items = list(payload.get("items") or [])
        if not items:
            await interaction.response.send_message("❌ No reviewed preview is available. Build the preview again.", ephemeral=True)
            return
        if any(item.get("status") == "failed" for item in items):
            await interaction.response.send_message("❌ This preview has blockers. Fix them and preview again.", ephemeral=True)
            return

        lock = legacy._lock_for(int(guild.id))  # type: ignore[attr-defined]
        if lock.locked():
            await interaction.response.send_message("⏳ A design Apply is already running for this server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        mode = _safe_str(payload.get("mode"), "preview")
        async with lock:
            ready, skipped, preflight_errors = await apply_service.preflight_plan(
                guild,
                items,
                name_limit=studio.DISCORD_NAME_LIMIT,
            )
            if preflight_errors:
                legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]
                embed = discord.Embed(
                    title="❌ Preview Changed Before Apply",
                    description=(
                        "**No names were changed.** The complete batch was checked before the first rename and at least one preview row is no longer current. "
                        "Build a fresh preview instead of applying stale assumptions."
                    ),
                    color=discord.Color.orange(),
                )
                embed.add_field(name="What changed", value="\n".join(f"• {line}" for line in preflight_errors[:8])[:1024], inline=False)
                await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=False))
                return

            async def progress(done: int, total: int) -> None:
                if done % 5 == 0 or done == total:
                    await interaction.edit_original_response(content=f"🚀 Applying reviewed design… **{done}/{total}** changed.")

            result = await apply_service.apply_prepared(
                guild,
                ready,
                user_id=int(interaction.user.id),
                delay_seconds=studio.DEFAULT_DELAY_SECONDS,
                progress=progress,
            )
            if not result.ok:
                legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]
                residual = list(result.residual or [])
                snapshot, durable = await _store_residual_snapshot(int(guild.id), int(interaction.user.id), residual)
                embed = discord.Embed(
                    title="⚠️ Apply Stopped Safely",
                    description=(
                        f"{result.failure}\n\n"
                        + (
                            f"Automatically restored **{result.restored_count}** earlier rename(s). **No partial design was left behind.**"
                            if not residual
                            else f"Automatic compensation left **{len(residual)}** item(s) changed. An Undo snapshot was kept{' durably' if durable else ' in emergency memory'} for those rows."
                        )
                    ),
                    color=discord.Color.orange(),
                )
                if result.rollback_failures:
                    embed.add_field(name="Compensation attention", value="\n".join(f"• {line}" for line in result.rollback_failures[:8])[:1024], inline=False)
                if snapshot and not durable:
                    embed.add_field(name="Important", value="Emergency Undo history is memory-only because durable snapshot storage failed. Use **Undo Latest Apply** before a bot restart.", inline=False)
                await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(snapshot)))
                return

            separator_previous_options: dict[str, Any] | None = None
            if mode == "style_change_separator":
                try:
                    separator_previous_options = await _persist_separator_settings(interaction, payload, result.applied)
                except Exception as settings_exc:
                    if result.applied:
                        restored, residual, rollback_failures = await apply_service.compensate_applied(
                            guild,
                            result.applied,
                            user_id=int(interaction.user.id),
                            delay_seconds=studio.DEFAULT_DELAY_SECONDS,
                        )
                    else:
                        restored, residual, rollback_failures = 0, [], []
                    emergency, durable = await _store_residual_snapshot(int(guild.id), int(interaction.user.id), residual)
                    legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]
                    embed = discord.Embed(
                        title="⚠️ Separator Apply Reversed Because Its Setting Could Not Be Saved",
                        description=(
                            f"Saving the selected separator failed with **{type(settings_exc).__name__}**. "
                            + (
                                f"Automatically restored **{restored}** live rename(s); the old saved design remains authoritative."
                                if not residual
                                else f"Automatic restore left **{len(residual)}** row(s) changed. An {'durable' if durable else 'emergency memory-only'} Undo record was retained for them."
                            )
                        ),
                        color=discord.Color.orange(),
                    )
                    if rollback_failures:
                        embed.add_field(name="Restore attention", value="\n".join(f"• {line}" for line in rollback_failures[:8])[:1024], inline=False)
                    await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(emergency)))
                    return

            snapshot: dict[str, Any] | None = None
            snapshot_durable = False
            if result.applied:
                snapshot, snapshot_durable = await _store_snapshot_with_memory_fallback(
                    int(guild.id),
                    int(interaction.user.id),
                    result.applied,
                )

            legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]

        if mode == "style_change_separator":
            title = "✅ Channel Separator Applied & Saved"
            description = f"Changed **{len(result.applied)}** live channel name(s), left **{skipped}** reviewed skip(s) untouched, and saved **{legacy._separator_choice_label(payload.get('separator_id'))}** as the authoritative separator. Failed **0**."  # type: ignore[attr-defined]
        elif "consistency" in mode:
            title = "✅ Inconsistent Names Repaired"
            description = f"Changed **{len(result.applied)}** item(s). Left **{skipped}** reviewed skip(s) untouched. Failed **0**."
        else:
            title = "✅ Reviewed Design Applied"
            description = f"Changed **{len(result.applied)}** item(s). Left **{skipped}** reviewed skip(s) untouched. Failed **0**."
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green(),
        )
        if snapshot and snapshot_durable:
            embed.add_field(
                name="Undo ready",
                value="The previous names were saved durably for Undo.",
                inline=False,
            )
        elif snapshot:
            embed.add_field(
                name="⚠️ Undo is memory-only",
                value=(
                    "The design **stays applied**. Durable Undo storage was unavailable, so this Undo snapshot lasts only until the bot restarts. "
                    "Dank Design will not automatically revert a successful Apply just because Undo-history storage failed."
                ),
                inline=False,
            )
        await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(snapshot)))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:preview_back", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _return_from_reviewed_preview(interaction, self.pending_created_at)


class LegacyStyleChangePreviewView(ReviewedPreviewView):
    """Keep separator issue-review buttons while sharing the one Apply owner."""

    def __init__(self, *, can_apply: bool, has_blockers: bool = False, pending_created_at: float) -> None:
        super().__init__(can_apply=can_apply, pending_created_at=pending_created_at)
        if has_blockers:
            self.add_item(legacy.StyleChangeFixMissingEmojiButton(row=2, pending_created_at=pending_created_at))  # type: ignore[attr-defined]
            self.add_item(legacy.StyleChangeApplySafeOnlyButton(row=2, pending_created_at=pending_created_at))  # type: ignore[attr-defined]


def _install_legacy_compatibility_bridge() -> None:
    """Keep mature sub-editors inside the consolidated public workflow.

    This bridge changes only navigation/apply UI globals. It does not replace
    planning, config, registration, doctor logic, or service functions.
    """

    global _COMPATIBILITY_BRIDGE_INSTALLED
    if _COMPATIBILITY_BRIDGE_INSTALLED:
        return
    legacy._home_embed = _home_embed  # type: ignore[attr-defined]
    legacy.DesignHomeView = DesignHomeView  # type: ignore[attr-defined]
    legacy.DesignPreviewView = ReviewedPreviewView  # type: ignore[attr-defined]
    legacy.StyleChangePreviewView = LegacyStyleChangePreviewView  # type: ignore[attr-defined]
    _COMPATIBILITY_BRIDGE_INSTALLED = True


_install_legacy_compatibility_bridge()


async def open_design_studio(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    options = await _load_design_options(int(guild.id))
    await interaction.edit_original_response(
        embed=_home_embed(guild, options),
        view=DesignHomeView(options),
        allowed_mentions=discord.AllowedMentions.none(),
    )


__all__ = [
    "DesignHomeView",
    "DesignServerView",
    "DoneView",
    "EditOneItemView",
    "LegacyStyleChangePreviewView",
    "ReviewedPreviewView",
    "ReviewRepairView",
    "SavedRulesView",
    "UndoConfirmView",
    "_home_embed",
    "_install_legacy_compatibility_bridge",
    "_load_design_options",
    "_open_undo",
    "_require_design_permission",
    "open_design_studio",
]
