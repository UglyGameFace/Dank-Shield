from __future__ import annotations

"""Same-screen self-repair integration for public configuration menus.

This module is loaded from the existing late public-runtime UX integration point.
It composes repair controls into canonical views without creating a second
command, persistence owner, or permission mutation path. Actual repairs remain
owned by ``permission_repair_core`` through ``contextual_permission_repair``.
"""

from typing import Any, Iterable

import discord

from stoney_verify import contextual_permission_repair as contextual
from stoney_verify import welcome_event_services as welcome
from stoney_verify.commands_ext import public_setup_recommend as setup
from stoney_verify.commands_ext import public_setup_solid as solid
from stoney_verify.guild_config import get_guild_config

_PATCHED = False
_ORIGINAL_WELCOME_EVENTS_VIEW = welcome.WelcomeEventsCenterView
_VERIFICATION_VIEW_CLASS: type[discord.ui.View] | None = None


def _cfg_id(cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            parsed = int(setup._cfg_value(cfg, key, 0) or 0)
        except Exception:
            parsed = 0
        if parsed > 0:
            return parsed
    return 0


def _target(
    cfg: Any,
    keys: tuple[str, ...],
    feature: str,
    label: str,
) -> contextual.ContextualRepairTarget | None:
    channel_id = _cfg_id(cfg, *keys)
    if channel_id <= 0:
        return None
    return contextual.ContextualRepairTarget(channel_id, feature, label)


def _targets(
    rows: Iterable[contextual.ContextualRepairTarget | None],
) -> tuple[contextual.ContextualRepairTarget, ...]:
    return contextual.normalize_targets(item for item in rows if item is not None)


def _setup_targets(
    guild: discord.Guild,
    cfg: Any,
    *,
    verification_only: bool = False,
) -> tuple[contextual.ContextualRepairTarget, ...]:
    """Return exact configured targets whose bot access can be repaired safely."""

    _ = guild
    try:
        services = setup._selected_setup_services(cfg)
    except Exception:
        services = {
            "tickets": False,
            "verify": False,
            "voice": False,
            "logs": False,
        }

    rows: list[contextual.ContextualRepairTarget | None] = []
    include_verify = bool(services.get("verify"))
    include_voice = bool(services.get("voice"))

    if include_verify:
        rows.append(
            _target(
                cfg,
                ("verify_channel_id", "verification_channel_id"),
                "general",
                "Verification start channel",
            )
        )

    if include_voice:
        rows.extend(
            (
                _target(
                    cfg,
                    ("vc_verify_channel_id", "vc_verify_vc_id", "voice_verify_channel_id"),
                    "general",
                    "Voice Verify room",
                ),
                _target(
                    cfg,
                    (
                        "vc_verify_queue_channel_id",
                        "vc_queue_channel_id",
                        "vc_request_channel_id",
                        "vc_verify_requests_channel_id",
                    ),
                    "general",
                    "Voice Verify staff request channel",
                ),
            )
        )

    if verification_only:
        return _targets(rows)

    if services.get("tickets"):
        rows.extend(
            (
                _target(
                    cfg,
                    ("ticket_panel_channel_id", "support_channel_id"),
                    "tickets",
                    "Ticket panel channel",
                ),
                _target(
                    cfg,
                    ("ticket_category_id",),
                    "tickets",
                    "Active tickets category",
                ),
                _target(
                    cfg,
                    ("ticket_archive_category_id", "archive_category_id"),
                    "tickets",
                    "Ticket archive category",
                ),
                _target(
                    cfg,
                    ("transcripts_channel_id", "transcript_channel_id"),
                    "tickets",
                    "Ticket transcripts channel",
                ),
            )
        )

    if services.get("logs"):
        rows.extend(
            (
                _target(
                    cfg,
                    ("modlog_channel_id",),
                    "logs",
                    "Moderation log channel",
                ),
                _target(
                    cfg,
                    ("raidlog_channel_id", "raid_log_channel_id"),
                    "logs",
                    "Security log channel",
                ),
            )
        )

    return _targets(rows)


def _server_permission_issues(
    guild: discord.Guild,
    cfg: Any,
    *,
    verification_only: bool = False,
) -> list[str]:
    """Return only feature-level prerequisites that target repair cannot own.

    View/send/embed/history/attachment failures are deliberately excluded here:
    those are target-effective permissions and belong to the contextual channel
    repair audit. Treating them as server-level blockers would leave Setup Check
    permanently red even after the exact configured target had been repaired.
    """

    try:
        services = dict(setup._selected_setup_services(cfg))
    except Exception:
        services = {
            "tickets": False,
            "verify": False,
            "basic_verify": False,
            "voice": False,
            "id": False,
            "spam_guard": False,
            "logs": False,
        }

    if verification_only:
        services.update(tickets=False, spam_guard=False, logs=False)

    bot_member = getattr(guild, "me", None)
    bot_permissions = getattr(bot_member, "guild_permissions", None)
    try:
        missing = setup._missing_setup_permissions(bot_permissions, services)
    except Exception:
        missing = []

    feature_level = {
        "Manage Roles",
        "Manage Channels",
        "Manage Messages",
    }
    missing_prerequisites = [label for label in missing if label in feature_level]
    if not missing_prerequisites:
        return []
    return [
        "Server-role prerequisite(s) still missing: "
        + ", ".join(missing_prerequisites)
        + ". Reauthorize Dank Shield or update its server role permissions. "
        "The same-screen target repair only changes Dank Shield's own configured channel overwrite."
    ]


def _verification_mapping_issues(cfg: Any) -> list[str]:
    issues: list[str] = []
    try:
        services = setup._selected_setup_services(cfg)
    except Exception:
        services = {"verify": False, "voice": False}

    if services.get("verify") and _cfg_id(cfg, "verify_channel_id", "verification_channel_id") <= 0:
        issues.append(
            "Verification is ON but no verification start channel is configured. Choose one on this screen."
        )
    if services.get("voice"):
        if _cfg_id(cfg, "vc_verify_channel_id", "vc_verify_vc_id", "voice_verify_channel_id") <= 0:
            issues.append(
                "Voice Verify is ON but no Voice Verify room is configured. Choose one on this screen."
            )
        if _cfg_id(
            cfg,
            "vc_verify_queue_channel_id",
            "vc_queue_channel_id",
            "vc_request_channel_id",
            "vc_verify_requests_channel_id",
        ) <= 0:
            issues.append(
                "Voice Verify is ON but no private staff request channel is configured. Choose one on this screen."
            )
    return issues


def _setup_manual_issues(
    guild: discord.Guild,
    cfg: Any,
    *,
    guided_target: str = "ready",
    guided_title: str = "",
    guided_explanation: str = "",
    verification_only: bool = False,
) -> list[str]:
    issues = _server_permission_issues(
        guild,
        cfg,
        verification_only=verification_only,
    )
    if verification_only:
        issues.extend(_verification_mapping_issues(cfg))
        return issues

    if str(guided_target or "ready") != "ready":
        text = str(guided_title or "Setup still has an unfinished required item.").strip()
        explanation = str(guided_explanation or "").strip()
        if explanation:
            text += ": " + explanation
        issues.append(text)
    return issues


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

        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None or int(guild.id) != self.guild_id:
            return await welcome._send_ephemeral(
                interaction,
                "❌ This repair control belongs to a different server. Reopen `/dank welcome join-leave`.",
            )

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


class SetupContextRepairButton(discord.ui.Button):
    def __init__(
        self,
        *,
        guild: discord.Guild,
        cfg: Any,
        manual_issues: Iterable[str] = (),
    ) -> None:
        self.guild_id = int(guild.id)
        audit = contextual.audit_context(
            guild,
            _setup_targets(guild, cfg),
            manual_issues=manual_issues,
        )
        label, emoji, style, disabled = contextual.repair_button_state(audit)
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id="dank_setup_review:contextual_repair",
            row=1,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None or int(guild.id) != self.guild_id:
            return await interaction.response.send_message(
                "❌ This Setup Check belongs to a different server. Reopen `/dank setup`.",
                ephemeral=True,
            )

        await solid._safe_defer_update(interaction)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        target, title, explanation, _requirement_key = await setup._guided_setup_target(guild)
        manual_issues = _setup_manual_issues(
            guild,
            cfg,
            guided_target=target,
            guided_title=title,
            guided_explanation=explanation,
        )
        result = await contextual.repair_context(
            guild,
            _setup_targets(guild, cfg),
            actor_id=int(interaction.user.id),
            manual_issues=manual_issues,
        )
        await setup._open_health_check(
            interaction,
            saved_message=result.summary(),
            already_deferred=True,
        )


class VerificationContextRepairButton(discord.ui.Button):
    def __init__(
        self,
        *,
        guild: discord.Guild | None = None,
        cfg: Any = None,
    ) -> None:
        self.guild_id = int(guild.id) if guild is not None else 0
        if guild is None or cfg is None:
            label, emoji, style, disabled = (
                "Check / Fix Access",
                "🛠️",
                discord.ButtonStyle.secondary,
                False,
            )
        else:
            audit = contextual.audit_context(
                guild,
                _setup_targets(guild, cfg, verification_only=True),
                manual_issues=_setup_manual_issues(
                    guild,
                    cfg,
                    verification_only=True,
                ),
            )
            label, emoji, style, disabled = contextual.repair_button_state(audit)
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id="stoney_solid:verification_contextual_repair",
            row=3,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This must be used inside a server.",
                ephemeral=True,
            )
        if self.guild_id and int(guild.id) != self.guild_id:
            return await interaction.response.send_message(
                "❌ This verification repair control belongs to a different server. Reopen `/dank setup`.",
                ephemeral=True,
            )

        await solid._safe_defer_update(interaction)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        manual_issues = _setup_manual_issues(
            guild,
            cfg,
            verification_only=True,
        )
        result = await contextual.repair_context(
            guild,
            _setup_targets(guild, cfg, verification_only=True),
            actor_id=int(interaction.user.id),
            manual_issues=manual_issues,
        )
        cfg = await get_guild_config(int(guild.id), refresh=True)

        source_embed = None
        try:
            message = getattr(interaction, "message", None)
            embeds = list(getattr(message, "embeds", []) or [])
            if embeds:
                source_embed = embeds[0].copy()
        except Exception:
            source_embed = None
        embed = source_embed or discord.Embed(
            title="🎙️ Verification Channels",
            description="Choose the channels used by verification and repair Dank Shield access here.",
            color=discord.Color.blurple(),
        )
        for index in range(len(embed.fields) - 1, -1, -1):
            if str(embed.fields[index].name) == "Access Repair":
                embed.remove_field(index)
        embed.add_field(
            name="Access Repair",
            value=result.summary() or "Access re-checked.",
            inline=False,
        )
        await solid._edit_or_followup(
            interaction,
            embed=embed,
            view=_verification_view(guild=guild, cfg=cfg),
        )


