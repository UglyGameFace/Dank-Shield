from __future__ import annotations

"""Expose clear invite hard-block controls on the main Protection Center.

Discord's native UserSelect/ChannelSelect can hide members/channels depending on
client search and visibility. This guard uses Dank Shield-owned paginated string
selects built from the guild cache so the Protection Center can show every bot
and every text channel the bot can see, plus a manual-ID fallback.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_EMBED: Any = None
_ORIGINAL_VIEW_INIT: Any = None
_PAGE_SIZE = 25


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


def _all_bot_choices(guild: discord.Guild, selected_ids: list[str]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for member in sorted([m for m in getattr(guild, "members", []) if getattr(m, "bot", False)], key=lambda m: str(getattr(m, "display_name", getattr(m, "name", ""))).lower()):
        mid = str(getattr(member, "id", "") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        name = str(getattr(member, "display_name", getattr(member, "name", mid)) or mid)
        found.append((mid, _short(name, 95), f"ID {mid}"))
    for mid in selected_ids:
        if mid not in seen:
            found.append((mid, f"Saved bot/user {mid}", "Saved ID not currently visible in member cache"))
            seen.add(mid)
    return found


def _all_channel_choices(guild: discord.Guild, selected_ids: list[str]) -> list[tuple[str, str, str]]:
    channels = list(getattr(guild, "text_channels", []) or [])
    channels.sort(key=lambda c: (str(getattr(getattr(c, "category", None), "name", "") or "").lower(), int(getattr(c, "position", 0) or 0), str(getattr(c, "name", "")).lower()))
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for channel in channels:
        cid = str(getattr(channel, "id", "") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        category = getattr(getattr(channel, "category", None), "name", None)
        desc = f"#{getattr(channel, 'name', cid)}"
        if category:
            desc = f"{category} / {desc}"
        found.append((cid, _short(f"#{getattr(channel, 'name', cid)}", 95), _short(f"ID {cid} • {desc}", 95)))
    for cid in selected_ids:
        if cid not in seen:
            found.append((cid, f"Saved channel {cid}", "Saved ID not currently visible to the bot"))
            seen.add(cid)
    return found


def _page_count(items: list[Any]) -> int:
    return max(1, (len(items) + _PAGE_SIZE - 1) // _PAGE_SIZE)


def _options_for(items: list[tuple[str, str, str]], page: int, *, empty_label: str) -> list[discord.SelectOption]:
    if not items:
        return [discord.SelectOption(label=empty_label[:100], value="__none__", description="Use Manual IDs if the item is missing.")]
    page = max(0, min(page, _page_count(items) - 1))
    chunk = items[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]
    return [discord.SelectOption(label=label[:100], value=value, description=desc[:100]) for value, label, desc in chunk]


def _scope_editor_embed(policy: Any, guild: discord.Guild, settings: dict[str, Any], *, bot_page: int = 0, channel_page: int = 0) -> discord.Embed:
    bot_ids = policy._parse_ids(settings.get("invite_hard_block_target_bot_ids"))
    channel_ids = policy._parse_ids(settings.get("invite_hard_block_target_channel_ids"))
    bots = _all_bot_choices(guild, bot_ids)
    channels = _all_channel_choices(guild, channel_ids)
    embed = discord.Embed(
        title="🎯 Invite Hard-Block Scope",
        description=(
            "Dank Shield lists every bot and text channel it can see with paged menus. "
            "Use Manual IDs for humans, uncached bots, or bulk paste."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Current scope", value=_format_scope_status(policy, settings), inline=False)
    embed.add_field(
        name="Paged selectors",
        value=(
            f"**Bots page:** {min(bot_page + 1, _page_count(bots))}/{_page_count(bots)} • visible bots: {len(bots)}\n"
            f"**Channels page:** {min(channel_page + 1, _page_count(channels))}/{_page_count(channels)} • visible text channels: {len(channels)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Manual IDs format",
        value=(
            "Paste IDs separated by **comma, space, semicolon, or new line**. Mentions like `<@123>`, `<@!123>`, and `<#123>` are accepted.\n"
            "Blank bot/user targets = humans only; bots ignored unless listed. Blank channel targets = all text channels."
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


class BotTargetSelect(discord.ui.Select):
    def __init__(self, *, guild: discord.Guild, settings: dict[str, Any], channel_id: int, message_id: int, bot_page: int, channel_page: int) -> None:
        _center, _spam_guard, policy = _patch_helpers()
        selected = policy._parse_ids(settings.get("invite_hard_block_target_bot_ids"))
        bots = _all_bot_choices(guild, selected)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.bot_page = max(0, min(int(bot_page), _page_count(bots) - 1))
        self.channel_page = int(channel_page)
        super().__init__(
            placeholder=f"Add bot target • page {self.bot_page + 1}/{_page_count(bots)}",
            min_values=1,
            max_values=1,
            options=_options_for(bots, self.bot_page, empty_label="No visible bots found"),
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        value = str(self.values[0])
        if value == "__none__":
            return await interaction.response.send_message("No visible bot was found. Use **Manual IDs** for bots/users that are not listed.", ephemeral=True)
        current = await spam_guard.get_spam_settings(int(guild.id))
        ids = policy._parse_ids(current.get("invite_hard_block_target_bot_ids"))
        if value not in ids:
            ids.append(value)
        settings, _persisted = await spam_guard.save_spam_settings(int(guild.id), {"invite_hard_block_target_bot_ids": ids}, updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None)
        try:
            await _refresh_original_protection_message(guild=guild, author_id=int(interaction.user.id), channel_id=self.channel_id, message_id=self.message_id, center=center)
        except Exception:
            pass
        await interaction.response.edit_message(embed=_scope_editor_embed(policy, guild, settings, bot_page=self.bot_page, channel_page=self.channel_page), view=InviteScopeEditorView(channel_id=self.channel_id, message_id=self.message_id, settings=settings, bot_page=self.bot_page, channel_page=self.channel_page, guild=guild))


class ChannelTargetSelect(discord.ui.Select):
    def __init__(self, *, guild: discord.Guild, settings: dict[str, Any], channel_id: int, message_id: int, bot_page: int, channel_page: int) -> None:
        _center, _spam_guard, policy = _patch_helpers()
        selected = policy._parse_ids(settings.get("invite_hard_block_target_channel_ids"))
        channels = _all_channel_choices(guild, selected)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.bot_page = int(bot_page)
        self.channel_page = max(0, min(int(channel_page), _page_count(channels) - 1))
        super().__init__(
            placeholder=f"Add channel target • page {self.channel_page + 1}/{_page_count(channels)}",
            min_values=1,
            max_values=1,
            options=_options_for(channels, self.channel_page, empty_label="No visible text channels found"),
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        value = str(self.values[0])
        if value == "__none__":
            return await interaction.response.send_message("No visible text channel was found. Use **Manual IDs** for channels that are not listed.", ephemeral=True)
        current = await spam_guard.get_spam_settings(int(guild.id))
        ids = policy._parse_ids(current.get("invite_hard_block_target_channel_ids"))
        if value not in ids:
            ids.append(value)
        settings, _persisted = await spam_guard.save_spam_settings(int(guild.id), {"invite_hard_block_target_channel_ids": ids}, updated_by=interaction.user if isinstance(interaction.user, discord.Member) else None)
        try:
            await _refresh_original_protection_message(guild=guild, author_id=int(interaction.user.id), channel_id=self.channel_id, message_id=self.message_id, center=center)
        except Exception:
            pass
        await interaction.response.edit_message(embed=_scope_editor_embed(policy, guild, settings, bot_page=self.bot_page, channel_page=self.channel_page), view=InviteScopeEditorView(channel_id=self.channel_id, message_id=self.message_id, settings=settings, bot_page=self.bot_page, channel_page=self.channel_page, guild=guild))


class InviteScopeEditorView(discord.ui.View):
    def __init__(self, *, channel_id: int, message_id: int, settings: dict[str, Any], bot_page: int = 0, channel_page: int = 0, guild: discord.Guild | None = None) -> None:
        super().__init__(timeout=600)
        self.channel_id = int(channel_id)
        self.message_id = int(message_id)
        self.settings = dict(settings or {})
        self.bot_page = max(0, int(bot_page))
        self.channel_page = max(0, int(channel_page))
        if guild is not None:
            self.add_item(BotTargetSelect(guild=guild, settings=self.settings, channel_id=self.channel_id, message_id=self.message_id, bot_page=self.bot_page, channel_page=self.channel_page))
            self.add_item(ChannelTargetSelect(guild=guild, settings=self.settings, channel_id=self.channel_id, message_id=self.message_id, bot_page=self.bot_page, channel_page=self.channel_page))

    async def _redraw(self, interaction: discord.Interaction, *, bot_delta: int = 0, channel_delta: int = 0) -> None:
        center, spam_guard, policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        settings = await spam_guard.get_spam_settings(int(guild.id))
        bots = _all_bot_choices(guild, policy._parse_ids(settings.get("invite_hard_block_target_bot_ids")))
        channels = _all_channel_choices(guild, policy._parse_ids(settings.get("invite_hard_block_target_channel_ids")))
        next_bot_page = max(0, min(self.bot_page + bot_delta, _page_count(bots) - 1))
        next_channel_page = max(0, min(self.channel_page + channel_delta, _page_count(channels) - 1))
        await interaction.response.edit_message(
            embed=_scope_editor_embed(policy, guild, settings, bot_page=next_bot_page, channel_page=next_channel_page),
            view=InviteScopeEditorView(channel_id=self.channel_id, message_id=self.message_id, settings=settings, bot_page=next_bot_page, channel_page=next_channel_page, guild=guild),
        )

    @discord.ui.button(label="◀ Bots", style=discord.ButtonStyle.secondary, row=2)
    async def prev_bots(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redraw(interaction, bot_delta=-1)

    @discord.ui.button(label="Bots ▶", style=discord.ButtonStyle.secondary, row=2)
    async def next_bots(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redraw(interaction, bot_delta=1)

    @discord.ui.button(label="◀ Channels", style=discord.ButtonStyle.secondary, row=3)
    async def prev_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redraw(interaction, channel_delta=-1)

    @discord.ui.button(label="Channels ▶", style=discord.ButtonStyle.secondary, row=3)
    async def next_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._redraw(interaction, channel_delta=1)

    @discord.ui.button(label="Manual IDs", emoji="✍️", style=discord.ButtonStyle.primary, row=4)
    async def manual_ids(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        center, spam_guard, _policy = _patch_helpers()
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        settings = await spam_guard.get_spam_settings(int(guild.id))
        await interaction.response.send_modal(ProtectionInviteScopeManualModal(channel_id=self.channel_id, message_id=self.message_id, settings=settings))

    @discord.ui.button(label="Clear", emoji="🧹", style=discord.ButtonStyle.danger, row=4)
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
        await interaction.response.edit_message(embed=_scope_editor_embed(policy, guild, settings, bot_page=0, channel_page=0), view=InviteScopeEditorView(channel_id=self.channel_id, message_id=self.message_id, settings=settings, guild=guild))

    @discord.ui.button(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=4)
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
            embed=_scope_editor_embed(policy, guild, settings, bot_page=0, channel_page=0),
            view=InviteScopeEditorView(channel_id=int(getattr(message.channel, "id", 0) or 0), message_id=int(message.id), settings=settings, guild=guild),
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
        + "**Invite Scope** = paged bot/channel lists from the guild plus Manual IDs for bulk paste."
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
        print("✅ protection_center_invite_controls_guard active; Protection Center has paged bot/channel invite scope controls")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_center_invite_controls_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
