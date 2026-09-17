from __future__ import annotations

"""Shared same-screen permission repair for Profile / Self Roles surfaces.

This module owns no Discord permission mutation. It composes the existing
Profile Builder, Compact Profile Signatures, and Roles Center onto the shared
``contextual_permission_repair`` contract so every overwrite change still flows
through ``permission_repair_core``.
"""

from typing import Any, Iterable, Optional

import discord

from stoney_verify import contextual_permission_repair as contextual
from stoney_verify import profile_card_setup_ui as signatures
from stoney_verify import roles_center_services as roles_center
from stoney_verify.guild_config import get_guild_config
from stoney_verify.profile_card_runtime import parse_live_card_config

from . import public_self_roles_group as profile

_PATCHED = False

_ORIGINAL_PROFILE_STATUS = profile._profile_builder_status
_ORIGINAL_PROFILE_VIEW = profile.ProfileBuilderView
_ORIGINAL_BUILDER_ACTION = profile._handle_builder_action
_ORIGINAL_SIGNATURE_VIEW = signatures.ProfileCardSetupView
_ORIGINAL_SIGNATURE_SAVE = signatures._save_selected_channels
_ORIGINAL_ROLES_POST = roles_center._post_default_panel


def _pretty_permission(name: str) -> str:
    return str(name or "").replace("_", " ").strip().title()