def _verification_view(
    *,
    guild: discord.Guild | None = None,
    cfg: Any = None,
) -> discord.ui.View:
    if _VERIFICATION_VIEW_CLASS is None:
        view = solid.VerificationChannelsPickerView()
    else:
        view = _VERIFICATION_VIEW_CLASS()
    for child in list(view.children):
        if str(getattr(child, "custom_id", "") or "") == "stoney_solid:verification_contextual_repair":
            view.remove_item(child)
    view.add_item(VerificationContextRepairButton(guild=guild, cfg=cfg))
    return view


async def _contextual_open_health_check(
    interaction: discord.Interaction,
    *,
    saved_message: str = "",
    already_deferred: bool = False,
) -> None:
    """Render canonical Setup Check with same-screen contextual repair state."""

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    if not already_deferred:
        await solid._safe_defer_update(interaction)

    embed = await setup._build_plain_setup_health_embed(guild)
    target, title, explanation, _requirement_key = await setup._guided_setup_target(guild)
    ready = target == "ready"
    cfg = await get_guild_config(int(guild.id), refresh=True)
    manual_issues = _setup_manual_issues(
        guild,
        cfg,
        guided_target=target,
        guided_title=title,
        guided_explanation=explanation,
    )

    if saved_message:
        embed.add_field(
            name="Last Step Finished",
            value=saved_message[:1024],
            inline=False,
        )

    view = setup.SetupReviewView(ready=ready)
    view.add_item(
        SetupContextRepairButton(
            guild=guild,
            cfg=cfg,
            manual_issues=manual_issues,
        )
    )
    await solid._edit_or_followup(
        interaction,
        embed=embed,
        view=view,
    )


def apply_contextual_permission_repair() -> bool:
    """Install shared same-screen repair on supported public configuration views."""

    global _PATCHED, _VERIFICATION_VIEW_CLASS
    if _PATCHED:
        return True
    try:
        welcome.WelcomeEventsCenterView = ContextualWelcomeEventsCenterView
        setup._open_health_check = _contextual_open_health_check

        base_verification_view = solid.VerificationChannelsPickerView
        _VERIFICATION_VIEW_CLASS = base_verification_view

        def contextual_verification_view() -> discord.ui.View:
            return _verification_view()

        solid.VerificationChannelsPickerView = contextual_verification_view  # type: ignore[assignment]
        _PATCHED = True
        print(
            "✅ public_contextual_permission_repair: Welcome, Setup Check, and Verification Channels have same-screen repair"
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
    "SetupContextRepairButton",
    "VerificationContextRepairButton",
    "apply_contextual_permission_repair",
]
