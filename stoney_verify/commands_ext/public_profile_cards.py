from __future__ import annotations

"""Public profile controls backed by compact forum-style signatures."""

from typing import Any, Mapping, Optional

import asyncio
import discord
from discord import app_commands

from . import public_profile_cards_core as _core
from .public_profile_cards_core import *  # noqa: F401,F403
from .public_self_roles_group import profile_group
from stoney_verify.guild_config import get_guild_config
from stoney_verify.profile_card_runtime import (
    LiveProfileCardRuntime,
    parse_live_card_config,
    render_live_profile_card,
)
from stoney_verify.profile_card_service import (
    PLATFORM_SPECS,
    ProfileStorageUnavailable,
    display_profile_username,
    effective_preferences,
    get_effective_profile_settings,
    get_profile_guild_settings,
    get_profile_user,
    platform_entry_mode,
)

_RUNTIME_ATTRIBUTE = _core._RUNTIME_ATTRIBUTE
_REGISTERED = False
_PROFILE_COPY_LISTENER_REGISTERED = False
_PROFILE_COPY_PREFIX = "dank:profilecopy:v1:"

_defer_private = _core._defer_private
_send_private = _core._send_private
_safe_ephemeral = _core._safe_ephemeral
_settings_payload = _core._settings_payload
_settings_embed = _core._settings_embed
_profile_runtime = _core._profile_runtime

invalidate_member_live_cards = _core.invalidate_member_live_cards
profile_platform = _core.profile_platform
profile_platform_remove = _core.profile_platform_remove
profile_live_cards = _core.profile_live_cards
profile_live_fields = _core.profile_live_fields
profile_live_status = _core.profile_live_status
PublicProfileView = _core.PublicProfileView


class _BackToPrivacyButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Back to Privacy",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank:profilecard:v3:preview_back_privacy",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await profile_settings(interaction)


class _BackToSignatureButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Back to Profile",
            emoji="🪪",
            style=discord.ButtonStyle.secondary,
            custom_id="dank:profilecard:v3:back_signature",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.profile_signature_studio import open_profile_signature_studio

        await open_profile_signature_studio(interaction, replace=True)


class _LiveSignatureToggleButton(discord.ui.Button):
    def __init__(self, *, enabled: bool, row: int = 0) -> None:
        self.enabled = bool(enabled)
        super().__init__(
            label="Turn Off Live Signature" if self.enabled else "Turn On Live Signature",
            emoji="⏸️" if self.enabled else "▶️",
            style=discord.ButtonStyle.danger if self.enabled else discord.ButtonStyle.success,
            custom_id="dank:profilecard:v3:toggle_live_signature",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileSettingsView) or not await view.interaction_check(interaction):
            return
        await _defer_private(interaction, component_update=True)
        try:
            user_row = await _core.get_profile_user(view.author_id, refresh=True)
            guild_row = await _core.get_profile_guild_settings(view.guild_id, view.author_id, refresh=True)
            current = bool(
                _core.effective_preferences(
                    user_row.get("preferences"),
                    guild_row.get("settings"),
                ).get("live_cards_enabled", True)
            )
            if current:
                await _core.upsert_profile_user_preferences(
                    view.author_id,
                    {"live_cards_enabled": False},
                )
            else:
                await _core.upsert_profile_user_preferences(
                    view.author_id,
                    {"live_cards_enabled": True},
                )
                await _core.upsert_profile_guild_settings(
                    view.guild_id,
                    view.author_id,
                    {"live_cards_enabled": None},
                )
            if interaction.guild is not None:
                await invalidate_member_live_cards(
                    interaction.client,
                    interaction.guild,
                    view.author_id,
                    all_guilds=True,
                )
            await view.refresh(interaction)
        except ProfileStorageUnavailable:
            await _safe_ephemeral(interaction, "Private profile storage is unavailable. Nothing changed.", ok=False)


