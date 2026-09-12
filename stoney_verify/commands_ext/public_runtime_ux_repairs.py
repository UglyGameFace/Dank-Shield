from __future__ import annotations

"""Late public-runtime UX repairs for setup and Dank Design.

This module intentionally patches presentation/navigation only after the canonical
setup and design owners have loaded. It does not register commands or replace
planning/apply services.
"""

import time
from collections.abc import Mapping
from typing import Any

import discord

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as design_v2
from stoney_verify.commands_ext import public_setup_recommend as setup
from stoney_verify.setup_ui import public_setup_compact as compact_ui

_PATCHED = False
_EDITOR_CONTEXT_TTL = 30 * 60
_EDITOR_CONTEXT: dict[tuple[int, int, int], tuple[float, int, int | None]] = {}

_ORIGINAL_CHANNEL_ACTION_VIEW = legacy.ChannelEditorActionView
_ORIGINAL_REVIEWED_PREVIEW_VIEW = design_v2.ReviewedPreviewView
_ORIGINAL_LEGACY_STYLE_PREVIEW_VIEW = design_v2.LegacyStyleChangePreviewView
_ORIGINAL_PREVIEW_EMBED = legacy._preview_embed
_ORIGINAL_CHANNEL_ACTION_EMBED = legacy._channel_action_embed
_ORIGINAL_COMPACT_ADVANCED_VIEW = compact_ui.CompactAdvancedView


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _context_key(interaction: discord.Interaction, channel_id: int) -> tuple[int, int, int] | None:
    guild = interaction.guild
    user = interaction.user
    if guild is None or user is None:
        return None
    return int(guild.id), int(user.id), int(channel_id)


def _remember_editor_context(
    interaction: discord.Interaction,
    *,
    channel_id: int,
    page: int,
    category_filter_id: int | None,
) -> None:
    key = _context_key(interaction, channel_id)
    if key is None:
        return
    now = time.monotonic()
    _EDITOR_CONTEXT[key] = (now, int(page), int(category_filter_id) if category_filter_id is not None else None)
    if len(_EDITOR_CONTEXT) > 512:
        cutoff = now - _EDITOR_CONTEXT_TTL
        for old_key, record in list(_EDITOR_CONTEXT.items()):
            if record[0] < cutoff:
                _EDITOR_CONTEXT.pop(old_key, None)


def _editor_context(
    interaction: discord.Interaction,
    *,
    channel_id: int,
    fallback_category_id: int | None,
) -> tuple[int, int | None]:
    key = _context_key(interaction, channel_id)
    record = _EDITOR_CONTEXT.get(key) if key is not None else None
    if record is not None:
        created_at, page, category_filter_id = record
        if time.monotonic() - created_at <= _EDITOR_CONTEXT_TTL:
            return max(0, int(page)), category_filter_id
        if key is not None:
            _EDITOR_CONTEXT.pop(key, None)
    return 0, int(fallback_category_id) if fallback_category_id is not None else None


def _channel_page_data(
    guild: discord.Guild,
    *,
    page: int,
    category_id: int | None,
) -> tuple[int, int, list[Any], int | None]:
    if category_id is not None:
        source = legacy._category_channels(guild, int(category_id))
        total_pages = max(1, (len(source) + legacy.EDITOR_PAGE_SIZE - 1) // legacy.EDITOR_PAGE_SIZE)
        page = max(0, min(int(page), total_pages - 1))
        start = page * legacy.EDITOR_PAGE_SIZE
        return page, total_pages, source[start:start + legacy.EDITOR_PAGE_SIZE], int(category_id)

    groups = legacy._channel_editor_groups(guild)
    total_pages = max(1, len(groups))
    page = max(0, min(int(page), total_pages - 1))
    group = groups[page]
    active_category_id = _safe_int(group.get("category_id"), 0) or None
    return page, total_pages, list(group.get("channels") or []), active_category_id


class ChannelPageJumpSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild, *, page: int, category_id: int | None) -> None:
        current_page, total_pages, _chunk, _active_category = _channel_page_data(
            guild,
            page=page,
            category_id=category_id,
        )
        window_start = max(0, min(current_page - 12, max(0, total_pages - 25)))
        window_end = min(total_pages, window_start + 25)
        groups = legacy._channel_editor_groups(guild) if category_id is None else []
        options: list[discord.SelectOption] = []
        for index in range(window_start, window_end):
            if category_id is None and index < len(groups):
                group = groups[index]
                label = str(group.get("label") or "No Category")
                part = _safe_int(group.get("part"), 1)
                parts = _safe_int(group.get("parts"), 1)
                suffix = f" · part {part}/{parts}" if parts > 1 else ""
                description = f"{label}{suffix}"[:100]
            else:
                description = f"Channels page {index + 1} of {total_pages}"
            options.append(
                discord.SelectOption(
                    label=f"Page {index + 1} of {total_pages}",
                    value=str(index),
                    description=description,
                    default=index == current_page,
                )
            )
        super().__init__(
            placeholder=f"Jump to channel page… ({current_page + 1}/{total_pages})",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank_design:channel_page_jump",
            row=3,
        )
        self.category_id = int(category_id) if category_id is not None else None

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await legacy._require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        page = max(0, _safe_int(self.values[0], 0))
        await interaction.response.edit_message(
            embed=legacy._channel_editor_embed(guild, page=page, category_id=self.category_id),
            view=legacy.ChannelEditorPickerView(guild, page=page, category_id=self.category_id),
        )


