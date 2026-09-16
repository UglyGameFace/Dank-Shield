from __future__ import annotations

"""Same-screen self-repair integration for public configuration menus.

This module is loaded from the existing late public-runtime UX integration point.
It composes a repair control into canonical views without creating a second
command, persistence owner, or permission mutation path. Actual repairs remain
owned by ``permission_repair_core`` through ``contextual_permission_repair``.
"""

from typing import Any

import discord

from stoney_verify import contextual_permission_repair as contextual
from stoney_verify import welcome_event_services as welcome

_PATCHED = False
_ORIGINAL_WELCOME_EVENTS_VIEW = welcome.WelcomeEventsCenterView


def _welcome_targets(
    guild: discord.Guild,
    cfg: Any,
) -> tuple[contextual.ContextualRepairTarget, ...]:
    targets: list[contextual.ContextualRepairTarget] = []
    join_channel = welcome._join_channel(guild, cfg)
    leave_channel = welcome._leave_channel(guild, cfg)

    if isinstance(join_channel, discord.TextChannel):
        targets.append(
            contextual.ContextualRepairTarget(
                int(join_channel.id),
                "welcome",
                "Member-facing Join channel",
            )
        )
    if isinstance(leave_channel, discord.TextChannel):
        targets.append(
            contextual.ContextualRepairTarget(
                int(leave_channel.id),
                "logs",
                "Private Join/Leave log",
            )
        )
    return contextual.normalize_targets(targets)


def _welcome_manual_issues(
    guild: discord.Guild,
    cfg: Any,
) -> list[str]:
    issues: list[str] = []
    join_enabled = welcome._join_enabled(cfg)
    leave_enabled = welcome._leave_enabled(cfg)
    join_channel = welcome._join_channel(guild, cfg)
    leave_channel = welcome._leave_channel(guild, cfg)

    if join_enabled and not isinstance(join_channel, discord.TextChannel):
        issues.append(
            "Join announcements are ON but the configured Join channel is missing. "
            "Choose a live Join channel above."
        )
    if leave_enabled and not isinstance(leave_channel, discord.TextChannel):
        issues.append(
            "Leave announcements are ON but the configured Leave channel is missing. "
            "Choose a live Leave channel above."
        )

    if join_enabled and isinstance(join_channel, discord.TextChannel):
        visibility = welcome._join_audience_status(guild, cfg, join_channel)
        if str(visibility).startswith("⚠️"):
            issues.append(
                "Join audience: "
                + str(visibility).removeprefix("⚠️").strip()
                + ". Choose a public Join channel above. Dank Shield will not make a private staff channel public automatically."
            )
    return issues


class WelcomeContextRepairButton(discord.ui.Button):
    def __init__(self, *, guild: discord.Guild, cfg: Any) -> None:
        self.guild_id = int(guild.id)
        audit = contextual.audit_context(
            guild,
            _welcome_targets(guild, cfg),
            manual_issues=_welcome_manual_issues(guild, cfg),
        )
        label, emoji, style, disabled = contextual.repair_button_state(audit)
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id="dank_setup_welcome_events:fix_issues",
            row=4,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
        from stoney_verify.guild_config import get_guild_config

        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None or int(guild.id) != self.guild_id:
            return await welcome._send_ephemeral(
                interaction,
                "❌ This repair control belongs to a different server. Reopen `/dank welcome join-leave`.",
            )

        # This is a component update, not a new response. Reuse the same
        # acknowledgement path as the menu's Refresh button so the repair result
        # edits the existing ephemeral menu instead of creating a detached
        # thinking response that can leave the original controls stale.
        await welcome._ack_update(interaction)

        cfg = await get_guild_config(int(guild.id), refresh=True)
        result = await contextual.repair_context(
            guild,
            _welcome_targets(guild, cfg),
            actor_id=int(interaction.user.id),
            manual_issues=_welcome_manual_issues(guild, cfg),
        )
        await welcome._refresh_center(
            interaction,
            last_action=result.summary(),
        )


class ContextualWelcomeEventsCenterView(_ORIGINAL_WELCOME_EVENTS_VIEW):
    def __init__(self, *, guild: discord.Guild, cfg: Any) -> None:
        super().__init__(guild=guild, cfg=cfg)
        for child in list(self.children):
            if str(getattr(child, "custom_id", "") or "") == "dank_setup_welcome_events:fix_issues":
                self.remove_item(child)
        self.add_item(WelcomeContextRepairButton(guild=guild, cfg=cfg))


def apply_contextual_permission_repair() -> bool:
    """Install the shared repair control into the canonical Welcome events view."""

    global _PATCHED
    if _PATCHED:
        return True
    try:
        welcome.WelcomeEventsCenterView = ContextualWelcomeEventsCenterView
        _PATCHED = True
        print(
            "✅ public_contextual_permission_repair: Welcome Join/Leave menu has same-screen Fix Issues"
        )
        return True
    except Exception as exc:
        try:
            print(
                "⚠️ public_contextual_permission_repair failed: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return False


__all__ = [
    "WelcomeContextRepairButton",
    "ContextualWelcomeEventsCenterView",
    "apply_contextual_permission_repair",
]