def _unique(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _target(
    channel: Any,
    *,
    feature: str = "general",
    label: str,
) -> contextual.ContextualRepairTarget | None:
    try:
        channel_id = int(getattr(channel, "id", 0) or 0)
    except Exception:
        channel_id = 0
    if channel_id <= 0:
        return None
    return contextual.ContextualRepairTarget(
        channel_id=channel_id,
        feature=feature,
        label=label,
    )


def _targets_for_ids(
    guild: discord.Guild,
    channel_ids: Iterable[int],
    *,
    label_prefix: str,
) -> tuple[contextual.ContextualRepairTarget, ...]:
    rows: list[contextual.ContextualRepairTarget] = []
    for raw in sorted({int(value) for value in channel_ids if int(value) > 0}):
        channel = guild.get_channel(raw)
        label = (
            f"{label_prefix} {getattr(channel, 'mention', f'`{raw}`')}"
            if channel is not None
            else f"{label_prefix} `{raw}`"
        )
        rows.append(
            contextual.ContextualRepairTarget(
                channel_id=raw,
                feature="general",
                label=label,
            )
        )
    return contextual.normalize_targets(rows)


def _profile_manual_issues(guild: discord.Guild) -> list[str]:
    try:
        return _unique(profile._profile_manual_blockers(guild))
    except Exception:
        return ["Profile role prerequisites could not be evaluated safely."]


def contextual_profile_builder_status(
    guild: discord.Guild,
    channel: discord.TextChannel,
) -> tuple[bool, list[str], list[str]]:
    target = _target(
        channel,
        feature="general",
        label="Profile / self-role panel channel",
    )
    manual = _profile_manual_issues(guild)
    audit = contextual.audit_context(
        guild,
        (target,) if target is not None else (),
        manual_issues=manual,
    )

    fixable: list[str] = []
    manual_out: list[str] = list(manual)
    for row in audit.targets:
        if row.missing_target:
            manual_out.append(row.note or "Profile panel channel is missing.")
            continue
        if not row.missing:
            continue
        if row.blockers:
            manual_out.append(f"{row.target.label}: " + " ".join(row.blockers))
            continue
        if row.repairable:
            fixable.extend(_pretty_permission(name) for name in row.repairable)
            continue
        manual_out.append(
            f"{row.target.label}: Discord has an explicit deny that safe repair will not clear automatically."
        )

    return audit.healthy, _unique(fixable), _unique(manual_out)


class ContextualProfileBuilderView(_ORIGINAL_PROFILE_VIEW):
    def __init__(self, *, author_id: int, ready: bool, fixable: bool, title: str) -> None:
        super().__init__(
            author_id=author_id,
            ready=ready,
            fixable=fixable,
            title=title,
        )

        fix_id = f"{profile.PROFILE_PREFIX}builder:fix"
        for child in list(self.children):
            if str(getattr(child, "custom_id", "") or "") == fix_id:
                self.remove_item(child)

        if ready:
            label = "Access Healthy"
            emoji = "✅"
            style = discord.ButtonStyle.secondary
            disabled = True
        elif fixable:
            label = "Fix Issues"
            emoji = "🛠️"
            style = discord.ButtonStyle.danger
            disabled = False
        else:
            label = "Manual Fix Needed"
            emoji = "⚠️"
            style = discord.ButtonStyle.secondary
            disabled = False

        self.add_item(
            discord.ui.Button(
                label=label,
                emoji=emoji,
                style=style,
                custom_id=fix_id,
                row=0,
                disabled=disabled,
            )
        )


def _builder_embed(
    guild: discord.Guild,
    channel: discord.TextChannel,
    *,
    last_action: str = "",
) -> discord.Embed:
    ready, fixable, manual = contextual_profile_builder_status(guild, channel)
    embed = discord.Embed(
        title="🌿 Profile Builder",
        description=(
            "This builder uses the current channel as the profile/self-role panel target. "
            "Dank Shield repairs only its own safe channel access; role hierarchy and role permissions stay manual."
        ),
        color=discord.Color.green() if ready else discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Panel target", value=channel.mention, inline=False)
    embed.add_field(
        name="Status",
        value=(
            "✅ Access Healthy"
            if ready
            else "🛠️ Safe repair available"
            if fixable
            else "⚠️ Manual Fix Needed"
        ),
        inline=False,
    )
    if fixable:
        embed.add_field(
            name="Bot can fix now",
            value="\n".join(f"• {item}" for item in fixable)[:1024],
            inline=False,
        )
    if manual:
        embed.add_field(
            name="Needs manual fix",
            value="\n".join(f"• {item}" for item in manual)[:1024],
            inline=False,
        )
    embed.add_field(
        name="Panel lifetime",
        value=profile.public_panel_lifecycle_text(
            "Profile Builder result panel",
            "Private builder actions",
        ),
        inline=False,
    )
    if last_action:
        embed.add_field(name="Last repair", value=str(last_action)[:1024], inline=False)
    return embed


async def contextual_builder_action(
    interaction: discord.Interaction,
    action: str,
) -> bool:
    if action != "fix":
        return await _ORIGINAL_BUILDER_ACTION(interaction, action)

    if not await profile._require_setup_permission(interaction):
        return True
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or not isinstance(channel, discord.TextChannel):
        await profile._reply(
            interaction,
            "Run builder actions inside the panel target channel.",
            ok=False,
        )
        return True

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=False)
    except Exception:
        pass

    target = _target(
        channel,
        feature="general",
        label="Profile / self-role panel channel",
    )
    result = await contextual.repair_context(
        guild,
        (target,) if target is not None else (),
        actor_id=int(interaction.user.id),
        manual_issues=_profile_manual_issues(guild),
    )
    ready, fixable, _manual = contextual_profile_builder_status(guild, channel)

    try:
        await interaction.edit_original_response(
            embed=_builder_embed(
                guild,
                channel,
                last_action=result.summary(),
            ),
            view=profile.ProfileBuilderView(
                author_id=int(interaction.user.id),
                ready=ready,
                fixable=bool(fixable),
                title="Profile Panel",
            ),
        )
    except Exception:
        await profile._reply(interaction, result.summary(), ok=result.ok)
    return True


class SignatureAccessButton(discord.ui.Button):
    def __init__(
        self,
        *,
        guild: Optional[discord.Guild],
        config: Any,
        channel_ids: Iterable[int],
    ) -> None:
        self.guild_id = int(guild.id) if guild is not None else 0
        self.channel_ids = set(signatures._clean_channel_ids(channel_ids))
        if guild is None:
            label, emoji, style, disabled = (
                "Check / Fix Access",
                "🛠️",
                discord.ButtonStyle.secondary,
                False,
            )
        else:
            manual = (
                []
                if self.channel_ids
                else ["No Compact Profile Signature channels are selected yet."]
            )
            audit = contextual.audit_context(
                guild,
                _targets_for_ids(
                    guild,
                    self.channel_ids,
                    label_prefix="Compact signature channel",
                ),
                manual_issues=manual,
            )
            label, emoji, style, disabled = contextual.repair_button_state(audit)
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id="dank_setup_profile_cards:contextual_repair:v1",
            row=3,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ContextualProfileCardSetupView):
            return
        if not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None or (self.guild_id and int(guild.id) != self.guild_id):
            return await signatures._private_message(
                interaction,
                "This repair control belongs to a different server. Reopen Compact Profile Signatures.",
                ok=False,
            )

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)
        except Exception:
            pass

        config = await get_guild_config(int(guild.id), refresh=True)
        saved = set(parse_live_card_config(config).channel_ids)
        selected = set(view.pending_channel_ids or saved)
        manual = [] if selected else ["No Compact Profile Signature channels are selected yet."]
        result = await contextual.repair_context(
            guild,
            _targets_for_ids(
                guild,
                selected,
                label_prefix="Compact signature channel",
            ),
            actor_id=int(interaction.user.id),
            manual_issues=manual,
        )
        await view.refresh(
            interaction,
            config=config,
            notice=result.summary(),
        )