class _ManagePlatformsButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(
            label="Manage Accounts",
            emoji="🎮",
            style=discord.ButtonStyle.success,
            custom_id="dank:profilecard:v3:manage_platforms",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.profile_signature_studio import open_platform_manager

        await open_platform_manager(interaction, replace=True)


class _ProfilePreviewView(discord.ui.View):
    def __init__(self, *, author_id: int, source_view: Optional[discord.ui.View]) -> None:
        super().__init__(timeout=600)
        self.author_id = int(author_id)
        for child in list(getattr(source_view, "children", []) or []):
            if not isinstance(child, discord.ui.Button):
                continue
            if child.url:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Profile")[:80],
                        emoji=child.emoji,
                        style=discord.ButtonStyle.link,
                        url=str(child.url),
                    )
                )
            elif child.custom_id:
                self.add_item(
                    discord.ui.Button(
                        label=str(child.label or "Username")[:80],
                        emoji=child.emoji,
                        style=child.style,
                        custom_id=str(child.custom_id),
                    )
                )
        self.add_item(_BackToPrivacyButton())
        self.add_item(_BackToSignatureButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _safe_ephemeral(interaction, "Only the member who opened this preview can use it.", ok=False)
            return False
        return True


class _PreviewProfileButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Preview Signature",
            emoji="👀",
            style=discord.ButtonStyle.primary,
            custom_id="dank:profilecard:v3:preview_compact",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileSettingsView) or not await view.interaction_check(interaction):
            return
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            return await _safe_ephemeral(interaction, "Could not resolve your server member.", ok=False)
        await _defer_private(interaction, component_update=True)
        try:
            config = await get_guild_config(view.guild_id)
            allowed = set(parse_live_card_config(config).allowed_fields)
            rendered = await render_live_profile_card(
                member,
                allowed,
                trigger_message_id=0,
                require_live_enabled=False,
            )
        except ProfileStorageUnavailable:
            return await interaction.edit_original_response(
                content="❌ Private profile storage is unavailable.",
                embed=None,
                view=ProfileSettingsView(
                    author_id=view.author_id,
                    guild_id=view.guild_id,
                    user_preferences={},
                    guild_settings={},
                ),
                attachments=[],
            )
        if rendered is None:
            return await profile_settings(interaction)
        rendered.embed.set_footer(text="Preview only • compact signature • nothing was posted publicly")
        payload: dict[str, Any] = {
            "content": None,
            "embed": rendered.embed,
            "view": _ProfilePreviewView(author_id=view.author_id, source_view=rendered.view),
            "attachments": [rendered.file] if rendered.file is not None else [],
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        await interaction.edit_original_response(**payload)


class ProfileSettingsView(_core.ProfileSettingsView):
    def __init__(
        self,
        *,
        author_id: int,
        guild_id: int,
        user_preferences: Mapping[str, Any],
        guild_settings: Mapping[str, Any],
    ) -> None:
        discord.ui.View.__init__(self, timeout=300)
        self.author_id = int(author_id)
        self.guild_id = int(guild_id)
        global_values = dict(user_preferences or {})
        local_values = dict(guild_settings or {})
        detail_specs = (
            ("Server Roles", "show_server_roles", "🏷️"),
            ("Profile Tags", "show_profile_tags", "🎭"),
            ("Dates", "show_account_dates", "📅"),
            ("Accounts", "show_platforms", "🔗"),
        )
        effective_values = _core.effective_preferences(global_values, local_values)
        self.add_item(
            _LiveSignatureToggleButton(
                enabled=bool(effective_values.get("live_cards_enabled", True)),
                row=0,
            )
        )
        self.add_item(_ManagePlatformsButton(row=0))
        self.add_item(_PreviewProfileButton(row=0))
        self.add_item(_BackToSignatureButton(row=0))
        for label, key, emoji in detail_specs:
            self.add_item(_core._GlobalPrivacyToggleButton(label, key, global_values, emoji, 1))
        for label, key, emoji in detail_specs:
            self.add_item(_core._GuildPrivacyToggleButton(label, key, local_values, emoji, 2))

    async def refresh(self, interaction: discord.Interaction) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            return await _safe_ephemeral(interaction, "Could not resolve your server member.", ok=False)
        user_row, guild_row, effective = await _settings_payload(self.guild_id, self.author_id)
        payload = {
            "embed": _settings_embed(member, user_row, guild_row, effective),
            "view": ProfileSettingsView(
                author_id=self.author_id,
                guild_id=self.guild_id,
                user_preferences=dict(user_row.get("preferences") or {}),
                guild_settings=dict(guild_row.get("settings") or {}),
            ),
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if interaction.response.is_done():
            await interaction.edit_original_response(**payload)
        else:
            await interaction.response.edit_message(**payload)


async def profile_settings(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _safe_ephemeral(interaction, "Use this command inside a server.", ok=False)
    component_update = getattr(interaction, "type", None) == discord.InteractionType.component
    await _defer_private(interaction, component_update=component_update)
    try:
        user_row, guild_row, effective = await _settings_payload(guild.id, member.id)
    except ProfileStorageUnavailable:
        return await _safe_ephemeral(
            interaction,
            "Private profile storage is not ready. No privacy setting was guessed or changed.",
            ok=False,
        )
    await interaction.edit_original_response(
        content=None,
        embed=_settings_embed(member, user_row, guild_row, effective),
        view=ProfileSettingsView(
            author_id=member.id,
            guild_id=guild.id,
            user_preferences=dict(user_row.get("preferences") or {}),
            guild_settings=dict(guild_row.get("settings") or {}),
        ),
        attachments=[],
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def send_privacy_aware_profile(
    interaction: discord.Interaction,
    member: discord.Member,
) -> None:
    guild = interaction.guild
    if guild is None or int(member.guild.id) != int(guild.id):
        return await _safe_ephemeral(interaction, "That member is not available in this server.", ok=False)
    await _defer_private(interaction)
    try:
        config = parse_live_card_config(await get_guild_config(guild.id))
        effective = await get_effective_profile_settings(guild.id, member.id)
        rendered = await render_live_profile_card(
            member,
            set(config.allowed_fields),
            trigger_message_id=0,
            require_live_enabled=False,
        )
    except ProfileStorageUnavailable:
        return await _safe_ephemeral(
            interaction,
            "Private profile storage is unavailable. Dank Shield will not guess this member's privacy settings.",
            ok=False,
        )

    preferences = dict(effective.get("preferences") or {})
    show_roles = bool(preferences.get("show_profile_tags", True)) and "profile_tags" in config.allowed_fields
    if rendered is None:
        embed = discord.Embed(
            title=member.display_name,
            description="This member has hidden their optional profile details.",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Dank Shield member profile")
        source_view = None
        file = None
    else:
        embed = rendered.embed
        embed.set_footer(text="Dank Shield member profile • compact signature")
        source_view = rendered.view
        file = rendered.file

    view = PublicProfileView(
        member_id=member.id,
        source_view=source_view,
        show_roles=show_roles,
        show_settings=int(interaction.user.id) == int(member.id),
    )
    payload: dict[str, Any] = {
        "embed": embed,
        "view": view if view.children else None,
    }
    if file is not None:
        payload["file"] = file
    await _send_private(interaction, **payload)


async def _handle_profile_username_copy(interaction: discord.Interaction) -> bool:
    """Return one currently-public username in a private, copy-ready response."""
    if interaction.type != discord.InteractionType.component:
        return False
    data = interaction.data or {}
    custom_id = str(data.get("custom_id") or "")
    if not custom_id.startswith(_PROFILE_COPY_PREFIX):
        return False
    parts = custom_id.split(":", 4)
    if len(parts) != 5:
        await _safe_ephemeral(interaction, "That platform username is no longer available.", ok=False)
        return True
    try:
        owner_id = int(parts[3])
    except Exception:
        owner_id = 0
    platform = str(parts[4] or "")
    if interaction.guild is None or owner_id <= 0 or platform not in PLATFORM_SPECS:
        await _safe_ephemeral(interaction, "That platform username is no longer available.", ok=False)
        return True
    try:
        user_row, guild_row = await asyncio.gather(
            get_profile_user(owner_id, refresh=True),
            get_profile_guild_settings(interaction.guild.id, owner_id, refresh=True),
        )
    except ProfileStorageUnavailable:
        await _safe_ephemeral(interaction, "Private profile storage is temporarily unavailable.", ok=False)
        return True
    preferences = effective_preferences(user_row.get("preferences"), guild_row.get("settings"))
    raw = dict(user_row.get("platforms") or {}).get(platform)
    if (
        not bool(preferences.get("show_platforms", True))
        or not isinstance(raw, Mapping)
        or not bool(raw.get("shared"))
        or platform_entry_mode(raw) != "username"
        or not str(raw.get("username") or "").strip()
    ):
        await _safe_ephemeral(interaction, "That member no longer shares this username.", ok=False)
        return True
    username = display_profile_username(raw.get("username"))
    await _send_private(interaction, content=f"```text\n{username}\n```")
    return True


def _attach_profile_commands() -> None:
    command_specs = (
        ("settings", "Open your private profile privacy and platform settings.", profile_settings),
        ("platform", "Save or update one private/shared platform identity.", profile_platform),
        ("platform-remove", "Remove one saved platform identity.", profile_platform_remove),
        ("live-cards", "Manager fallback: toggle one channel; the full picker is in /dank setup.", profile_live_cards),
        ("live-fields", "Manager: restrict fields compact profile signatures may display.", profile_live_fields),
        ("live-status", "Manager: view compact profile signature configuration.", profile_live_status),
    )
    for name, description, callback in command_specs:
        if profile_group.get_command(name) is not None:
            continue
        profile_group.add_command(app_commands.Command(name=name, description=description, callback=callback))


def register_public_profile_cards(bot: Any, tree: Any) -> None:
    del tree
    global _REGISTERED, _PROFILE_COPY_LISTENER_REGISTERED
    _attach_profile_commands()
    if bot is None:
        return
    runtime = getattr(bot, _RUNTIME_ATTRIBUTE, None)
    if not isinstance(runtime, LiveProfileCardRuntime):
        runtime = LiveProfileCardRuntime(bot)
        setattr(bot, _RUNTIME_ATTRIBUTE, runtime)
        bot.add_listener(runtime.on_message, "on_message")
        bot.add_listener(runtime.on_ready, "on_ready")
        bot.add_listener(runtime.on_member_remove, "on_member_remove")
        bot.add_listener(runtime.on_guild_channel_delete, "on_guild_channel_delete")
    if not _PROFILE_COPY_LISTENER_REGISTERED:
        @bot.listen("on_interaction")
        async def _dank_profile_username_copy_listener(interaction: discord.Interaction) -> None:
            try:
                await _handle_profile_username_copy(interaction)
            except Exception as exc:
                print(f"⚠️ profile username copy failed: {type(exc).__name__}: {exc}")
        _PROFILE_COPY_LISTENER_REGISTERED = True
    if not _REGISTERED:
        _REGISTERED = True
        print("✅ public_profile_cards: attached compact signatures, privacy, platforms, and lifecycle controls")


__all__ = [
    "ProfileSettingsView",
    "profile_live_cards",
    "profile_live_fields",
    "profile_live_status",
    "profile_platform",
    "profile_platform_remove",
    "profile_settings",
    "invalidate_member_live_cards",
    "send_privacy_aware_profile",
    "register_public_profile_cards",
]
