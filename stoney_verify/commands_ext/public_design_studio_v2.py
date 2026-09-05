from __future__ import annotations

"""Consolidated public Dank Design Studio.

The original Studio accumulated whole-server design, drift repair, exact item
editing, rule management, protection, diagnostics, and rollback on the same
screen. This module is the public workflow owner. The historical implementation
remains a compatibility backend for mature editor/apply primitives while users
enter through five explicit workflows.
"""

import asyncio
import time
from collections.abc import Mapping
from typing import Any

import discord

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.services import server_design_plan_service as plans

studio = legacy.studio
_PATCHED = False


def _safe_str(value: Any, default: str = "") -> str:
    return legacy._safe_str(value, default)  # type: ignore[attr-defined]


def _safe_int(value: Any, default: int = 0) -> int:
    return legacy._safe_int(value, default)  # type: ignore[attr-defined]


_require_design_permission = legacy._require_design_permission  # type: ignore[attr-defined]
_load_design_options = legacy._load_design_options  # type: ignore[attr-defined]


def _rule_counts(options: Mapping[str, Any]) -> dict[str, int]:
    return legacy._lock_count(options)  # type: ignore[attr-defined]


def _home_embed(guild: discord.Guild, options: Mapping[str, Any] | None = None) -> discord.Embed:
    options = options or {}
    theme = legacy._theme_from_options(options)  # type: ignore[attr-defined]
    counts = _rule_counts(options)
    embed = discord.Embed(
        title="🎨 Dank Design Studio",
        description=(
            "One job at a time. Pick what you actually want to do instead of mixing "
            "server styling, repairs, locks, and rollback on one dashboard.\n\n"
            "**Nothing renames a channel/category until a reviewed preview is applied.**"
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current saved server design",
        value=(
            f"Theme: **{getattr(theme, 'label', 'Gothic Clean')}**\n"
            f"Strength: **{_safe_int(options.get('strength'), 4)}/5**\n"
            f"Global rule: **{'On' if counts.get('global') else 'Off'}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Narrow saved rules",
        value=(
            f"Categories: **{counts.get('categories', 0)}**\n"
            f"Channels: **{counts.get('channels', 0)}**\n"
            f"Exact names: **{counts.get('manual_names', 0)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Workflows",
        value=(
            "🌐 **Design Server** — theme, strength, separator-only change, preview/apply.\n"
            "✏️ **Edit One Item** — category or channel editor.\n"
            "🩺 **Review / Repair** — audit drift and repair toward the live majority.\n"
            "🔐 **Saved Rules** — locks, exact names, protection, stale cleanup.\n"
            "↩️ **Rollback** — review and undo the last applied rename batch."
        ),
        inline=False,
    )
    embed.set_footer(text="Names only • Saved narrow rules win • Permissions/topics/order are never redesigned")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


async def _go_home(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    options = await _load_design_options(int(guild.id))
    await interaction.response.edit_message(embed=_home_embed(guild, options), view=DesignHomeView(options))


class HomeButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(label="Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:home", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await _go_home(interaction)


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
        view=ReviewedPreviewView(
            can_apply=not has_blockers and has_changes,
            pending_created_at=created_at,
        ),
    )


class DesignServerThemeSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        choices = []
        for theme in studio.THEMES[:25]:
            font = str(getattr(theme, "font", "normal") or "normal").replace("_", " ").title()
            frame = str(getattr(theme, "category_frame", "plain") or "plain").replace("_", " ").title()
            choices.append(discord.SelectOption(label=theme.label[:100], value=theme.id, default=theme.id == current, description=f"Font: {font} • Categories: {frame}"[:100]))
        super().__init__(placeholder="Choose the server theme", min_values=1, max_values=1, options=choices, row=0)

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
        1: ("1 — Icons", "Icon/base cleanup only."),
        2: ("2 — Layout", "Adds the selected channel separator."),
        3: ("3 — Font", "Adds the selected font style."),
        4: ("4 — Recommended", "Full theme including category frames."),
        5: ("5 — Exact", "Strictly normalizes the complete selected theme."),
    }

    def __init__(self, current: int) -> None:
        choices = [discord.SelectOption(label=label, value=str(value), default=value == current, description=description) for value, (label, description) in self.LABELS.items()]
        super().__init__(placeholder="Choose styling strength", min_values=1, max_values=1, options=choices, row=1)

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
        title="🌐 Design Server",
        description=(
            "Choose the reusable server design here. These controls save the desired rule, "
            "but **do not rename anything**. Preview the exact channel/category names before Apply."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Theme", value=f"**{getattr(theme, 'label', 'Gothic Clean')}**", inline=True)
    embed.add_field(name="Strength", value=f"**{_safe_int(options.get('strength'), 4)}/5**", inline=True)
    embed.add_field(
        name="What this can change",
        value="Visible channel/category names only. Permissions, roles, topics, order, ticket placement, slowmode, NSFW, and verification stay untouched.",
        inline=False,
    )
    embed.set_footer(text="Choose → Preview Server → Apply Reviewed Changes")
    return legacy._clean_design_embed(embed)  # type: ignore[attr-defined]


class DesignServerView(discord.ui.View):
    def __init__(self, options: Mapping[str, Any]) -> None:
        super().__init__(timeout=900)
        self.add_item(DesignServerThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(DesignServerStrengthSelect(_safe_int(options.get("strength"), 4)))

    @discord.ui.button(label="Preview Server", emoji="👁️", style=discord.ButtonStyle.success, custom_id="dank_design_v2:server_preview", row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_saved_design_plan(guild, options)
        await _store_preview(interaction, items, plan_options, mode="preview_server_v2", title="👁️ Server Design Preview")

    @discord.ui.button(label="Separator Only", emoji="⚡", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:separator_only", row=2)
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


class EditOneItemView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Category", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit_category", row=0)
    async def category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(embed=legacy._category_editor_embed(guild, page=0), view=legacy.CategoryEditorPickerView(guild, page=0))  # type: ignore[attr-defined]

    @discord.ui.button(label="Channel", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit_channel", row=0)
    async def channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(embed=legacy._channel_editor_embed(guild, page=0), view=legacy.ChannelEditorPickerView(guild, page=0))  # type: ignore[attr-defined]

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:edit_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


def _edit_one_embed() -> discord.Embed:
    embed = discord.Embed(
        title="✏️ Edit One Item",
        description=(
            "Choose the scope first. The detailed editor can rename immediately, create an exact style rule, "
            "or preview a repair for only that category/channel. Narrow rules stay above broader server rules."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Authority", value="Exact name → Channel rule → Category rule → Global rule → Server design", inline=False)
    return embed


def _review_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🩺 Review / Repair",
        description=(
            "Audit first, repair second. **Check Problems** uses your saved rules. **Review Name Drift** "
            "detects the live majority and builds an explicit repair plan while still respecting saved narrow rules."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Nothing is renamed until Apply Reviewed Changes")
    return embed


class ReviewRepairView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Check Problems", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:doctor", row=0)
    async def doctor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_saved_design_plan(guild, options)
        await interaction.edit_original_response(embed=legacy._doctor_embed(guild, plan_options, items), view=ReviewRepairView())  # type: ignore[attr-defined]

    @discord.ui.button(label="Review Name Drift", emoji="🧭", style=discord.ButtonStyle.success, custom_id="dank_design_v2:drift", row=0)
    async def drift(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items, plan_options, _analysis = await plans.build_drift_repair_plan(guild, options)
        created_at = legacy._store_pending(int(guild.id), int(interaction.user.id), {"items": items, "options": dict(plan_options), "mode": "consistency_check_v2"})  # type: ignore[attr-defined]
        has_blockers = any(item.get("status") == "failed" for item in items)
        has_changes = any(item.get("status") == "changed" for item in items)
        await interaction.edit_original_response(
            embed=legacy._consistency_embed(guild, items, plan_options),  # type: ignore[attr-defined]
            view=ReviewedPreviewView(can_apply=not has_blockers and has_changes, pending_created_at=created_at),
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:review_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


def _saved_rules_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    counts = _rule_counts(options)
    embed = discord.Embed(
        title="🔐 Saved Rules",
        description="Manage persistent design authority without mixing it into preview/repair controls.",
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
    embed.set_footer(text="Narrower rules always win; protection is evaluated separately")
    return embed


class SavedRulesView(discord.ui.View):
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


class DesignHomeView(discord.ui.View):
    """Five-workflow Studio hub. No style controls are mixed into the home."""

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Design Server", emoji="🌐", style=discord.ButtonStyle.success, custom_id="dank_design_v2:server", row=0)
    async def design_server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_design_server_embed(guild, options), view=DesignServerView(options))

    @discord.ui.button(label="Edit One Item", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:edit", row=0)
    async def edit_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_edit_one_embed(), view=EditOneItemView())

    @discord.ui.button(label="Review / Repair", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_design_v2:review", row=1)
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_review_embed(), view=ReviewRepairView())

    @discord.ui.button(label="Saved Rules", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:rules", row=1)
    async def rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_saved_rules_embed(guild, options), view=SavedRulesView())

    @discord.ui.button(label="Rollback", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design_v2:rollback", row=2)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await legacy._open_rollback(interaction)  # type: ignore[attr-defined]


class DoneView(discord.ui.View):
    def __init__(self, *, can_rollback: bool) -> None:
        super().__init__(timeout=900)
        self.rollback.disabled = not can_rollback

    @discord.ui.button(label="Rollback", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design_v2:done_rollback", row=0)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await legacy._open_rollback(interaction)  # type: ignore[attr-defined]

    @discord.ui.button(label="Back to Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:done_home", row=0)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


class ReviewedPreviewView(discord.ui.View):
    """Single apply path for every consolidated Studio preview."""

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
            await interaction.response.send_message("No reviewed preview is available.", ephemeral=True)
            return
        if any(item.get("status") == "failed" for item in items):
            await interaction.response.send_message("❌ This preview has blockers. Fix them and preview again.", ephemeral=True)
            return
        lock = legacy._lock_for(int(guild.id))  # type: ignore[attr-defined]
        if lock.locked():
            await interaction.response.send_message("⏳ A design job is already running for this server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        changed = 0
        skipped = 0
        failed: list[str] = []
        snapshot: list[dict[str, Any]] = []

        async with lock:
            for item in items:
                if item.get("status") != "changed":
                    skipped += 1
                    continue
                channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
                if channel is None:
                    failed.append(f"missing `{item.get('before')}`")
                    continue
                before = _safe_str(item.get("before"))
                after = _safe_str(item.get("after"))[: studio.DISCORD_NAME_LIMIT]
                current = _safe_str(getattr(channel, "name", ""))
                if current != before:
                    failed.append(f"stale `{before}` is now `{current}`")
                    continue
                try:
                    await channel.edit(name=after, reason=f"Dank Shield Server Design apply by {int(interaction.user.id)}")
                    changed += 1
                    snapshot.append({**item, "old_name": before, "new_name": after, "admin_id": str(int(interaction.user.id)), "timestamp": time.time(), "action_type": "apply"})
                    if changed % 5 == 0:
                        await interaction.edit_original_response(content=f"🚀 Applying reviewed design… {changed} changed, {skipped} skipped, {len(failed)} failed.")
                    await asyncio.sleep(studio.DEFAULT_DELAY_SECONDS)
                except Exception as exc:
                    failed.append(f"`{current}`: {type(exc).__name__}")

        if snapshot:
            snapshot_payload = {"created_at": time.time(), "items": snapshot, "admin_id": str(int(interaction.user.id))}
            guild_key = legacy._guild_key(int(guild.id))  # type: ignore[attr-defined]
            legacy._LAST_SNAPSHOTS.setdefault(guild_key, []).append(snapshot_payload)  # type: ignore[attr-defined]
            legacy._LAST_SNAPSHOTS[guild_key] = legacy._LAST_SNAPSHOTS[guild_key][-10:]  # type: ignore[attr-defined]
            await legacy._persist_rollback_snapshot(int(guild.id), snapshot_payload)  # type: ignore[attr-defined]

        legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]
        mode = _safe_str(payload.get("mode"), "preview")
        title = "✅ Name Drift Repaired" if "consistency" in mode else "✅ Server Design Applied"
        embed = discord.Embed(
            title=title,
            description=f"Changed **{changed}** item(s). Skipped **{skipped}**. Failed **{len(failed)}**.",
            color=discord.Color.green() if not failed else discord.Color.orange(),
        )
        if failed:
            embed.add_field(name="Skipped / Failed", value="\n".join(failed[:10])[:1024], inline=False)
        if snapshot:
            embed.add_field(name="Rollback ready", value="The previous names were saved before this apply.", inline=False)
        await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(snapshot)))

    @discord.ui.button(label="Back to Studio", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:preview_back", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _go_home(interaction)


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
            @dank_group.command(name="design", description="Open Dank Design Studio for channel/category name styling.")
            async def dank_design(interaction: discord.Interaction) -> None:
                await open_design_studio(interaction)
        _PATCHED = True
        return True
    except Exception:
        return False


__all__ = [
    "DesignHomeView",
    "ReviewedPreviewView",
    "_home_embed",
    "_load_design_options",
    "_require_design_permission",
    "open_design_studio",
    "register_public_design_studio_command",
]
