from __future__ import annotations

"""First-class /dank home Server Stats studio.

This module owns presentation and per-guild display preferences. The counters,
ownership, repair, and Discord-channel lifecycle remain authoritative in
stoney_verify.security_stats.
"""

from typing import Any, Mapping, Optional

import discord

from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
from ..security_stats import (
    DEFAULT_SECURITY_STATS_LABELS,
    DEFAULT_SECURITY_STATS_VISIBLE_KEYS,
    SECURITY_STATS_CATEGORY_ID_KEY,
    SECURITY_STATS_CATEGORY_NAME,
    SECURITY_STATS_CATEGORY_NAME_KEY,
    SECURITY_STATS_CUSTOM_LABELS_KEY,
    SECURITY_STATS_ENABLED_KEY,
    SECURITY_STATS_NUMBER_STYLE_KEY,
    SECURITY_STATS_PLACEMENT_KEY,
    SECURITY_STATS_VISIBLE_KEYS_KEY,
    disable_security_stats_display,
    ensure_security_stats_display,
    refresh_security_stats_display,
    security_stats_preferences,
)
from .public_setup_group import _require_setup_permission


_METRIC_TITLES = {
    "status": "SpamGuard status",
    "members": "Member count",
    "spam_blocked": "Spam blocked",
    "invites_blocked": "Invites blocked",
    "timeouts_issued": "Timeouts issued",
    "quarantines": "Quarantined",
    "open_tickets": "Open tickets",
    "claimed_tickets": "Claimed tickets",
    "closed_tickets": "Closed tickets",
}


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    try:
        raw = cfg.get(key, default)
    except Exception:
        raw = default
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _tracked_category(guild: discord.Guild, cfg: Any) -> Optional[discord.CategoryChannel]:
    try:
        category_id = int(str(cfg.get(SECURITY_STATS_CATEGORY_ID_KEY, "0") or "0"))
    except Exception:
        category_id = 0
    if category_id <= 0:
        return None
    channel = guild.get_channel(category_id)
    if isinstance(channel, discord.CategoryChannel):
        return channel
    return None


