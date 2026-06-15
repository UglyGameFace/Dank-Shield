from __future__ import annotations

"""Expose invite hard-block controls on the main Protection Center.

The SpamGuard Access page owns the detailed storage helpers, but server owners
expect the invite controls on `/dank protection` because that is the unified
Protection Center. This guard adds the same policy/scope controls directly to
that surface and refreshes the Protection Center message after saving.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_EMBED: Any = None
_ORIGINAL_VIEW_INIT: Any = None


def _short(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _patch_helpers() -> tuple[Any, Any, Any]:
    from stoney_verify import spam_guard
    from stoney_verify.commands_ext import public_protection_center as center
    from stoney_verify.startup_guards import spam_guard_invite_override_options as policy

    try:
        policy.apply()
    except Exception:
        pass
    return center, spam_guard, policy


async def _load_protection_payload(guild: discord.Guild, center: Any) -> tuple[Any, dict[str, Any], str]:
    cfg = await center.get_guild_config(int(guild.id), refresh=True)
    spam, spam_source = await center._load_spam_settings(int(guild.id))
    return cfg, spam, spam_source


async def _edit_protection_message(
    interaction: discord.Interaction,
    *,
    channel_id: int,
    message_id: int,
    content: str,
    center: Any,
) -> None:
    guild = interaction.guild
    if guild is None:
        return
    try:
        cfg, spam, spam_source = await _load_protection_payload(guild, center)
        embed = center._protection_embed(guild, cfg, spam, spam_source)
        view = center.ProtectionCenterView(author_id=int(interaction.user.id))
        channel = guild.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel):
            message = await channel.fetch_message(int(message_id))
            await message.edit(content=None, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        try:
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass
    except Exception as exc:
        try:
            await center._refresh_panel(interaction, content=f"{content}\n⚠️ Could not edit original Protection Center message: `{type(exc).__name__}: {_short(exc, 160)}`")
        except Exception:
            pass


class ProtectionInviteOverrideModal(discord.ui.Modal, title="Invite Override Policy"):
    def __init__(self, *, channel_id: int, message_id: int, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        _center, _spam_guard, policy = _patch_helpers()
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.fields: dict[str, discord.ui.TextInput] = {}
        for key, label in policy._OVERRIDE_KEYS:
            item = discord.ui.TextInput(
                label=f"Override {label}",
                placeholder="yes = block anyway, no = honor this bucket",
                default=policy._yes_no(settings.get(key)),
                required=True,
                max_length=8,
            )
            self.fields[key] = item
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        try:
            patch = {key: policy._parse_yes_no(self.fields[key].value, label=label) for key, label in policy._OVERRIDE_KEYS}
        except Exception as exc:
            return await center._send_ephemeral(interaction, f"❌ {exc}")
        settings, persisted = await spam_guard.save_spam_settings(
            int(guild.id),
            patch,
            updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        body = (
            "✅ Invite override policy saved from Protection Center.\n"
            + policy._override_summary(settings)
            + f"\nPersistence: `{spam_guard._build_persistence_label(int(guild.id), persisted)}`"
        )
        await _edit_protection_message(interaction, channel_id=self.channel_id, message_id=self.message_id, content=body, center=center)


class ProtectionInviteScopeModal(discord.ui.Modal, title="Invite Hard-Block Scope"):
    def __init__(self, *, channel_id: int, message_id: int, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        _center, _spam_guard, policy = _patch_helpers()
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.bot_ids = discord.ui.TextInput(
            label="Target bot/user IDs",
            placeholder="Blank = humans only. Add bot IDs to police those bots too.",
            default=policy._ids_text(settings.get("invite_hard_block_target_bot_ids")),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.channel_ids = discord.ui.TextInput(
            label="Target channel IDs",
            placeholder="Blank = all text channels. Add channel IDs to limit enforcement.",
            default=policy._ids_text(settings.get("invite_hard_block_target_channel_ids")),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.bot_ids)
        self.add_item(self.channel_ids)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        patch = {
            "invite_hard_block_target_bot_ids": policy._parse_ids(self.bot_ids.value),
            "invite_hard_block_target_channel_ids": policy._parse_ids(self.channel_ids.value),
        }
        settings, persisted = await spam_guard.save_spam_settings(
            int(guild.id),
            patch,
            updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        body = (
            "✅ Invite hard-block scope saved from Protection Center.\n"
            + policy._scope_summary(settings)
            + f"\nPersistence: `{spam_guard._build_persistence_label(int(guild.id), persisted)}`"
        )
        await _edit_protection_message(interaction, channel_id=self.channel_id, message_id=self.message_id, content=body, center=center)


class ProtectionInviteOverrideButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Invite Override",
            emoji="🚫",
            style=discord.ButtonStyle.danger,
            custom_id="dank_protection:invite_override",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        center, spam_guard, _policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        message = interaction.message
        if guild is None or message is None:
            return await center._send_ephemeral(interaction, "❌ Invalid Protection Center context.")
        settings = await spam_guard.get_spam_settings(int(guild.id))
        await interaction.response.send_modal(
            ProtectionInviteOverrideModal(
                channel_id=int(getattr(message.channel, "id", 0) or 0),
                message_id=int(message.id),
                settings=settings,
            )
        )


class ProtectionInviteScopeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Invite Scope",
            emoji="🎯",
            style=discord.ButtonStyle.primary,
            custom_id="dank_protection:invite_scope",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        center, spam_guard, _policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        message = interaction.message
        if guild is None or message is None:
            return await center._send_ephemeral(interaction, "❌ Invalid Protection Center context.")
        settings = await spam_guard.get_spam_settings(int(guild.id))
        await interaction.response.send_modal(
            ProtectionInviteScopeModal(
                channel_id=int(getattr(message.channel, "id", 0) or 0),
                message_id=int(message.id),
                settings=settings,
            )
        )


def _invite_controls_value(policy: Any, spam: dict[str, Any]) -> str:
    override = policy._override_summary(spam)
    scope = policy._scope_summary(spam)
    return (
        "**Override policy**\n"
        + override
        + "\n\n**Scope**\n"
        + scope
        + "\n\nUse **Invite Override** to ignore allow/exempt buckets. Use **Invite Scope** to target specific bots/users or channels."
    )[:1024]


def apply() -> bool:
    global _PATCHED, _ORIGINAL_EMBED, _ORIGINAL_VIEW_INIT
    if _PATCHED:
        return True
    try:
        center, _spam_guard, policy = _patch_helpers()
        _ORIGINAL_EMBED = center._protection_embed
        _ORIGINAL_VIEW_INIT = center.ProtectionCenterView.__init__

        def patched_embed(guild: discord.Guild, cfg: Any, spam: dict[str, Any], spam_source: str) -> discord.Embed:
            embed = _ORIGINAL_EMBED(guild, cfg, spam, spam_source)
            try:
                embed.add_field(name="Invite & Link Controls", value=_invite_controls_value(policy, spam), inline=False)
            except Exception:
                pass
            return embed

        def patched_init(self, *, author_id: int) -> None:
            _ORIGINAL_VIEW_INIT(self, author_id=author_id)
            existing = {str(getattr(child, "custom_id", "") or "") for child in getattr(self, "children", [])}
            if "dank_protection:invite_override" not in existing:
                try:
                    self.add_item(ProtectionInviteOverrideButton())
                except Exception:
                    pass
            if "dank_protection:invite_scope" not in existing:
                try:
                    self.add_item(ProtectionInviteScopeButton())
                except Exception:
                    pass

        center._protection_embed = patched_embed
        center.ProtectionCenterView.__init__ = patched_init
        _PATCHED = True
        print("✅ protection_center_invite_controls_guard active; Protection Center has Invite Override + Invite Scope")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_invite_controls_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
