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

studio = legacy.studio
_PATCHED = False
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
            f"Strength: **{_safe_int(options.get('strength'), 4)}/5**\n"
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
            f"Protection overrides: **{counts.get('protection_items', 0)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Choose what you want to do",
        value=(
            "🌐 **Design Entire Server** — choose the reusable theme/strength, then preview exact names.\n"
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


def _compat_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧭 How Dank Design Works",
        description="There are no hidden apply rules. Pick the job that matches what you want to change.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Whole server / repair / custom style",
        value="Choose settings → Preview exact names → **Apply Reviewed Changes**. Nothing is renamed before that Apply button.",
        inline=False,
    )
    embed.add_field(
        name="One exact Rename",
        value="Inside Edit One Category / Channel, **Rename** is immediate and saves that exact name rule. The item screen warns you before you use it.",
        inline=False,
    )
    embed.add_field(
        name="Saved Rules & Protection",
        value="Rules change what future previews enforce. Saving or unlocking a rule does not rename Discord by itself.",
        inline=False,
    )
    embed.set_footer(text="Batch changes are transactional-style: preflight first, stop on error, compensate partial changes")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


async def _go_home(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    options = await _load_design_options(int(guild.id))
    await interaction.response.edit_message(embed=_home_embed(guild, options), view=DesignHomeView(options))


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
    await interaction.edit_original_response(
        embed=legacy._preview_embed(guild, items, title=title),  # type: ignore[attr-defined]
        view=ReviewedPreviewView(can_apply=not has_blockers and has_changes, pending_created_at=created_at),
    )


class DesignServerThemeSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        choices: list[discord.SelectOption] = []
        for theme in studio.THEMES[:25]:
            font = str(getattr(theme, "font", "normal") or "normal").replace("_", " ").title()
            frame = str(getattr(theme, "category_frame", "plain") or "plain").replace("_", " ").title()
            choices.append(
                discord.SelectOption(
                    label=theme.label[:100],
                    value=theme.id,
                    default=theme.id == current,
                    description=f"Font: {font} • Categories: {frame}"[:100],
                )
            )
        super().__init__(placeholder="1) Choose the server theme", min_values=1, max_values=1, options=choices, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        options["theme_id"] = self.values[0]
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.response.edit_message(embed=_design_server_embed(guild, options), view=DesignServerView(options))


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
        super().__init__(placeholder="2) Choose how much styling to use", min_values=1, max_values=1, options=choices, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        options["strength"] = max(1, min(5, _safe_int(self.values[0], 4)))
        legacy._sync_enabled_global_lock(options)  # type: ignore[attr-defined]
        await legacy._save_options(interaction, options)  # type: ignore[attr-defined]
        await interaction.response.edit_message(embed=_design_server_embed(guild, options), view=DesignServerView(options))


def _design_server_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
    embed = discord.Embed(
        title="🌐 Design Entire Server",
        description=(
            "Choose the reusable server design. Theme and strength are **saved as settings immediately**, "
            "but they do **not** rename a single Discord channel/category.\n\n"
            "When the settings look right, press **Preview Server Changes**, review the exact names, then Apply."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Theme", value=f"**{getattr(theme, 'label', 'Gothic Clean')}**", inline=True)
    embed.add_field(name="Strength", value=f"**{_safe_int(options.get('strength'), 4)}/5**", inline=True)
    embed.add_field(
        name="Batch safety",
        value="The whole batch is preflighted before the first rename. If anything is stale, **nothing is renamed** and you preview again.",
        inline=False,
    )
    embed.set_footer(text="Choose settings → Preview Server Changes → Apply Reviewed Changes")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


class DesignServerView(DesignView):
    def __init__(self, options: Mapping[str, Any]) -> None:
        super().__init__(timeout=900)
        self.add_item(DesignServerThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(DesignServerStrengthSelect(_safe_int(options.get("strength"), 4)))

    @discord.ui.button(label="Preview Server Changes", emoji="👁️", style=discord.ButtonStyle.success, custom_id="dank_design_v2:server_preview", row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_saved_design_plan(guild, options)
        await _store_preview(interaction, items, plan_options, mode="preview_server_v2", title="👁️ Server Design Preview")

    @discord.ui.button(label="Change Separators Only", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:separator_only", row=2)
    async def separator_only(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        _analysis, repair_options, _summary = legacy._infer_live_majority_context(guild, options)  # type: ignore[attr-defined]
        current = _safe_str(repair_options.get("separator_id"), "none")
        selected = "bar_heavy" if current == "none" else current
        await interaction.response.edit_message(
            embed=legacy._style_change_embed(guild, options, separator_id=selected),  # type: ignore[attr-defined]
            view=legacy.StyleChangeView(separator_id=selected),
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
        description="These settings control what **future previews** enforce. Saving, unlocking, or changing a rule here does **not** rename a Discord item by itself.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current rules",
        value=(
            f"Global: **{'On' if counts.get('global') else 'Off'}**\n"
            f"Category: **{counts.get('categories', 0)}**\n"
            f"Channel: **{counts.get('channels', 0)}**\n"
            f"Exact names: **{counts.get('manual_names', 0)}**\n"
            f"Exact protection: **{counts.get('protection_items', 0)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Which tool does what",
        value=(
            "**Layout Rules** = global/category/channel visual rules.\n"
            "**Unlock / Clean** = remove one saved rule or stale rule.\n"
            "**Protection** = decide which exact/default names automated styling may touch."
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
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=legacy._format_locks_embed(guild, options), view=legacy.FormatLocksView())  # type: ignore[attr-defined]

    @discord.ui.button(label="Unlock / Clean", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:unlock", row=0)
    async def unlock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=legacy._format_lock_manager_embed(guild, options, page=0), view=legacy.LockManagerView(guild, options, page=0))  # type: ignore[attr-defined]

    @discord.ui.button(label="Protection", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:protection", row=1)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=legacy._protection_manager_embed(guild, options), view=legacy.ProtectionManagerView())  # type: ignore[attr-defined]

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
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_design_server_embed(guild, options), view=DesignServerView(options))

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
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_saved_rules_embed(guild, options), view=SavedRulesView())

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


async def _store_residual_snapshot(guild_id: int, user_id: int, prepared: list[apply_service.PreparedRename]) -> tuple[dict[str, Any] | None, bool]:
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
        return payload, False


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
    popped = await legacy._pop_latest_rollback_snapshot(int(guild.id))  # type: ignore[attr-defined]
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
    latest = await legacy._latest_rollback_snapshot(int(guild.id))  # type: ignore[attr-defined]
    if not latest:
        await interaction.response.send_message("No applied Dank Design batch is available to undo.", ephemeral=True)
        return
    created_at = _safe_float(latest.get("created_at"), 0.0)
    await interaction.response.edit_message(embed=_undo_preview_embed(latest), view=UndoConfirmView(snapshot_created_at=created_at))


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

        latest = await legacy._latest_rollback_snapshot(int(guild.id))  # type: ignore[attr-defined]
        if not _snapshot_matches(latest, self.snapshot_created_at):
            await interaction.response.send_message("❌ This Undo preview is obsolete. Open Undo Last Apply again.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
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

            snapshot: dict[str, Any] | None = None
            if result.applied:
                try:
                    snapshot = await _store_durable_snapshot(int(guild.id), int(interaction.user.id), result.applied)
                except Exception as snapshot_exc:
                    restored, residual, rollback_failures = await apply_service.compensate_applied(
                        guild,
                        result.applied,
                        user_id=int(interaction.user.id),
                        delay_seconds=studio.DEFAULT_DELAY_SECONDS,
                    )
                    emergency, durable = await _store_residual_snapshot(int(guild.id), int(interaction.user.id), residual)
                    legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]
                    embed = discord.Embed(
                        title="⚠️ Apply Reversed Because Undo History Could Not Be Saved",
                        description=(
                            f"Durable Undo history failed with **{type(snapshot_exc).__name__}**. Dank Design did not silently leave an unprotected batch. "
                            + (
                                f"Automatically restored **{restored}** rename(s); no applied design was left behind."
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

            legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]

        title = "✅ Inconsistent Names Repaired" if "consistency" in mode else "✅ Reviewed Design Applied"
        embed = discord.Embed(
            title=title,
            description=f"Changed **{len(result.applied)}** item(s). Left **{skipped}** reviewed skip(s) untouched. Failed **0**.",
            color=discord.Color.green(),
        )
        if snapshot:
            embed.add_field(name="Undo ready", value="The previous names were saved durably before this Apply was finalized.", inline=False)
        await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(snapshot)))

    @discord.ui.button(label="Back to Studio", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:preview_back", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


class LegacyStyleChangePreviewView(ReviewedPreviewView):
    """Keep separator issue-review buttons while sharing the one Apply owner."""

    def __init__(self, *, can_apply: bool, has_blockers: bool = False, pending_created_at: float) -> None:
        super().__init__(can_apply=can_apply, pending_created_at=pending_created_at)
        if has_blockers:
            self.add_item(legacy.StyleChangeFixMissingEmojiButton(row=2, pending_created_at=pending_created_at))  # type: ignore[attr-defined]
            self.add_item(legacy.StyleChangeApplySafeOnlyButton(row=2, pending_created_at=pending_created_at))  # type: ignore[attr-defined]


def _install_legacy_compatibility_bridge() -> None:
    """Keep mature sub-editors inside the consolidated public workflow.

    This bridge changes only navigation/help/apply UI globals. It does not replace
    planning, config, registration, doctor logic, or service functions.
    """

    global _COMPATIBILITY_BRIDGE_INSTALLED
    if _COMPATIBILITY_BRIDGE_INSTALLED:
        return
    legacy._home_embed = _home_embed  # type: ignore[attr-defined]
    legacy._start_here_embed = _compat_help_embed  # type: ignore[attr-defined]
    legacy._design_help_embed = _compat_help_embed  # type: ignore[attr-defined]
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
    options = await _load_design_options(int(guild.id))
    await interaction.response.send_message(
        embed=_home_embed(guild, options),
        view=DesignHomeView(options),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def register_public_design_studio_command(bot: Any = None, tree: Any = None) -> bool:
    """Compatibility registrar; normal ownership is public_design_group."""

    global _PATCHED
    if _PATCHED:
        return True
    try:
        import stoney_verify.commands_ext as commands_ext
        from stoney_verify.commands_ext.public_setup_group import dank_group

        allowed = set(getattr(commands_ext, "_ALLOWED_DANK_CHILDREN", set()) or set())
        allowed.add("design")
        commands_ext._ALLOWED_DANK_CHILDREN = allowed
        if dank_group.get_command("design") is None:
            @dank_group.command(name="design", description="Open Dank Design Studio for safe channel/category name styling.")
            async def dank_design(interaction: discord.Interaction) -> None:
                await open_design_studio(interaction)
        _PATCHED = True
        return True
    except Exception:
        return False


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
    "_compat_help_embed",
    "_home_embed",
    "_install_legacy_compatibility_bridge",
    "_load_design_options",
    "_open_undo",
    "_require_design_permission",
    "open_design_studio",
    "register_public_design_studio_command",
]
