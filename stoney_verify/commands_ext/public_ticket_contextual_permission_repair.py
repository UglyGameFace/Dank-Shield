from __future__ import annotations

"""Same-screen permission repair for normal public Ticket surfaces.

This module composes the shared contextual repair contract into the canonical
Ticket health and current-ticket controls. It never owns Discord overwrite
mutation itself; all safe changes remain delegated to
``contextual_permission_repair`` / ``permission_repair_core``.
"""

from typing import Any, Iterable

import discord

from stoney_verify import contextual_permission_repair as contextual
from stoney_verify.guild_config import get_guild_config
from stoney_verify.commands_ext import public_ticket_command_center as center
from stoney_verify.commands_ext import public_ticket_panel_clean as panel

_PATCHED = False
_ORIGINAL_TICKET_ACTION_CENTER_VIEW = center.TicketActionCenterView
_ORIGINAL_SEND_HEALTH = panel._send_health


def _cfg_id(cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            value = cfg.get(key) if hasattr(cfg, "get") else getattr(cfg, key, None)
        except Exception:
            value = None
        try:
            parsed = int(value or 0)
        except Exception:
            parsed = 0
        if parsed > 0:
            return parsed
    return 0


def _dedupe(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _ticket_config_targets(cfg: Any) -> tuple[contextual.ContextualRepairTarget, ...]:
    """Map only saved Ticket infrastructure IDs; never guess replacements."""

    rows: list[contextual.ContextualRepairTarget] = []
    specs = (
        (
            ("ticket_category_id", "active_ticket_category_id", "ticket_active_category_id", "open_ticket_category_id"),
            "tickets",
            "Active Tickets category",
        ),
        (
            ("ticket_archive_category_id", "archive_ticket_category_id", "ticket_archived_category_id"),
            "tickets",
            "Ticket archive category",
        ),
        (
            ("ticket_panel_channel_id", "support_channel_id", "ticket_support_channel_id"),
            "general",
            "Public ticket panel channel",
        ),
        (
            ("transcripts_channel_id", "ticket_transcripts_channel_id", "transcript_channel_id"),
            "logs",
            "Ticket transcripts channel",
        ),
    )
    for keys, feature, label in specs:
        channel_id = _cfg_id(cfg, *keys)
        if channel_id <= 0:
            continue
        rows.append(
            contextual.ContextualRepairTarget(
                channel_id=channel_id,
                feature=feature,
                label=label,
            )
        )
    return contextual.normalize_targets(rows)


def _selected_ticket_targets(channel: discord.TextChannel | None) -> tuple[contextual.ContextualRepairTarget, ...]:
    if not isinstance(channel, discord.TextChannel):
        return ()
    return (
        contextual.ContextualRepairTarget(
            channel_id=int(channel.id),
            feature="tickets",
            label="Selected ticket channel",
        ),
    )


def _ticket_manual_issues(guild: discord.Guild, cfg: Any) -> list[str]:
    """Return Ticket conditions the bot-only overwrite repair must not mutate."""

    issues: list[str] = []
    active_id = _cfg_id(
        cfg,
        "ticket_category_id",
        "active_ticket_category_id",
        "ticket_active_category_id",
        "open_ticket_category_id",
    )
    staff_id = _cfg_id(cfg, "staff_role_id", "ticket_staff_role_id", "support_role_id", "vc_staff_role_id")

    active = guild.get_channel(active_id) if active_id > 0 else None
    staff = guild.get_role(staff_id) if staff_id > 0 else None

    if active_id <= 0:
        issues.append("Active Tickets category is not configured. Choose it in `/dank setup` → Ticket Basics.")
    elif not isinstance(active, discord.CategoryChannel):
        issues.append("The saved Active Tickets category is missing or no longer a category. Choose the correct category in setup.")

    if staff_id <= 0:
        issues.append("Ticket staff role is not configured. Choose it in `/dank setup` → Ticket Basics.")
    elif not isinstance(staff, discord.Role):
        issues.append("The saved Ticket staff role no longer exists. Choose a live staff role in setup.")

    if isinstance(active, discord.CategoryChannel) and isinstance(staff, discord.Role):
        try:
            issues.extend(panel._ticket_category_shape_blockers(active, staff))
        except Exception:
            issues.append("Ticket category privacy/staff visibility could not be verified automatically.")

    return _dedupe(issues)


def _button_state(
    guild: discord.Guild | None,
    targets: Iterable[contextual.ContextualRepairTarget],
    *,
    manual_issues: Iterable[str] = (),
) -> tuple[str, str, discord.ButtonStyle, bool]:
    if guild is None:
        return "Check / Fix Access", "🛠️", discord.ButtonStyle.secondary, False
    audit = contextual.audit_context(guild, targets, manual_issues=manual_issues)
    return contextual.repair_button_state(audit)


async def _defer_update(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
    except Exception:
        pass


async def _edit_component(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    payload = {
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    try:
        await interaction.edit_original_response(**payload)
        return
    except Exception:
        pass
    try:
        message = getattr(interaction, "message", None)
        if message is not None:
            await message.edit(**payload)
            return
    except Exception:
        pass
    await center._private(interaction, embed=embed, view=view)


async def _ticket_health_embed(
    guild: discord.Guild,
    *,
    last_action: str = "",
) -> discord.Embed:
    blockers, warnings, ok = await panel._health_lines(guild)
    embed = discord.Embed(
        title="🩺 Ticket Panel Health",
        description=(
            "🚫 Fix the blockers first."
            if blockers
            else "✅ Ticket panel creation path looks ready."
        ),
        color=(
            discord.Color.red()
            if blockers
            else discord.Color.gold()
            if warnings
            else discord.Color.green()
        ),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Blockers", value=panel._field(blockers, "✅ None"), inline=False)
    embed.add_field(name="Warnings", value=panel._field(warnings, "✅ None"), inline=False)
    embed.add_field(name="Passing", value=panel._field(ok[:10], "No passing checks."), inline=False)
    if last_action:
        embed.add_field(name="Access Repair", value=str(last_action)[:1024], inline=False)
    embed.add_field(
        name="Panel lifetime",
        value=panel.public_panel_lifecycle_text("Create Ticket panel", "Private ticket type menus/confirm screens"),
        inline=False,
    )
    return embed


class TicketInfrastructureRepairButton(discord.ui.Button):
    def __init__(self, *, guild: discord.Guild, cfg: Any, row: int = 0) -> None:
        self.guild_id = int(guild.id)
        targets = _ticket_config_targets(cfg)
        label, emoji, style, disabled = _button_state(
            guild,
            targets,
            manual_issues=_ticket_manual_issues(guild, cfg),
        )
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id="dank:tickets:contextual_repair:infrastructure:v1",
            row=row,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None or int(guild.id) != self.guild_id:
            return await center._private(interaction, "❌ Reopen Ticket Panel Health in this server.")
        if not panel._staff_check(interaction):
            return await center._private(interaction, "❌ Staff only.")

        await _defer_update(interaction)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        result = await contextual.repair_context(
            guild,
            _ticket_config_targets(cfg),
            actor_id=int(interaction.user.id),
            manual_issues=_ticket_manual_issues(guild, cfg),
        )
        cfg = await get_guild_config(int(guild.id), refresh=True)
        embed = await _ticket_health_embed(guild, last_action=result.summary())
        view = TicketPanelHealthView(
            owner_id=int(interaction.user.id),
            guild=guild,
            cfg=cfg,
        )
        await _edit_component(interaction, embed=embed, view=view)


class TicketPanelHealthView(discord.ui.View):
    def __init__(self, *, owner_id: int, guild: discord.Guild, cfg: Any) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.add_item(TicketInfrastructureRepairButton(guild=guild, cfg=cfg, row=0))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await center._private(interaction, "❌ Open your own Ticket Panel Health screen to use this repair control.")
        return False


async def _contextual_send_health(interaction: discord.Interaction) -> None:
    if not panel._staff_check(interaction):
        return await panel.reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
    guild = interaction.guild
    if guild is None:
        return await panel.reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

    await panel._defer(interaction, True)
    cfg = await get_guild_config(int(guild.id), refresh=True)
    embed = await _ticket_health_embed(guild)
    view = TicketPanelHealthView(
        owner_id=int(interaction.user.id),
        guild=guild,
        cfg=cfg,
    )
    await panel._ephemeral(
        interaction,
        "Ticket panel health check complete.",
        embed=embed,
        view=view,
    )


class SelectedTicketRepairButton(discord.ui.Button):
    def __init__(
        self,
        parent_view: "ContextualTicketActionCenterView",
        *,
        guild: discord.Guild | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        self.parent_view = parent_view
        label, emoji, style, disabled = _button_state(
            guild,
            _selected_ticket_targets(channel),
        )
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id="dank:ticket:center:contextual_repair:v1",
            row=3,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await center._require_staff(interaction):
            return
        guild = interaction.guild
        channel = self.parent_view.target(interaction)
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await center._private(interaction, "❌ Select a live ticket channel first.")

        await _defer_update(interaction)
        result = await contextual.repair_context(
            guild,
            _selected_ticket_targets(channel),
            actor_id=int(interaction.user.id),
        )
        embed = center._ticket_center_embed(channel)
        embed.add_field(name="Access Repair", value=result.summary()[:1024], inline=False)
        view = ContextualTicketActionCenterView(
            int(self.parent_view.owner_id),
            channel_id=int(channel.id),
            guild=guild,
        )
        await _edit_component(interaction, embed=embed, view=view)


class ContextualTicketChannelPicker(center.TicketChannelPicker):
    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        selected = self.values[0] if self.values else None
        channel = guild.get_channel(int(getattr(selected, "id", 0) or 0)) if guild is not None else None
        if not isinstance(channel, discord.TextChannel):
            return await center._private(interaction, "❌ Choose a text channel from this server.")

        self.parent_view.channel_id = int(channel.id)
        view = ContextualTicketActionCenterView(
            int(self.parent_view.owner_id),
            channel_id=int(channel.id),
            guild=guild,
        )
        await interaction.response.edit_message(
            embed=center._ticket_center_embed(channel),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ContextualTicketActionCenterView(_ORIGINAL_TICKET_ACTION_CENTER_VIEW):
    def __init__(
        self,
        owner_id: int,
        *,
        channel_id: int = 0,
        guild: discord.Guild | None = None,
    ) -> None:
        super().__init__(owner_id, channel_id=channel_id)

        for child in list(self.children):
            custom_id = str(getattr(child, "custom_id", "") or "")
            if custom_id in {
                "dank:ticket:center:channel:v1",
                "dank:ticket:center:contextual_repair:v1",
            }:
                self.remove_item(child)
        self.add_item(ContextualTicketChannelPicker(self))

        channel = None
        if guild is not None and int(channel_id or 0) > 0:
            resolved = guild.get_channel(int(channel_id))
            if isinstance(resolved, discord.TextChannel):
                channel = resolved
        self.add_item(SelectedTicketRepairButton(self, guild=guild, channel=channel))


def apply_ticket_contextual_permission_repair() -> bool:
    """Install Ticket adoption without creating another permission-mutation owner."""

    global _PATCHED
    if _PATCHED:
        return True
    try:
        panel._send_health = _contextual_send_health
        center.TicketActionCenterView = ContextualTicketActionCenterView
        _PATCHED = True
        print(
            "✅ public_ticket_contextual_permission_repair: Ticket Panel Health and Current Ticket Center have same-screen repair"
        )
        return True
    except Exception as exc:
        try:
            print(
                "⚠️ public_ticket_contextual_permission_repair failed: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return False


__all__ = [
    "TicketInfrastructureRepairButton",
    "TicketPanelHealthView",
    "SelectedTicketRepairButton",
    "ContextualTicketActionCenterView",
    "apply_ticket_contextual_permission_repair",
]
