from __future__ import annotations

"""Use a real channel picker for Invite Shield cleanup target selection."""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_CALLBACK: Any = None
PAGE_SIZE = 25


def _text_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    channels = [channel for channel in getattr(guild, "text_channels", []) if isinstance(channel, discord.TextChannel)]
    return sorted(channels, key=lambda ch: ((getattr(ch.category, "position", 9999) if ch.category else 9999), getattr(ch, "position", 9999), str(ch.name).lower()))


def _label(channel: discord.TextChannel) -> str:
    name = str(getattr(channel, "name", "channel") or "channel")
    return name[:100]


def _description(channel: discord.TextChannel) -> str:
    category = getattr(getattr(channel, "category", None), "name", None) or "No category"
    text = f"{category} • ID {channel.id}"
    return text[:100]


class InviteCleanupChannelSelect(discord.ui.Select):
    def __init__(self, *, guild: discord.Guild, page: int) -> None:
        self.guild = guild
        self.page = max(0, int(page))
        channels = _text_channels(guild)
        start = self.page * PAGE_SIZE
        page_channels = channels[start:start + PAGE_SIZE]
        options = [
            discord.SelectOption(label=_label(channel), value=str(channel.id), description=_description(channel), emoji="#️⃣")
            for channel in page_channels
        ] or [discord.SelectOption(label="No text channels found", value="0", description="Use Paste Raw ID if needed.")]
        total_pages = max(1, (len(channels) + PAGE_SIZE - 1) // PAGE_SIZE)
        super().__init__(placeholder=f"Choose channel to clean • page {self.page + 1}/{total_pages}", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.startup_guards import protection_invite_toggle_cleanup_guard as cleanup
        from stoney_verify.commands_ext import public_protection_center as center
        if not await center._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
        channel_id = int(self.values[0]) if self.values and str(self.values[0]).isdigit() else 0
        channel = guild.get_channel(channel_id) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.edit_message(content="⚠️ That channel is no longer available. Reopen the picker and try again.", embed=None, view=None)
        result = await cleanup._clean_existing_invites(channel, limit=200)
        note = cleanup._scan_note(f"🧹 Clean {channel.mention}", result)
        await cleanup._refresh_card(center, interaction, note=note)


class InviteCleanupPickerView(discord.ui.View):
    def __init__(self, *, guild: discord.Guild, page: int = 0) -> None:
        super().__init__(timeout=300)
        self.guild = guild
        self.page = max(0, int(page))
        channels = _text_channels(guild)
        self.total_pages = max(1, (len(channels) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(self.page, self.total_pages - 1)
        self.add_item(InviteCleanupChannelSelect(guild=guild, page=self.page))

    async def _turn_page(self, interaction: discord.Interaction, page: int) -> None:
        embed = _picker_embed(self.guild, page)
        await interaction.response.edit_message(embed=embed, view=InviteCleanupPickerView(guild=self.guild, page=page), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="◀ Channels", style=discord.ButtonStyle.secondary, row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._turn_page(interaction, max(0, self.page - 1))

    @discord.ui.button(label="Channels ▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._turn_page(interaction, min(self.total_pages - 1, self.page + 1))

    @discord.ui.button(label="Paste Raw ID", emoji="✍️", style=discord.ButtonStyle.secondary, row=2)
    async def paste_raw_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.startup_guards.protection_invite_toggle_cleanup_guard import TargetChannelCleanupModal
        await interaction.response.send_modal(TargetChannelCleanupModal())

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary, row=2)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Invite cleanup channel picker closed.", embed=None, view=None)


def _picker_embed(guild: discord.Guild, page: int = 0) -> discord.Embed:
    channels = _text_channels(guild)
    total_pages = max(1, (len(channels) + PAGE_SIZE - 1) // PAGE_SIZE)
    embed = discord.Embed(
        title="🎯 Clean Invite Links — Pick Channel",
        description=(
            "Choose the channel from the dropdown. You do **not** need to type the channel name.\n\n"
            "Use **Paste Raw ID** only if Discord fails to show a channel."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Channels found", value=f"`{len(channels)}` text channels • page `{min(page + 1, total_pages)}/{total_pages}`", inline=False)
    embed.set_footer(text="This scans only the selected channel and still preserves allowed/internal server invites.")
    return embed


async def _open_picker(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_protection_center as center
    if not await center._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await center._send_ephemeral(interaction, "❌ This must be used inside a server.")
    await interaction.response.send_message(embed=_picker_embed(guild), view=InviteCleanupPickerView(guild=guild), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def apply() -> bool:
    global _PATCHED, _ORIGINAL_CALLBACK
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import protection_invite_toggle_cleanup_guard as cleanup
        _ORIGINAL_CALLBACK = cleanup.CleanTargetChannelInvites.callback
        cleanup.CleanTargetChannelInvites.callback = lambda self, interaction: _open_picker(interaction)
        _PATCHED = True
        print("✅ protection_invite_cleanup_picker_guard active; invite cleanup uses a channel picker")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ protection_invite_cleanup_picker_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]