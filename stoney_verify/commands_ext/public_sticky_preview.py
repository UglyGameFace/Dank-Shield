from __future__ import annotations

"""Private preview and non-persistent temporary test delivery for stickies."""

from dataclasses import replace
from typing import Any, Optional

import discord

from stoney_verify.community_tools_runtime import ensure_community_tools_runtime, sticky_embed, sticky_poll_embed
from stoney_verify.community_tools_service import (
    CommunityStorageUnavailable,
    InvalidCommunityToolValue,
    StickyConfig,
    StickyPoll,
    get_sticky,
    normalize_sticky,
    save_sticky,
)

_ALLOWED_MENTIONS = discord.AllowedMentions.none()


def _manage_messages(interaction: discord.Interaction) -> bool:
    member = interaction.user
    return bool(
        isinstance(member, discord.Member)
        and (member.guild_permissions.administrator or member.guild_permissions.manage_messages)
    )


def _text_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


async def _private(
    interaction: discord.Interaction,
    content: str,
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "content": content,
        "ephemeral": True,
        "allowed_mentions": _ALLOWED_MENTIONS,
    }
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


def _preview_payload(config: StickyConfig, poll: Optional[StickyPoll], *, draft: bool) -> tuple[str, Optional[discord.Embed]]:
    prefix = (
        "👁️ **Draft preview** — nothing has changed live yet."
        if draft
        else "👁️ **Private sticky preview** — only you can see this."
    )
    content = prefix
    embed: Optional[discord.Embed] = None
    if config.mode == "plain":
        content += f"\n\n{config.content}"
    elif config.mode == "embed":
        embed = sticky_embed(config)
    elif config.mode == "poll" and poll is not None:
        content += "\nVoting buttons are intentionally disabled in preview/test mode."
        embed = sticky_poll_embed(poll)
    else:
        content += "\n\n⚠️ Preview data is incomplete for this sticky."

    if config.use_webhook:
        content += f"\n\n🎭 **Live sender:** {config.sender_name or 'Dank Shield'}"
    return content, embed


async def _post_temporary_test(
    interaction: discord.Interaction,
    config: StickyConfig,
    poll: Optional[StickyPoll],
) -> None:
    if not _manage_messages(interaction):
        return await _private(interaction, "❌ Temporary sticky tests require **Manage Messages**.")
    channel = _text_channel(interaction)
    if channel is None:
        return await _private(interaction, "❌ Run the test inside a normal text channel.")

    try:
        if config.mode == "plain":
            await channel.send(
                content=config.content,
                allowed_mentions=_ALLOWED_MENTIONS,
                delete_after=30,
            )
        elif config.mode == "embed":
            await channel.send(
                embed=sticky_embed(config),
                allowed_mentions=_ALLOWED_MENTIONS,
                delete_after=30,
            )
        elif config.mode == "poll" and poll is not None:
            await channel.send(
                content="🧪 **Sticky poll test** — voting is disabled in temporary tests.",
                embed=sticky_poll_embed(poll),
                allowed_mentions=_ALLOWED_MENTIONS,
                delete_after=30,
            )
        else:
            return await _private(interaction, "❌ This sticky is missing preview data.")
    except (discord.Forbidden, discord.HTTPException):
        return await _private(interaction, "❌ Dank Shield could not post the temporary test in this channel.")

    note = "✅ Temporary test posted for 30 seconds. It did **not** move or replace the real sticky."
    if config.use_webhook:
        note += " The test uses Dank Shield's identity; the live sticky will still use your configured custom sender."
    await _private(interaction, note)


def _merge_draft_with_live_state(draft: StickyConfig, current: Optional[StickyConfig]) -> StickyConfig:
    """Keep content edits while preserving the latest operational/delivery state."""
    if current is None:
        return replace(draft, last_message_id=None, last_sent_at=None)
    return replace(
        draft,
        enabled=current.enabled,
        color=current.color,
        interval_seconds=current.interval_seconds,
        message_threshold=current.message_threshold,
        use_webhook=current.use_webhook,
        sender_name=current.sender_name,
        sender_avatar_url=current.sender_avatar_url,
        last_message_id=current.last_message_id,
        last_sent_at=current.last_sent_at,
    )


class _OwnedPreviewView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own Sticky Messages panel to use these controls.")
        return False


class StickyPreviewTestView(_OwnedPreviewView):
    def __init__(self, owner_id: int, config: StickyConfig, poll: Optional[StickyPoll]) -> None:
        super().__init__(owner_id)
        self.config = config
        self.poll = poll

    @discord.ui.button(label="Post 30s Test", emoji="🧪", style=discord.ButtonStyle.primary)
    async def post_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _post_temporary_test(interaction, self.config, self.poll)


