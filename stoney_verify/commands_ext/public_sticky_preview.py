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
    get_sticky_poll,
    normalize_sticky,
    save_sticky_bundle,
)

_ALLOWED_MENTIONS = discord.AllowedMentions.none()


def _text_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


def _manage_messages(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> bool:
    member = interaction.user
    target = channel or _text_channel(interaction)
    if not isinstance(member, discord.Member) or target is None:
        return False
    perms = target.permissions_for(member)
    return bool(member.guild_permissions.administrator or perms.manage_messages)


def _sticky_design_signature(config: Optional[StickyConfig]) -> Optional[tuple[Any, ...]]:
    if config is None:
        return None
    return (
        str(config.mode),
        str(config.content),
        str(config.title),
        int(config.color),
        str(config.image_url),
        str(config.thumbnail_url),
    )


def _poll_design_signature(poll: Optional[StickyPoll]) -> Optional[tuple[Any, ...]]:
    if poll is None:
        return None
    return (str(poll.question), tuple(str(item) for item in poll.options))


def _draft_is_stale(
    baseline: Optional[StickyConfig],
    current: Optional[StickyConfig],
    baseline_poll: Optional[StickyPoll] = None,
    current_poll: Optional[StickyPoll] = None,
) -> bool:
    if _sticky_design_signature(baseline) != _sticky_design_signature(current):
        return True
    return _poll_design_signature(baseline_poll) != _poll_design_signature(current_poll)


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
    prefix = "👁️ **Draft preview** — nothing has changed live yet." if draft else "👁️ **Private sticky preview** — only you can see this."
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


def _destination(interaction: discord.Interaction, channel_id: int) -> Optional[discord.TextChannel]:
    channel = interaction.client.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None


async def _post_temporary_test(
    interaction: discord.Interaction,
    config: StickyConfig,
    poll: Optional[StickyPoll],
) -> None:
    channel = _destination(interaction, int(config.channel_id))
    if channel is None:
        return await _private(interaction, "❌ The sticky destination channel is unavailable.")
    if not _manage_messages(interaction, channel):
        return await _private(interaction, "❌ Temporary sticky tests require **Manage Messages in the destination channel**.")
    me = channel.guild.me
    perms = channel.permissions_for(me) if me is not None else None
    if perms is None or not (perms.view_channel and perms.send_messages):
        return await _private(interaction, "❌ Dank Shield cannot send messages in the sticky destination.")
    if config.mode in {"embed", "poll"} and not perms.embed_links:
        return await _private(interaction, "❌ Dank Shield needs **Embed Links** in the sticky destination.")

    try:
        if config.mode == "plain":
            await channel.send(content=config.content, allowed_mentions=_ALLOWED_MENTIONS, delete_after=30)
        elif config.mode == "embed":
            await channel.send(embed=sticky_embed(config), allowed_mentions=_ALLOWED_MENTIONS, delete_after=30)
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
        return await _private(interaction, "❌ Dank Shield could not post the temporary test in the destination channel.")

    note = f"✅ Temporary test posted in <#{channel.id}> for 30 seconds. It did **not** move or replace the real sticky."
    if config.use_webhook:
        note += " Tests use Dank Shield's identity; the published sticky still uses your configured managed sender."
    await _private(interaction, note)


def _merge_draft_with_live_state(draft: StickyConfig, current: Optional[StickyConfig]) -> StickyConfig:
    """Keep draft content/style while preserving latest operational/delivery state."""
    if current is None:
        return replace(draft, last_message_id=None, last_sent_at=None)
    return replace(
        draft,
        enabled=current.enabled,
        interval_seconds=current.interval_seconds,
        message_threshold=current.message_threshold,
        use_webhook=current.use_webhook if current.mode != "poll" else False,
        sender_name=current.sender_name if current.mode != "poll" else "",
        sender_avatar_url=current.sender_avatar_url if current.mode != "poll" else "",
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
    def __init__(
        self,
        owner_id: int,
        config: StickyConfig,
        *,
        baseline: Optional[StickyConfig] = None,
        baseline_poll: Optional[StickyPoll] = None,
    ) -> None:
        super().__init__(owner_id)
        self.config = config
        self.baseline = baseline
        self.baseline_poll = baseline_poll

    @discord.ui.button(label="Publish Sticky", emoji="✅", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = _destination(interaction, int(self.config.channel_id))
        guild = interaction.guild
        if channel is None or guild is None:
            return await _private(interaction, "❌ The sticky destination is unavailable.")
        if not _manage_messages(interaction, channel):
            return await _private(interaction, "❌ Publishing requires **Manage Messages in the destination channel**.")
        if int(guild.id) != int(self.config.guild_id):
            return await _private(interaction, "❌ This draft belongs to a different server.")

        await interaction.response.defer()
        try:
            current = await get_sticky(int(channel.id))
            current_poll = await get_sticky_poll(int(channel.id)) if current is not None and current.mode == "poll" else None
            if current is not None and int(current.guild_id) != int(guild.id):
                raise InvalidCommunityToolValue("The saved sticky does not belong to this server.")
            if _draft_is_stale(self.baseline, current, self.baseline_poll, current_poll):
                raise InvalidCommunityToolValue(
                    "This draft is stale because another Community Tools change modified the live sticky while you were editing. Reopen the editor so newer work is not overwritten."
                )
            publish_config = _merge_draft_with_live_state(self.config, current)
            saved, saved_poll = await save_sticky_bundle(publish_config, None)
            if saved_poll is not None:
                raise CommunityStorageUnavailable("Unexpected sticky-poll state remained after a normal sticky save.")
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True, allowed_mentions=_ALLOWED_MENTIONS)

        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_config(saved)
        posted = await runtime.refresh_channel(channel, force=True) if saved.enabled else None

        from .public_community_tools import StickyCenterView, _sticky_status_embed

        if not saved.enabled:
            message = "✅ Sticky changes saved atomically. It is still **paused**, so nothing was posted live."
        elif posted is not None:
            message = "✅ Sticky published."
        else:
            message = (
                "⚠️ Sticky state was saved safely, but Dank Shield could not post the replacement. "
                "The previous live sticky was left intact; run Permission Check before retrying."
            )
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
    if config is None:
        return await _private(interaction, "ℹ️ Create a sticky draft first, then preview it before publishing.")
    channel = _destination(interaction, int(config.channel_id))
    if channel is None or not _manage_messages(interaction, channel):
        return await _private(interaction, "❌ Sticky preview requires **Manage Messages in the destination channel**.")

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


async def show_sticky_draft_preview(
    interaction: discord.Interaction,
    config: StickyConfig,
    *,
    baseline: Optional[StickyConfig] = None,
    baseline_poll: Optional[StickyPoll] = None,
) -> None:
    channel = _destination(interaction, int(config.channel_id))
    if channel is None or not _manage_messages(interaction, channel):
        return await _private(interaction, "❌ Sticky preview requires **Manage Messages in the destination channel**.")
    try:
        safe = normalize_sticky(config)
    except InvalidCommunityToolValue as exc:
        return await _private(interaction, f"❌ {exc}")

    content, embed = _preview_payload(safe, None, draft=True)
    content += (
        "\n\nUse **Publish Sticky** only when this looks right. You can also post a 30-second test first. "
        "If another manager changes the live sticky before you publish, this draft will refuse to overwrite their newer work."
    )
    payload: dict[str, Any] = {
        "content": content,
        "ephemeral": True,
        "allowed_mentions": _ALLOWED_MENTIONS,
        "view": StickyDraftPreviewView(
            int(interaction.user.id),
            safe,
            baseline=baseline,
            baseline_poll=baseline_poll,
        ),
    }
    if embed is not None:
        payload["embed"] = embed
    await interaction.response.send_message(**payload)


__all__ = [
    "StickyDraftPreviewView",
    "StickyPreviewTestView",
    "_draft_is_stale",
    "_merge_draft_with_live_state",
    "show_sticky_draft_preview",
    "show_sticky_preview",
]