class ContextAwareChannelPickButton(discord.ui.Button):
    def __init__(
        self,
        channel: discord.abc.GuildChannel,
        *,
        display_index: int,
        row: int,
        page: int,
        category_filter_id: int | None,
    ) -> None:
        super().__init__(
            label=f"{display_index}. {legacy._short_label(getattr(channel, 'name', 'Channel'), 54)}",
            emoji={"category": "🗂️", "voice": "🔊", "text": "#️⃣", "forum": "💬", "stage": "🎙️"}.get(legacy._kind(channel), "#️⃣"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_design:pick_channel:{int(channel.id)}",
            row=row,
        )
        self.channel_id = int(channel.id)
        self.page = int(page)
        self.category_filter_id = int(category_filter_id) if category_filter_id is not None else None

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await legacy._require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            await interaction.response.send_message("That channel no longer exists.", ephemeral=True)
            return
        _remember_editor_context(
            interaction,
            channel_id=self.channel_id,
            page=self.page,
            category_filter_id=self.category_filter_id,
        )
        parent = getattr(channel, "category", None)
        parent_id = _safe_int(getattr(parent, "id", 0), 0) or None
        await interaction.response.edit_message(
            embed=legacy._channel_action_embed(channel),
            view=legacy.ChannelEditorActionView(self.channel_id, category_id=parent_id),
        )


class ContextAwareChannelEditorPickerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, *, page: int = 0, category_id: int | None = None) -> None:
        super().__init__(timeout=900)
        page, total_pages, chunk, active_category_id = _channel_page_data(
            guild,
            page=page,
            category_id=category_id,
        )

        for offset, channel in enumerate(chunk):
            self.add_item(
                ContextAwareChannelPickButton(
                    channel,
                    display_index=offset + 1,
                    row=offset // 2,
                    page=page,
                    category_filter_id=category_id,
                )
            )

        if total_pages > 1:
            self.add_item(ChannelPageJumpSelect(guild, page=page, category_id=category_id))

        nav_row = 4
        if active_category_id is not None:
            self.add_item(legacy.EditCategoryFromChannelEditorButton(active_category_id, row=nav_row))
        if page > 0:
            self.add_item(legacy.ChannelPageButton(page - 1, label="Prev", emoji="⬅️", row=nav_row, category_id=category_id))
        if page < total_pages - 1:
            self.add_item(legacy.ChannelPageButton(page + 1, label="Next", emoji="➡️", row=nav_row, category_id=category_id))
        if category_id is not None:
            self.add_item(legacy.BackToCategoryButton(int(category_id), row=nav_row))
        else:
            self.add_item(legacy.BackToDesignButton(row=nav_row))


class ContextAwareChannelEditorActionView(_ORIGINAL_CHANNEL_ACTION_VIEW):
    def __init__(self, channel_id: int, *, category_id: int | None = None) -> None:
        super().__init__(channel_id, category_id=category_id)
        for child in self.children:
            custom_id = str(getattr(child, "custom_id", "") or "")
            if custom_id == "dank_design:channel_action_back":
                child.callback = self._back_to_editor
            elif custom_id == "dank_design:channel_protection_mode":
                child.label = "Protection / Skip Rule"

        icon_button = discord.ui.Button(
            label="Change Icon / Emoji",
            emoji="😀",
            style=discord.ButtonStyle.primary,
            custom_id="dank_design:channel_change_icon",
            row=2,
        )
        icon_button.callback = self._change_icon
        self.add_item(icon_button)

    async def _change_icon(self, interaction: discord.Interaction) -> None:
        if not await legacy._require_design_permission(interaction):
            return
        await interaction.response.send_modal(
            legacy.CustomEmojiModal(scope="channel", target_id=self.channel_id)
        )

    async def _back_to_editor(self, interaction: discord.Interaction) -> None:
        if not await legacy._require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        page, category_filter_id = _editor_context(
            interaction,
            channel_id=self.channel_id,
            fallback_category_id=self.category_id,
        )
        await interaction.response.edit_message(
            embed=legacy._channel_editor_embed(guild, page=page, category_id=category_filter_id),
            view=legacy.ChannelEditorPickerView(guild, page=page, category_id=category_filter_id),
        )