class StickyDraftPreviewView(_OwnedPreviewView):
    def __init__(self, owner_id: int, config: StickyConfig) -> None:
        super().__init__(owner_id)
        self.config = config

    @discord.ui.button(label="Publish Sticky", emoji="✅", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Publishing a sticky requires **Manage Messages**.")
        channel = _text_channel(interaction)
        guild = interaction.guild
        if channel is None or guild is None:
            return await _private(interaction, "❌ Publish the sticky from its destination text channel.")
        if int(channel.id) != int(self.config.channel_id) or int(guild.id) != int(self.config.guild_id):
            return await _private(interaction, "❌ This draft belongs to a different server channel. Reopen Sticky Messages there.")

        await interaction.response.defer()
        try:
            current = await get_sticky(int(channel.id))
            if current is not None and int(current.guild_id) != int(guild.id):
                raise InvalidCommunityToolValue("The saved sticky does not belong to this server.")
            publish_config = _merge_draft_with_live_state(self.config, current)
            saved = await save_sticky(publish_config)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await interaction.followup.send(
                f"❌ {exc}",
                ephemeral=True,
                allowed_mentions=_ALLOWED_MENTIONS,
            )

        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_config(saved)
        posted = await runtime.refresh_channel(channel, force=True) if saved.enabled else None

        # Local import avoids a module-import cycle while still returning the user
        # to the canonical Sticky Center after the draft is actually published.
        from .public_community_tools import StickyCenterView, _sticky_status_embed

        if not saved.enabled:
            message = "✅ Sticky changes saved. It is still **paused**, so nothing was posted live. Resume it from Sticky Settings when ready."
        elif posted is not None:
            message = "✅ Sticky published."
        else:
            message = "⚠️ Sticky was saved, but Dank Shield could not post it in this channel. Check channel permissions before retrying."
        await interaction.edit_original_response(
            content=message,
            embed=_sticky_status_embed(saved),
            view=StickyCenterView(int(interaction.user.id), config=saved, poll=None),
            allowed_mentions=_ALLOWED_MENTIONS,
        )
        self.stop()

    @discord.ui.button(label="Post 30s Test", emoji="🧪", style=discord.ButtonStyle.primary)
    async def post_test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _post_temporary_test(interaction, self.config, None)

    @discord.ui.button(label="Discard Draft", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            content="✅ Draft discarded. The current live sticky was not changed.",
            embed=None,
            view=None,
            allowed_mentions=_ALLOWED_MENTIONS,
        )
        self.stop()


async def show_sticky_preview(
    interaction: discord.Interaction,
    config: Optional[StickyConfig],
    poll: Optional[StickyPoll],
) -> None:
    if not _manage_messages(interaction):
        return await _private(interaction, "❌ Sticky preview requires **Manage Messages**.")
    if config is None:
        return await _private(interaction, "ℹ️ Create a sticky draft first, then preview it before publishing.")

    content, embed = _preview_payload(config, poll, draft=False)
    payload: dict[str, Any] = {
        "content": content,
        "ephemeral": True,
        "allowed_mentions": _ALLOWED_MENTIONS,
        "view": StickyPreviewTestView(int(interaction.user.id), config, poll),
    }
    if embed is not None:
        payload["embed"] = embed
    await interaction.response.send_message(**payload)


async def show_sticky_draft_preview(interaction: discord.Interaction, config: StickyConfig) -> None:
    if not _manage_messages(interaction):
        return await _private(interaction, "❌ Sticky preview requires **Manage Messages**.")
    try:
        safe = normalize_sticky(config)
    except InvalidCommunityToolValue as exc:
        return await _private(interaction, f"❌ {exc}")

    content, embed = _preview_payload(safe, None, draft=True)
    content += "\n\nUse **Publish Sticky** only when this looks right. You can also post a 30-second test first."
    payload: dict[str, Any] = {
        "content": content,
        "ephemeral": True,
        "allowed_mentions": _ALLOWED_MENTIONS,
        "view": StickyDraftPreviewView(int(interaction.user.id), safe),
    }
    if embed is not None:
        payload["embed"] = embed
    await interaction.response.send_message(**payload)


__all__ = [
    "StickyDraftPreviewView",
    "StickyPreviewTestView",
    "show_sticky_draft_preview",
    "show_sticky_preview",
]
