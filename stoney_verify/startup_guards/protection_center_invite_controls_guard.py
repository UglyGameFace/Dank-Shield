from __future__ import annotations

"""Expose clear invite hard-block controls on the main Protection Center.

The SpamGuard Access page owns the detailed storage helpers, but server owners
expect invite controls on `/dank protection` because that is the unified
Protection Center. This guard adds policy/scope controls directly to that
surface, supports Discord selects so owners do not have to paste raw IDs, and
refreshes the Protection Center message after saving.
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


async def _refresh_original_protection_message(
    *,
    guild: discord.Guild,
    author_id: int,
    channel_id: int,
    message_id: int,
    center: Any,
) -> None:
    cfg, spam, spam_source = await _load_protection_payload(guild, center)
    embed = center._protection_embed(guild, cfg, spam, spam_source)
    view = center.ProtectionCenterView(author_id=int(author_id))
    channel = guild.get_channel(int(channel_id))
    if isinstance(channel, discord.TextChannel):
        message = await channel.fetch_message(int(message_id))
        await message.edit(content=None, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


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
        await _refresh_original_protection_message(
            guild=guild,
            author_id=int(interaction.user.id),
            channel_id=channel_id,
            message_id=message_id,
            center=center,
        )
        try:
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass
    except Exception as exc:
        try:
            await center._refresh_panel(
                interaction,
                content=f"{content}\n⚠️ Could not edit original Protection Center message: `{type(exc).__name__}: {_short(exc, 160)}`",
            )
        except Exception:
            pass


def _format_override_status(policy: Any, settings: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, label in policy._OVERRIDE_KEYS:
        locked = policy._safe_bool(settings.get(key), False)
        lines.append(f"**{label}:** {'🚫 ignored by hard block' if locked else '✅ honored'}")
    return "\n".join(lines)


def _format_scope_status(policy: Any, settings: dict[str, Any]) -> str:
    bot_ids = policy._parse_ids(settings.get("invite_hard_block_target_bot_ids"))
    channel_ids = policy._parse_ids(settings.get("invite_hard_block_target_channel_ids"))
    return (
        f"**Bot/user targets:** {', '.join(f'`{x}`' for x in bot_ids) if bot_ids else 'Humans only; bots ignored unless listed'}\n"
        f"**Channel targets:** {', '.join(f'`{x}`' for x in channel_ids) if channel_ids else 'All text channels'}"
    )


def _scope_editor_embed(policy: Any, settings: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title="🎯 Invite Hard-Block Scope",
        description=(
            "Choose who and where invite-link deletion applies. Use the selects when possible; "
            "use Manual IDs only for bulk paste."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Current scope", value=_format_scope_status(policy, settings), inline=False)
    embed.add_field(
        name="How to add more",
        value=(
            "**Bot/user select:** adds selected humans or bots to the target list.\n"
            "**Channel select:** adds selected text channels to the target list.\n"
            "**Manual IDs:** paste IDs separated by comma, space, semicolon, or new line. Mentions like `<@123>` and `<#123>` are accepted.\n"
            "Blank target lists mean: humans only, all text channels."
        ),
        inline=False,
    )
    return embed


class ProtectionInviteOverrideModal(discord.ui.Modal, title="Invite Override Rules"):
    def __init__(self, *, channel_id: int, message_id: int, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        _center, _spam_guard, policy = _patch_helpers()
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.fields: dict[str, discord.ui.TextInput] = {}
        for key, label in policy._OVERRIDE_KEYS:
            item = discord.ui.TextInput(
                label=f"Ignore {label}?",
                placeholder="yes = delete invites anyway, no = honor this bucket",
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
            "✅ Invite override rules saved from Protection Center.\n"
            + _format_override_status(policy, settings)
            + f"\nPersistence: `{spam_guard._build_persistence_label(int(guild.id), persisted)}`"
        )
        await _edit_protection_message(interaction, channel_id=self.channel_id, message_id=self.message_id, content=body, center=center)


class ProtectionInviteScopeManualModal(discord.ui.Modal, title="Manual Invite Scope IDs"):
    def __init__(self, *, channel_id: int, message_id: int, settings: dict[str, Any]) -> None:
        super().__init__(timeout=300)
        _center, _spam_guard, policy = _patch_helpers()
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.bot_ids = discord.ui.TextInput(
            label="Bot/user IDs to target",
            placeholder="Example: 123, 456 or paste <@123> <@456>. Blank clears bot/user targets.",
            default=policy._ids_text(settings.get("invite_hard_block_target_bot_ids")),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=800,
        )
        self.channel_ids = discord.ui.TextInput(
            label="Channel IDs to target",
            placeholder="Example: 123, 456 or paste <#123> <#456>. Blank = all text channels.",
            default=policy._ids_text(settings.get("invite_hard_block_target_channel_ids")),
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=800,
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
            "✅ Manual invite scope saved.\n"
            + _format_scope_status(policy, settings)
            + f"\nPersistence: `{spam_guard._build_persistence_label(int(guild.id), persisted)}`"
        )
        await _edit_protection_message(interaction, channel_id=self.channel_id, message_id=self.message_id, content=body, center=center)


class ScopeUserSelect(discord.ui.UserSelect):
    def __init__(self, *, channel_id: int, message_id: int) -> None:
        super().__init__(placeholder="Add bots/users to invite hard-block target list", min_values=1, max_values=25, row=0)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        current = await spam_guard.get_spam_settings(int(guild.id))
        ids = policy._parse_ids(current.get("invite_hard_block_target_bot_ids"))
        for user in self.values:
            text = str(getattr(user, "id", "") or "")
            if text and text not in ids:
                ids.append(text)
        settings, _persisted = await spam_guard.save_spam_settings(int(guild.id), {"invite_hard_block_target_bot_ids": ids}, updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None)
        try:
            await _refresh_original_protection_message(guild=guild, author_id=int(interaction.user.id), channel_id=self.channel_id, message_id=self.message_id, center=center)
        except Exception:
            pass
        await interaction.response.edit_message(embed=_scope_editor_embed(policy, settings), view=InviteScopeEditorView(channel_id=self.channel_id, message_id=self.message_id, settings=settings))


class ScopeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, channel_id: int, message_id: int) -> None:
        try:
            super().__init__(placeholder="Add text channels to invite hard-block target list", min_values=1, max_values=25, channel_types=[discord.ChannelType.text], row=1)
        except TypeError:
            super().__init__(placeholder="Add text channels to invite hard-block target list", min_values=1, max_values=25, row=1)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        current = await spam_guard.get_spam_settings(int(guild.id))
        ids = policy._parse_ids(current.get("invite_hard_block_target_channel_ids"))
        for channel in self.values:
            text = str(getattr(channel, "id", "") or "")
            if text and text not in ids:
                ids.append(text)
        settings, _persisted = await spam_guard.save_spam_settings(int(guild.id), {"invite_hard_block_target_channel_ids": ids}, updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None)
        try:
            await _refresh_original_protection_message(guild=guild, author_id=int(interaction.user.id), channel_id=self.channel_id, message_id=self.message_id, center=center)
        except Exception:
            pass
        await interaction.response.edit_message(embed=_scope_editor_embed(policy, settings), view=InviteScopeEditorView(channel_id=self.channel_id, message_id=self.message_id, settings=settings))


class InviteScopeEditorView(discord.ui.View):
    def __init__(self, *, channel_id: int, message_id: int, settings: dict[str, Any]) -> None:
        super().__init__(timeout=600)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.add_item(ScopeUserSelect(channel_id=channel_id, message_id=message_id))
        self.add_item(ScopeChannelSelect(channel_id=channel_id, message_id=message_id))

    @discord.ui.button(label="Manual IDs", emoji="✍️", style=discord.ButtonStyle.secondary, row=2)
    async def manual_ids(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        center, spam_guard, _policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        settings = await spam_guard.get_spam_settings(int(guild.id))
        await interaction.response.send_modal(ProtectionInviteScopeManualModal(channel_id=self.channel_id, message_id=self.message_id, settings=settings))

    @discord.ui.button(label="Clear Scope", emoji="🧹", style=discord.ButtonStyle.danger, row=2)
    async def clear_scope(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        settings, _persisted = await spam_guard.save_spam_settings(
            int(guild.id),
            {"invite_hard_block_target_bot_ids": [], "invite_hard_block_target_channel_ids": []},
            updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        try:
            await _refresh_original_protection_message(guild=guild, author_id=int(interaction.user.id), channel_id=self.channel_id, message_id=self.message_id, center=center)
        except Exception:
            pass
        await interaction.response.edit_message(embed=_scope_editor_embed(policy, settings), view=InviteScopeEditorView(channel_id=self.channel_id, message_id=self.message_id, settings=settings))

    @discord.ui.button(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=2)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Invite scope editor closed. Reopen it from Protection Center if needed.", embed=None, view=None)


class ProtectionInviteOverrideButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Override Rules",
            emoji="⚙️",
            style=discord.ButtonStyle.secondary,
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
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        message = interaction.message
        if guild is None or message is None:
            return await center._send_ephemeral(interaction, "❌ Invalid Protection Center context.")
        settings = await spam_guard.get_spam_settings(int(guild.id))
        await interaction.response.send_message(
            embed=_scope_editor_embed(policy, settings),
            view=InviteScopeEditorView(channel_id=int(getattr(message.channel, "id", 0) or 0), message_id=int(message.id), settings=settings),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def _invite_controls_value(policy: Any, spam: dict[str, Any]) -> str:
    return (
        "**Override rules**\n"
        + _format_override_status(policy, spam)
        + "\n\n**Scope**\n"
        + _format_scope_status(policy, spam)
        + "\n\n**Override Rules** = decide whether allow/exempt buckets are honored.\n"
        + "**Invite Scope** = choose bots/users or channels with Discord selects; Manual IDs accepts comma, space, semicolon, newline, user mentions, or channel mentions."
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
        print("✅ protection_center_invite_controls_guard active; Protection Center has clearer Override Rules + Invite Scope")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_invite_controls_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
