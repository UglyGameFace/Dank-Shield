from __future__ import annotations

from typing import Any, Iterable, Optional

import discord

from .commands_ext.public_setup_group import _require_setup_permission
from .guild_config import get_guild_config, upsert_guild_config
from .profile_card_runtime import (
    LIVE_ALLOWED_FIELDS_KEY,
    LiveProfileCardRuntime,
    parse_live_card_config,
)

_RUNTIME_ATTRIBUTE = "_dank_live_profile_card_runtime"
_MAX_LIVE_CHANNELS = 10
_FIELD_LABELS = {
    "server_roles": "Server roles",
    "profile_tags": "Profile tags",
    "account_dates": "Account dates",
    "platforms": "Shared platforms",
}


def _clean_channel_ids(values: Iterable[Any]) -> set[int]:
    cleaned: set[int] = set()
    for value in values:
        try:
            channel_id = int(getattr(value, "id", value))
        except (TypeError, ValueError):
            continue
        if channel_id > 0:
            cleaned.add(channel_id)
    return cleaned


def _channel_permission_issues(channel: discord.TextChannel) -> list[str]:
    me = channel.guild.me
    if not isinstance(me, discord.Member):
        return ["Dank Shield member unavailable"]
    permissions = channel.permissions_for(me)
    checks = (
        ("View Channel", permissions.view_channel),
        ("Send Messages", permissions.send_messages),
        ("Embed Links", permissions.embed_links),
        ("Read Message History", permissions.read_message_history),
        ("Attach Files", permissions.attach_files),
    )
    return [label for label, allowed in checks if not allowed]


def _channel_lines(guild: discord.Guild, channel_ids: Iterable[int], *, health: bool) -> str:
    lines: list[str] = []
    for channel_id in sorted(_clean_channel_ids(channel_ids)):
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            lines.append(f"❌ Missing text channel `{channel_id}`")
            continue
        if not health:
            lines.append(channel.mention)
            continue
        issues = _channel_permission_issues(channel)
        lines.append(
            f"{'✅' if not issues else '❌'} {channel.mention}"
            + (" — ready" if not issues else " — missing " + ", ".join(issues))
        )
    return "\n".join(lines)[:1024] if lines else "None selected."


def _runtime(client: Any) -> Optional[LiveProfileCardRuntime]:
    found = getattr(client, _RUNTIME_ATTRIBUTE, None)
    return found if isinstance(found, LiveProfileCardRuntime) else None


async def _edit_or_send(
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
    if not interaction.response.is_done():
        try:
            await interaction.response.edit_message(**payload)
            return
        except Exception:
            await interaction.response.send_message(**payload, ephemeral=True)
            return
    try:
        await interaction.edit_original_response(**payload)
    except Exception:
        await interaction.followup.send(**payload, ephemeral=True)


async def _private_message(interaction: discord.Interaction, content: str, *, ok: bool = True) -> None:
    text = ("✅ " if ok else "❌ ") + str(content or "")[:1900]
    kwargs = {
        "content": text,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.send_message(**kwargs)
    else:
        await interaction.followup.send(**kwargs)


class _FieldToggleButton(discord.ui.Button):
    def __init__(self, field_key: str, *, allowed: bool) -> None:
        self.field_key = field_key
        label = _FIELD_LABELS[field_key]
        super().__init__(
            label=f"{label}: {'Allowed' if allowed else 'Hidden'}",
            emoji={"server_roles": "🏷️", "profile_tags": "🎭", "account_dates": "📅", "platforms": "🔗"}[field_key],
            style=discord.ButtonStyle.success if allowed else discord.ButtonStyle.secondary,
            custom_id=f"dank_setup_profile_cards:field:{field_key}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileCardSetupView) or not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _private_message(interaction, "Use this inside a server.", ok=False)
        if not interaction.response.is_done():
            await interaction.response.defer()
        config = await get_guild_config(guild.id, refresh=True)
        allowed = set(parse_live_card_config(config).allowed_fields)
        if self.field_key in allowed:
            allowed.remove(self.field_key)
            action = "hidden"
        else:
            allowed.add(self.field_key)
            action = "allowed"
        updated = await upsert_guild_config(
            guild.id,
            {LIVE_ALLOWED_FIELDS_KEY: sorted(allowed)},
        )
        runtime = _runtime(interaction.client)
        if runtime is not None:
            await runtime.invalidate_guild_cards(guild)
        await view.refresh(
            interaction,
            config=updated,
            notice=(
                f"{_FIELD_LABELS[self.field_key]} are now {action}. Member privacy can still hide them. "
                "A basic avatar/name signature remains available even when every optional field is hidden."
            ),
        )


class _RefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:refresh",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileCardSetupView) or not await view.interaction_check(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        await view.refresh(interaction, notice="Refreshed from saved configuration.")


class _BackButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Back to All Features",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:back",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .commands_ext import public_setup_recommend

        await public_setup_recommend._open_advanced_settings(interaction)


class _HomeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Setup Home",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:home",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .commands_ext import public_setup_recommend

        await public_setup_recommend._home_edit(interaction)


class _CloseButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Close",
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            custom_id="dank_setup_profile_cards:close",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            for child in self.view.children:
                child.disabled = True
        await interaction.response.edit_message(
            content="Closed Compact Profile Signatures setup. Reopen it from `/dank setup`.",
            embed=None,
            view=self.view,
        )


class ProfileCardSetupView(discord.ui.View):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _private_message(
                interaction,
                "Only the manager who opened this setup panel can use it.",
                ok=False,
            )
            return False
        if interaction.guild is None:
            await _private_message(interaction, "Use this inside a server.", ok=False)
            return False
        return await _require_setup_permission(interaction)


__all__ = ["ProfileCardSetupView"]