def _center_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    prefs = security_stats_preferences(cfg)
    enabled = _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False)
    category = _tracked_category(guild, cfg)
    visible = tuple(prefs["visible_keys"])
    custom_labels = dict(prefs["labels"])

    if enabled and category is not None:
        health = f"✅ Active • <#{int(category.id)}>"
        color = discord.Color.green()
    elif enabled:
        health = "🟡 Enabled • display missing, **Repair Display** will recreate it"
        color = discord.Color.gold()
    else:
        health = "⚪ Disabled"
        color = discord.Color.blurple()

    embed = discord.Embed(
        title="📊 Dank Shield Server Stats",
        description=(
            "Build the live locked voice-channel counters your server actually wants. "
            "Every option below is saved per server and uses the same owned display that "
            "the protection/ticket counters update."
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Status", value=health, inline=False)
    embed.add_field(
        name="Display",
        value=(
            f"**Category:** {prefs['category_name']}\n"
            f"**Placement:** {str(prefs['placement']).title()}\n"
            f"**Numbers:** {str(prefs['number_style']).title()}\n"
            f"**Visible counters:** {len(visible)}/{len(DEFAULT_SECURITY_STATS_VISIBLE_KEYS)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Visible counters",
        value=" • ".join(_METRIC_TITLES[key] for key in visible) or "None",
        inline=False,
    )
    embed.add_field(
        name="Customization",
        value=(
            f"**Custom labels:** {len(custom_labels)}\n"
            "Use **Choose Visible Counters** to hide anything you do not want. "
            "Use **Customize a Label** for custom wording or emoji. Hidden owned counters "
            "are removed instead of lingering as stale channels."
        ),
        inline=False,
    )
    embed.add_field(
        name="Repair behavior",
        value=(
            "If an enabled stats category is deleted, refresh now repairs it instead of silently giving up. "
            "The saved category name, labels, visible counters, placement, and number style are reapplied."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield Server Stats • per-server settings • no hardcoded home-server layout")
    return embed


async def _save_preferences(guild_id: int, updates: Mapping[str, Any]) -> Any:
    saved = await upsert_guild_config(int(guild_id), dict(updates))
    invalidate_guild_config(int(guild_id))
    return saved


async def _render_center(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
) -> None:
    guild = interaction.guild
    if guild is None:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Use Server Stats inside a server.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Use Server Stats inside a server.", ephemeral=True)
        return

    cfg = await get_guild_config(int(guild.id), refresh=True)
    embed = _center_embed(guild, cfg)
    view = ServerStatsView(owner_id=int(interaction.user.id), cfg=cfg)

    if not interaction.response.is_done():
        await interaction.response.edit_message(
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    await interaction.edit_original_response(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def open_server_stats_center(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await _render_center(interaction)


class CategoryNameModal(discord.ui.Modal):
    def __init__(self, *, current: str, owner_id: int) -> None:
        super().__init__(title="Server Stats Category")
        self.owner_id = int(owner_id)
        self.category_name = discord.ui.TextInput(
            label="Category name",
            placeholder=SECURITY_STATS_CATEGORY_NAME,
            default=str(current or SECURITY_STATS_CATEGORY_NAME)[:100],
            max_length=100,
            required=False,
        )
        self.add_item(self.category_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("❌ Open your own Server Stats panel.", ephemeral=True)
            return
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
            return

        # Modal submits should update the panel that launched them instead of
        # spawning a second stale copy of the controls.
        await interaction.response.defer()
        value = " ".join(str(self.category_name.value or "").replace("\n", " ").split()).strip()
        await _save_preferences(
            int(guild.id),
            {SECURITY_STATS_CATEGORY_NAME_KEY: value or SECURITY_STATS_CATEGORY_NAME},
        )
        cfg = await get_guild_config(int(guild.id), refresh=True)
        note = "✅ Server Stats category name saved."
        if _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False):
            ok, result = await ensure_security_stats_display(guild)
            note = result
        await _render_center(interaction, content=note)


class StatLabelModal(discord.ui.Modal):
    def __init__(self, *, key: str, current: str, owner_id: int) -> None:
        self.key = str(key)
        self.owner_id = int(owner_id)
        super().__init__(title=f"Customize {_METRIC_TITLES[self.key]}"[:45])
        self.label_text = discord.ui.TextInput(
            label="Channel label / emoji",
            placeholder=DEFAULT_SECURITY_STATS_LABELS[self.key],
            default=str(current or DEFAULT_SECURITY_STATS_LABELS[self.key])[:72],
            max_length=72,
            required=False,
        )
        self.add_item(self.label_text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("❌ Open your own Server Stats panel.", ephemeral=True)
            return
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
            return

        # Modal submits should update the panel that launched them instead of
        # spawning a second stale copy of the controls.
        await interaction.response.defer()
        cfg = await get_guild_config(int(guild.id), refresh=True)
        prefs = security_stats_preferences(cfg)
        labels = dict(prefs["labels"])
        value = " ".join(str(self.label_text.value or "").replace("\n", " ").split()).strip().rstrip(":").strip()
        if not value or value == DEFAULT_SECURITY_STATS_LABELS[self.key]:
            labels.pop(self.key, None)
        else:
            labels[self.key] = value[:72]

        await _save_preferences(
            int(guild.id),
            {SECURITY_STATS_CUSTOM_LABELS_KEY: labels},
        )
        cfg = await get_guild_config(int(guild.id), refresh=True)
        note = f"✅ **{_METRIC_TITLES[self.key]}** label saved."
        if _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False):
            ok, result = await ensure_security_stats_display(guild)
            note = result
        await _render_center(interaction, content=note)


class VisibleStatsSelect(discord.ui.Select):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        self.owner_id = int(owner_id)
        prefs = security_stats_preferences(cfg)
        current = set(prefs["visible_keys"])
        options = [
            discord.SelectOption(
                label=_METRIC_TITLES[key],
                value=key,
                default=key in current,
                description=f"Show {_METRIC_TITLES[key].lower()} in the stats category."[:100],
            )
            for key in DEFAULT_SECURITY_STATS_VISIBLE_KEYS
        ]
        super().__init__(
            placeholder="Choose Visible Counters",
            min_values=1,
            max_values=len(options),
            options=options,
            row=1,
            custom_id="dank_server_stats:visible",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("❌ Open your own Server Stats panel.", ephemeral=True)
            return
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
            return

        await interaction.response.defer()
        chosen = [key for key in DEFAULT_SECURITY_STATS_VISIBLE_KEYS if key in set(self.values)]
        await _save_preferences(int(guild.id), {SECURITY_STATS_VISIBLE_KEYS_KEY: chosen})
        cfg = await get_guild_config(int(guild.id), refresh=True)
        note = f"✅ Showing **{len(chosen)}** Server Stats counters."
        if _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False):
            ok, result = await ensure_security_stats_display(guild)
            note = result
        await _render_center(interaction, content=note)


class StatLabelSelect(discord.ui.Select):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        self.owner_id = int(owner_id)
        prefs = security_stats_preferences(cfg)
        labels = dict(prefs["labels"])
        options = [
            discord.SelectOption(
                label=_METRIC_TITLES[key],
                value=key,
                description=str(labels.get(key) or DEFAULT_SECURITY_STATS_LABELS[key])[:100],
            )
            for key in DEFAULT_SECURITY_STATS_VISIBLE_KEYS
        ]
        super().__init__(
            placeholder="Customize a Label",
            min_values=1,
            max_values=1,
            options=options,
            row=2,
            custom_id="dank_server_stats:label",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("❌ Open your own Server Stats panel.", ephemeral=True)
            return
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
            return
        cfg = await get_guild_config(int(guild.id), refresh=True)
        prefs = security_stats_preferences(cfg)
        key = str(self.values[0])
        current = str(dict(prefs["labels"]).get(key) or DEFAULT_SECURITY_STATS_LABELS[key])
        await interaction.response.send_modal(
            StatLabelModal(key=key, current=current, owner_id=self.owner_id)
        )


class ServerStatsView(discord.ui.View):
    def __init__(self, *, owner_id: int, cfg: Any) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.cfg = cfg
        self.prefs = security_stats_preferences(cfg)
        self.enabled = _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False)
        self.add_item(VisibleStatsSelect(owner_id=self.owner_id, cfg=cfg))
        self.add_item(StatLabelSelect(owner_id=self.owner_id, cfg=cfg))

        for child in self.children:
            custom_id = str(getattr(child, "custom_id", "") or "")
            if custom_id == "dank_server_stats:enable":
                child.label = "Repair Display" if self.enabled else "Enable Stats"
                child.style = discord.ButtonStyle.success if self.enabled else discord.ButtonStyle.primary
            elif custom_id == "dank_server_stats:number_style":
                child.label = f"Numbers: {str(self.prefs['number_style']).title()}"
            elif custom_id == "dank_server_stats:placement":
                child.label = f"Placement: {str(self.prefs['placement']).title()}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await interaction.response.send_message("❌ Open your own Server Stats panel.", ephemeral=True)
        return False

    @discord.ui.button(
        label="Enable Stats",
        emoji="📊",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="dank_server_stats:enable",
    )
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
        await interaction.response.defer()
        _ok, note = await ensure_security_stats_display(guild)
        await _render_center(interaction, content=note)

    @discord.ui.button(
        label="Disable & Remove",
        emoji="🗑️",
        style=discord.ButtonStyle.danger,
        row=0,
        custom_id="dank_server_stats:disable",
    )
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
        await interaction.response.defer()
        _ok, note = await disable_security_stats_display(guild, remove_channels=True)
        await _render_center(interaction, content=note)

    @discord.ui.button(
        label="Refresh Now",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id="dank_server_stats:refresh",
    )
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
        await interaction.response.defer()
        cfg = await get_guild_config(int(guild.id), refresh=True)
        if not _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False):
            return await _render_center(interaction, content="ℹ️ Server Stats are disabled. Press **Enable Stats** first.")
        changed = await refresh_security_stats_display(guild, force=True)
        await _render_center(
            interaction,
            content="✅ Server Stats refreshed and repaired." if changed else "ℹ️ Server Stats did not need a refresh.",
        )

    @discord.ui.button(
        label="Category Name",
        emoji="✏️",
        style=discord.ButtonStyle.secondary,
        row=3,
        custom_id="dank_server_stats:category_name",
    )
    async def category_name(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(
            CategoryNameModal(current=str(self.prefs["category_name"]), owner_id=self.owner_id)
        )

    @discord.ui.button(
        label="Numbers",
        emoji="🔢",
        style=discord.ButtonStyle.secondary,
        row=3,
        custom_id="dank_server_stats:number_style",
    )
    async def number_style(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
        await interaction.response.defer()
        target = "exact" if str(self.prefs["number_style"]) == "compact" else "compact"
        await _save_preferences(int(guild.id), {SECURITY_STATS_NUMBER_STYLE_KEY: target})
        cfg = await get_guild_config(int(guild.id), refresh=True)
        note = f"✅ Number style set to **{target.title()}**."
        if _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False):
            ok, applied = await ensure_security_stats_display(guild)
            if not ok:
                note = applied
        await _render_center(interaction, content=note)

    @discord.ui.button(
        label="Placement",
        emoji="↕️",
        style=discord.ButtonStyle.secondary,
        row=3,
        custom_id="dank_server_stats:placement",
    )
    async def placement(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
        await interaction.response.defer()
        current = str(self.prefs["placement"])
        target = {"top": "keep", "keep": "bottom", "bottom": "top"}.get(current, "top")
        await _save_preferences(int(guild.id), {SECURITY_STATS_PLACEMENT_KEY: target})
        cfg = await get_guild_config(int(guild.id), refresh=True)
        note = f"✅ Category placement set to **{target.title()}**."
        if _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False):
            ok, applied = await ensure_security_stats_display(guild)
            if not ok:
                note = applied
        await _render_center(interaction, content=note)

    @discord.ui.button(
        label="Reset Look",
        emoji="🧹",
        style=discord.ButtonStyle.secondary,
        row=3,
        custom_id="dank_server_stats:reset",
    )
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
        await interaction.response.defer()
        await _save_preferences(
            int(guild.id),
            {
                SECURITY_STATS_CATEGORY_NAME_KEY: SECURITY_STATS_CATEGORY_NAME,
                SECURITY_STATS_VISIBLE_KEYS_KEY: list(DEFAULT_SECURITY_STATS_VISIBLE_KEYS),
                SECURITY_STATS_CUSTOM_LABELS_KEY: {},
                SECURITY_STATS_NUMBER_STYLE_KEY: "compact",
                SECURITY_STATS_PLACEMENT_KEY: "top",
            },
        )
        cfg = await get_guild_config(int(guild.id), refresh=True)
        note = "✅ Server Stats appearance reset to defaults."
        if _cfg_bool(cfg, SECURITY_STATS_ENABLED_KEY, False):
            ok, applied = await ensure_security_stats_display(guild)
            if not ok:
                note = applied
        await _render_center(interaction, content=note)

    @discord.ui.button(
        label="Dank Shield Home",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        row=4,
        custom_id="dank_server_stats:home",
    )
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_surface_v2 import CompactDankHomeView, _home_embed

        await interaction.response.edit_message(
            content=None,
            embed=_home_embed(),
            view=CompactDankHomeView(self.owner_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(
        label="Close",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        row=4,
        custom_id="dank_server_stats:close",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            content="Server Stats closed. Reopen it from `/dank home`.",
            embed=None,
            view=None,
        )


__all__ = [
    "ServerStatsView",
    "open_server_stats_center",
]