def _protected_item(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().lower()
    if status == "protected":
        return True
    text = " ".join(
        str(item.get(key) or "")
        for key in ("reason", "note", "notes", "message", "warning", "detail")
    ).lower()
    return "protect" in text or "safe skip" in text


def _protected_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if _protected_item(item)]


def _protected_items_embed(items: list[dict[str, Any]]) -> discord.Embed:
    lines: list[str] = []
    for index, item in enumerate(items[:6], start=1):
        before = str(item.get("before") or item.get("name") or f"Item {index}")
        reason = str(item.get("reason") or item.get("note") or "Protected by the current design safety rule.")
        lines.append(f"**{index}.** `{before}`\n{reason[:180]}")
    embed = discord.Embed(
        title="🛡️ Protected / Skipped Design Items",
        description=(
            "These items were skipped on purpose, but the override is no longer hidden. "
            "Choose one item to edit its exact protection rule, or explicitly allow full styling for every item listed in this preview.\n\n"
            "After changing protection, rebuild the design preview so the new names are reviewed before Apply."
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(name=f"Protected items ({len(items)})", value="\n\n".join(lines)[:1024] or "None.", inline=False)
    embed.set_footer(text="Protection only controls Dank Design name styling; it does not change channel permissions or placement.")
    return legacy._clean_design_embed(embed)


class ProtectedItemButton(discord.ui.Button):
    def __init__(self, item: Mapping[str, Any], *, display_index: int, row: int) -> None:
        channel_id = _safe_int(item.get("channel_id"), 0)
        label = str(item.get("before") or item.get("name") or f"Item {display_index}")
        super().__init__(
            label=f"{display_index}. {legacy._short_label(label, 50)}",
            emoji="🛡️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_design:protected_item:{channel_id}",
            row=row,
            disabled=channel_id <= 0,
        )
        self.channel_id = channel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await legacy._require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        target = guild.get_channel(self.channel_id)
        if target is None:
            await interaction.response.send_message("That protected item no longer exists.", ephemeral=True)
            return
        if isinstance(target, discord.CategoryChannel):
            embed = legacy._category_action_embed(target)
            embed.title = "🛡️ Protected Category"
            view = legacy.CategoryEditorActionView(self.channel_id)
        else:
            embed = legacy._channel_action_embed(target)
            embed.title = "🛡️ Protected Channel"
            parent = getattr(target, "category", None)
            view = legacy.ChannelEditorActionView(
                self.channel_id,
                category_id=_safe_int(getattr(parent, "id", 0), 0) or None,
            )
        embed.add_field(
            name="How to allow this item",
            value="Choose **Protection / Skip Rule**, then select **Full styling**. Rebuild the preview afterward.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class ProtectedItemsView(discord.ui.View):
    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__(timeout=900)
        self.items = list(items)
        for offset, item in enumerate(self.items[:6]):
            self.add_item(ProtectedItemButton(item, display_index=offset + 1, row=offset // 2))

        allow_all = discord.ui.Button(
            label="Allow Full Styling for Listed",
            emoji="🔓",
            style=discord.ButtonStyle.primary,
            custom_id="dank_design:allow_listed_protected",
            row=3,
        )
        allow_all.callback = self._allow_all
        self.add_item(allow_all)

        back = discord.ui.Button(
            label="Back to Studio",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:protected_back",
            row=4,
        )
        back.callback = self._back
        self.add_item(back)

    async def _allow_all(self, interaction: discord.Interaction) -> None:
        if not await legacy._require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await legacy._load_design_options(int(guild.id))
        raw_rules = options.get("protection_item_rules")
        rules = dict(raw_rules) if isinstance(raw_rules, Mapping) else {}
        changed = 0
        for item in self.items:
            channel_id = _safe_int(item.get("channel_id"), 0)
            if channel_id <= 0:
                continue
            key = str(channel_id)
            if rules.get(key) != "full":
                rules[key] = "full"
                changed += 1
        options["protection_item_rules"] = rules
        await legacy._save_options(interaction, options)
        embed = discord.Embed(
            title="🔓 Protected Items Can Now Be Styled",
            description=(
                f"Saved **{changed}** exact-item override(s) as **Full styling**. "
                "No channel names were changed yet. Rebuild the preview and review the exact results before Apply."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(embed=embed, view=design_v2.DesignHomeView(options))

    async def _back(self, interaction: discord.Interaction) -> None:
        await design_v2._go_home(interaction)


async def _open_protected_items(interaction: discord.Interaction, pending_created_at: float | None) -> None:
    if not await legacy._require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    key = legacy._key(int(guild.id), int(interaction.user.id))
    payload = legacy._PENDING.get(key) or {}
    if not legacy._pending_matches(payload, pending_created_at):
        await interaction.response.send_message("❌ This preview is obsolete. Build a fresh preview first.", ephemeral=True)
        return
    items = _protected_items(list(payload.get("items") or []))
    if not items:
        await interaction.response.send_message("No protected/skipped design items are present in this preview.", ephemeral=True)
        return
    await interaction.response.edit_message(
        embed=_protected_items_embed(items),
        view=ProtectedItemsView(items),
    )


def _add_protection_review_button(view: discord.ui.View, pending_created_at: float | None) -> None:
    if any(str(getattr(child, "custom_id", "")) == "dank_design_v2:preview_protection" for child in view.children):
        return
    button = discord.ui.Button(
        label="Review Protected Items",
        emoji="🛡️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_design_v2:preview_protection",
        row=1,
    )

    async def callback(interaction: discord.Interaction) -> None:
        await _open_protected_items(interaction, pending_created_at)

    button.callback = callback
    view.add_item(button)


class ReviewedPreviewWithProtection(_ORIGINAL_REVIEWED_PREVIEW_VIEW):
    def __init__(self, *, can_apply: bool, pending_created_at: float | None = None) -> None:
        super().__init__(can_apply=can_apply, pending_created_at=pending_created_at)
        _add_protection_review_button(self, pending_created_at)


class LegacyStylePreviewWithProtection(_ORIGINAL_LEGACY_STYLE_PREVIEW_VIEW):
    def __init__(self, *, can_apply: bool, has_blockers: bool = False, pending_created_at: float) -> None:
        super().__init__(can_apply=can_apply, has_blockers=has_blockers, pending_created_at=pending_created_at)
        _add_protection_review_button(self, pending_created_at)


def _preview_embed_with_protection(guild: discord.Guild, items: list[dict[str, Any]], *args: Any, **kwargs: Any) -> discord.Embed:
    embed = _ORIGINAL_PREVIEW_EMBED(guild, items, *args, **kwargs)
    protected = _protected_items(list(items))
    if protected:
        embed.add_field(
            name="Want the protected items changed too?",
            value=(
                f"**{len(protected)}** item(s) are protected/skipped. Use **Review Protected Items** below to see exactly which ones. "
                "You can override one item or explicitly allow full styling for the listed items, then rebuild the preview."
            ),
            inline=False,
        )
    return legacy._clean_design_embed(embed)


def _channel_action_embed_with_icon(channel: discord.abc.GuildChannel) -> discord.Embed:
    embed = _ORIGINAL_CHANNEL_ACTION_EMBED(channel)
    embed.add_field(
        name="Icon / emoji",
        value="Use **Change Icon / Emoji** to replace or clear this channel's design icon after setup. Save the rule and preview before applying it.",
        inline=False,
    )
    return legacy._clean_design_embed(embed)


class CompactAdvancedWithoutDuplicateFeatures(_ORIGINAL_COMPACT_ADVANCED_VIEW):
    def __init__(self) -> None:
        super().__init__()
        for child in list(self.children):
            if str(getattr(child, "custom_id", "")) == "dank_setup_advanced:features":
                self.remove_item(child)


def apply_runtime_ux_repairs() -> None:
    global _PATCHED
    if _PATCHED:
        return

    # Six items leaves row 3 free for a direct page selector and row 4 for
    # category/prev/next/back controls. Embed pagination uses the same constant.
    legacy.EDITOR_PAGE_SIZE = 6
    legacy.ChannelEditorPickerView = ContextAwareChannelEditorPickerView
    legacy.ChannelEditorActionView = ContextAwareChannelEditorActionView
    legacy._preview_embed = _preview_embed_with_protection
    legacy._channel_action_embed = _channel_action_embed_with_icon

    design_v2.ReviewedPreviewView = ReviewedPreviewWithProtection
    design_v2.LegacyStyleChangePreviewView = LegacyStylePreviewWithProtection
    legacy.DesignPreviewView = ReviewedPreviewWithProtection
    legacy.StyleChangePreviewView = LegacyStylePreviewWithProtection

    compact_ui.CompactAdvancedView = CompactAdvancedWithoutDuplicateFeatures

    _PATCHED = True
    print("✅ public_runtime_ux_repairs active; setup duplicate removed, design pages/protection/icons repaired")


__all__ = [
    "CompactAdvancedWithoutDuplicateFeatures",
    "ContextAwareChannelEditorActionView",
    "ContextAwareChannelEditorPickerView",
    "ProtectedItemsView",
    "ReviewedPreviewWithProtection",
    "apply_runtime_ux_repairs",
]