class ContextualProfileCardSetupView(_ORIGINAL_SIGNATURE_VIEW):
    def __init__(
        self,
        *,
        owner_id: int,
        config: Any,
        pending_channel_ids: Optional[set[int]] = None,
        guild: Optional[discord.Guild] = None,
    ) -> None:
        super().__init__(
            owner_id=owner_id,
            config=config,
            pending_channel_ids=pending_channel_ids,
        )
        self.add_item(
            SignatureAccessButton(
                guild=guild,
                config=config,
                channel_ids=set(self.pending_channel_ids),
            )
        )

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        config: Optional[Any] = None,
        notice: str = "",
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        latest = dict(config or await get_guild_config(guild.id, refresh=True))
        replacement = ContextualProfileCardSetupView(
            owner_id=self.owner_id,
            config=latest,
            pending_channel_ids=set(self.pending_channel_ids),
            guild=guild,
        )
        await signatures._edit_or_send(
            interaction,
            embed=signatures._setup_embed(
                guild,
                latest,
                pending_channel_ids=set(self.pending_channel_ids),
                notice=notice,
            ),
            view=replacement,
        )


async def contextual_save_selected_channels(
    interaction: discord.Interaction,
    view: Any,
    selected: set[int],
) -> None:
    guild = interaction.guild
    if guild is None:
        return await _ORIGINAL_SIGNATURE_SAVE(interaction, view, selected)

    cleaned = signatures._clean_channel_ids(selected)
    problems = signatures._selection_problems(guild, cleaned)
    if not problems:
        return await _ORIGINAL_SIGNATURE_SAVE(interaction, view, cleaned)

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=False)
    except Exception:
        pass

    result = await contextual.repair_context(
        guild,
        _targets_for_ids(
            guild,
            cleaned,
            label_prefix="Compact signature channel",
        ),
        actor_id=int(interaction.user.id),
    )
    if result.ok:
        return await _ORIGINAL_SIGNATURE_SAVE(interaction, view, cleaned)

    await signatures._private_message(
        interaction,
        result.summary()
        + "\nThe selected channels were not saved because access still needs manual attention.",
        ok=False,
    )


async def contextual_roles_panel_post(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    *,
    kind: str,
) -> None:
    from .public_setup_group import _require_setup_permission

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await roles_center._send_ephemeral(
            interaction,
            "❌ This must be used inside a server.",
        )

    target = _target(
        channel,
        feature="general",
        label="Self-role panel channel",
    )
    manual = _profile_manual_issues(guild)
    audit = contextual.audit_context(
        guild,
        (target,) if target is not None else (),
        manual_issues=manual,
    )
    if audit.needs_attention:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass
        result = await contextual.repair_context(
            guild,
            (target,) if target is not None else (),
            actor_id=int(interaction.user.id),
            manual_issues=manual,
        )
        if not result.ok:
            return await roles_center._send_ephemeral(
                interaction,
                "⚠️ Self-role panel access still needs attention.\n" + result.summary(),
            )

    await _ORIGINAL_ROLES_POST(interaction, channel, kind=kind)


def _restore_originals() -> None:
    profile._profile_builder_status = _ORIGINAL_PROFILE_STATUS
    profile.ProfileBuilderView = _ORIGINAL_PROFILE_VIEW
    profile._handle_builder_action = _ORIGINAL_BUILDER_ACTION
    signatures.ProfileCardSetupView = _ORIGINAL_SIGNATURE_VIEW
    signatures._save_selected_channels = _ORIGINAL_SIGNATURE_SAVE
    roles_center._post_default_panel = _ORIGINAL_ROLES_POST


def apply_profile_contextual_permission_repair() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        profile._profile_builder_status = contextual_profile_builder_status
        profile.ProfileBuilderView = ContextualProfileBuilderView
        profile._handle_builder_action = contextual_builder_action
        signatures.ProfileCardSetupView = ContextualProfileCardSetupView
        signatures._save_selected_channels = contextual_save_selected_channels
        roles_center._post_default_panel = contextual_roles_panel_post
        _PATCHED = True
        return True
    except Exception as exc:
        _restore_originals()
        try:
            print(
                "⚠️ public_profile_contextual_permission_repair failed: "
                f"{type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return False


__all__ = [
    "ContextualProfileBuilderView",
    "ContextualProfileCardSetupView",
    "SignatureAccessButton",
    "contextual_profile_builder_status",
    "contextual_builder_action",
    "contextual_save_selected_channels",
    "contextual_roles_panel_post",
    "apply_profile_contextual_permission_repair",
]
