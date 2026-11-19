from __future__ import annotations

"""Menu-first community utilities for Dank Shield."""

import hashlib
import random
import re
from datetime import timedelta
from typing import Any, Optional, Sequence

import discord

from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.ui.picker import DankUserSelect

from stoney_verify.community_lookup_service import (
    CommunityLookupError,
    WEATHER_LABELS,
    random_wikihow,
    random_wikipedia,
    urban_dictionary_lookup,
    weather_lookup,
    wikipedia_lookup,
)
from stoney_verify.community_tools_runtime import ensure_community_tools_runtime
from stoney_verify.community_tools_service import InvalidCommunityToolValue
from .public_quiet_notice import open_quiet_notice_center

_ALLOWED_MENTIONS = discord.AllowedMentions.none()
_DICE_PATTERN = re.compile(r"^(?P<count>\d{1,2})?d(?P<sides>\d{1,4})(?P<modifier>[+-]\d{1,5})?$", re.IGNORECASE)


def _text_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


def _member_permissions(interaction: discord.Interaction) -> Optional[discord.Permissions]:
    channel = _text_channel(interaction)
    member = interaction.user
    if channel is None or not isinstance(member, discord.Member):
        return None
    return channel.permissions_for(member)


def _can_send_messages(interaction: discord.Interaction) -> bool:
    perms = _member_permissions(interaction)
    return bool(perms and perms.view_channel and perms.send_messages)


def _poll_permission(perms: Any) -> bool:
    values = [bool(getattr(perms, name)) for name in ("send_polls", "create_polls") if hasattr(perms, name)]
    return all(values) if values else True


def _can_create_poll(interaction: discord.Interaction) -> bool:
    perms = _member_permissions(interaction)
    return bool(perms and perms.view_channel and perms.send_messages and _poll_permission(perms))


def _bot_permissions(channel: discord.TextChannel) -> Optional[discord.Permissions]:
    me = channel.guild.me
    return channel.permissions_for(me) if me is not None else None


def _bot_can_post(channel: discord.TextChannel, *, embed: bool = False, poll: bool = False) -> bool:
    perms = _bot_permissions(channel)
    if perms is None or not (perms.view_channel and perms.send_messages):
        return False
    if embed and not perms.embed_links:
        return False
    if poll and not _poll_permission(perms):
        return False
    return True


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {"ephemeral": True, "allowed_mentions": _ALLOWED_MENTIONS}
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _replace(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    await interaction.response.edit_message(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=_ALLOWED_MENTIONS,
    )


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = PRIVATE_MENU_TTL_SECONDS) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own `/dank home` panel to use these controls.")
        return False


def _center_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧰 Community Tools",
        description=(
            "Choose what you want to do below. Use **Embed Studio** to make "
            "announcements, rules, and event posts. Nothing is posted without "
            "your review."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="✏️ Make a post",
        value=(
            "**Create Poll:** Let members vote.\n"
            "**Quiet Server Notice:** Post a notice when the server goes quiet.\n"
            "**Embed Studio:** Build a custom announcement with images and buttons."
        ),
        inline=False,
    )
    embed.add_field(
        name="🔗 Share & manage",
        value=(
            "**Share Router:** Move or copy matching videos, photos, and links.\n"
            "**Member / Server Info:** View useful account or server facts.\n"
            "**Permission Check:** See what Dank Shield can access."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎲 Explore",
        value="Weather, useful lookups, dice, coin flips, and community games.",
        inline=False,
    )
    embed.set_footer(text="Community Tools • previews stay private until you publish or post a test • Blue: open a task • Grey: browse • Red: remove")
    return embed


class CommunityToolsView(_OwnedView):
    @discord.ui.button(label="Quiet Server Notice", emoji="🌙", style=discord.ButtonStyle.primary, row=0)
    async def quiet_notice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_quiet_notice_center(interaction)

    @discord.ui.button(label="Create Poll", emoji="📊", style=discord.ButtonStyle.primary, row=0)
    async def poll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if _text_channel(interaction) is None:
            return await _private(interaction, "❌ Create polls inside a normal text channel.")
        if not _can_create_poll(interaction):
            return await _private(interaction, "❌ You need this channel's **Send Messages + poll permission** to create a poll.")
        await interaction.response.send_modal(NativePollModal())

    @discord.ui.button(label="Embed Studio", emoji="🧱", style=discord.ButtonStyle.primary, row=0)
    async def embed_builder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from ..embed_studio_ui import open_embed_studio

        await open_embed_studio(interaction)

    @discord.ui.button(label="Member / Server Info", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            embed=discord.Embed(
                title="👤 Member / Server Info",
                description="Pick a member for useful account/server facts, or open the server summary.",
                color=discord.Color.blurple(),
            ),
            view=InfoView(self.owner_id),
        )

    @discord.ui.button(label="Permission Check", emoji="🔐", style=discord.ButtonStyle.secondary, row=1)
    async def permissions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await show_permission_check(interaction)

    @discord.ui.button(label="Share Router", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def share_router(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_share_router import open_share_router

        await open_share_router(interaction, replace_message=True)

    @discord.ui.button(label="Fun & Lookup", emoji="🎲", style=discord.ButtonStyle.secondary, row=1)
    async def fun(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_fun_embed(), view=FunLookupView(self.owner_id))

    @discord.ui.button(label="Dank Shield Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_surface_v2 import _open_home

        await _open_home(interaction)

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Community Tools closed.", embed=None, view=None)


def _normalize_native_poll_choices(raw: str) -> list[str]:
    options: list[str] = []
    seen: set[str] = set()
    for line in str(raw or "").splitlines():
        value = " ".join(line.split()).strip()[:55]
        key = value.casefold()
        if value and key not in seen:
            options.append(value)
            seen.add(key)
    if not 2 <= len(options) <= 7:
        raise InvalidCommunityToolValue("Polls need 2 to 7 unique visible choices.")
    return options


class NativePollModal(discord.ui.Modal, title="Create Discord poll"):
    question = discord.ui.TextInput(label="Question", required=True, max_length=300)
    choices = discord.ui.TextInput(label="Choices — one per line (2-7)", style=discord.TextStyle.paragraph, required=True, max_length=400)
    hours = discord.ui.TextInput(label="Duration in hours (1-168)", required=True, max_length=3, default="24")
    multiple = discord.ui.TextInput(label="Allow multiple choices? yes/no", required=True, max_length=3, default="no")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = _text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Create polls inside a normal text channel.")
        if not _can_create_poll(interaction):
            return await _private(interaction, "❌ You no longer have this channel's Send Messages + poll permission.")
        try:
            options = _normalize_native_poll_choices(str(self.choices.value))
            duration_hours = int(str(self.hours.value).strip())
        except ValueError:
            return await _private(interaction, "❌ Duration must be a whole number from 1 to 168.")
        except InvalidCommunityToolValue as exc:
            return await _private(interaction, f"❌ {exc}")
        if not 1 <= duration_hours <= 168:
            return await _private(interaction, "❌ Duration must be 1-168 hours; the value was not silently changed.")
        multiple = str(self.multiple.value).strip().lower()
        if multiple not in {"y", "yes", "n", "no"}:
            return await _private(interaction, "❌ For multiple choices, enter `yes` or `no`.")
        allow_multiple = multiple in {"y", "yes"}
        preview = discord.Embed(title="📊 Poll Preview", description=str(self.question.value), color=discord.Color.blurple())
        preview.add_field(name="Choices", value="\n".join(f"**{i + 1}.** {value}" for i, value in enumerate(options)), inline=False)
        preview.add_field(name="Duration", value=f"{duration_hours} hour{'s' if duration_hours != 1 else ''}", inline=True)
        preview.add_field(name="Multiple choices", value="Yes" if allow_multiple else "No", inline=True)
        await _private(
            interaction,
            "👁️ Private preview. Discord does not receive the poll until you press **Publish Poll**.",
            embed=preview,
            view=NativePollPreviewView(
                int(interaction.user.id), int(channel.id), str(self.question.value), options, duration_hours, allow_multiple
            ),
        )


class NativePollPreviewView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        channel_id: int,
        question: str,
        options: Sequence[str],
        duration_hours: int,
        allow_multiple: bool,
    ) -> None:
        super().__init__(owner_id, timeout=300)
        self.channel_id = int(channel_id)
        self.question = question
        self.options = tuple(options)
        self.duration_hours = int(duration_hours)
        self.allow_multiple = bool(allow_multiple)

    @discord.ui.button(label="Publish Poll", emoji="✅", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = interaction.client.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ Poll destination no longer exists or is unavailable.")
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await _private(interaction, "❌ Member permissions could not be resolved.")
        member_perms = channel.permissions_for(member)
        if not (member_perms.view_channel and member_perms.send_messages and _poll_permission(member_perms)):
            return await _private(interaction, "❌ You no longer have permission to post polls in the destination channel.")
        if not _bot_can_post(channel, poll=True):
            return await _private(interaction, "❌ Dank Shield lacks Send Messages or poll permission in the destination channel.")
        try:
            poll = discord.Poll(self.question, timedelta(hours=self.duration_hours), multiple=self.allow_multiple)
            for option in self.options:
                poll.add_answer(text=option)
            message = await channel.send(poll=poll, allowed_mentions=_ALLOWED_MENTIONS)
        except (discord.HTTPException, discord.Forbidden, discord.ClientException, ValueError):
            return await _private(interaction, "❌ Discord rejected that poll. Nothing else was changed.")
        await _replace(
            interaction,
            content=f"✅ Poll posted in <#{channel.id}>: {message.jump_url}",
            embed=None,
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Discard", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Poll draft discarded.", embed=None, view=None)
        self.stop()


# Custom embeds use the single canonical Embed Studio; the old one-modal
# Community Tools editor was removed rather than kept as a parallel writer.


async def _ignore_user_pick(interaction: discord.Interaction, user: object) -> None:
    """No-op on_pick for DankUserSelect subclasses that override callback."""
    return None


class InfoMemberSelect(DankUserSelect):
    """House user picker for member info.

    Uses the framework's DankUserSelect wrapper. The original select
    had no owner binding of its own — the host InfoView's owner check
    gates the pick — so the wrapper is opened with ``author_id=0``
    (no extra owner lock), and the callback keeps the exact original
    flow (host check, then the member-info embed), as with
    SubjectUserSelect in Network Safety. The wrapper's own callback
    only forwards the picked user, so it is overridden here.
    """

    def __init__(self) -> None:
        super().__init__(
            author_id=0,
            on_pick=_ignore_user_pick,
            placeholder="Choose a member",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.custom_id = "dank:community:info:user:v2"

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, InfoView) or not await view.interaction_check(interaction):
            return
        target = self.values[0]
        guild = interaction.guild
        member = guild.get_member(int(target.id)) if guild is not None else None
        user = member or target
        embed = discord.Embed(
            title=f"👤 {getattr(user, 'display_name', str(user))}",
            color=getattr(member, "color", discord.Color.blurple()) if member else discord.Color.blurple(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(user.created_at, style="R"), inline=True)
        embed.add_field(name="Account type", value="Bot" if bool(getattr(user, "bot", False)) else "Member", inline=True)
        if member is not None:
            embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown", inline=True)
            embed.add_field(name="Top role", value=member.top_role.mention if member.top_role else "None", inline=True)
            visible_roles = [role.mention for role in member.roles[1:] if not role.managed]
            shown = visible_roles[-10:]
            role_text = " ".join(shown) if shown else "No extra roles"
            if len(visible_roles) > len(shown):
                role_text += f"\n+{len(visible_roles) - len(shown)} more"
            embed.add_field(name=f"Roles ({len(visible_roles)})", value=role_text, inline=False)
        embed.set_footer(text="Only standard Discord server and account information is shown")
        await _replace(interaction, embed=embed, view=InfoView(view.owner_id))


class InfoView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(InfoMemberSelect())

    @discord.ui.button(label="Server Info", emoji="🏠", style=discord.ButtonStyle.primary, row=1)
    async def server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, "❌ Use this in a server.")
        humans = sum(1 for member in guild.members if not member.bot)
        bots = max(0, len(guild.members) - humans)
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        embed = discord.Embed(title=f"🏠 {guild.name}", color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Members", value=f"{guild.member_count or len(guild.members)} ({humans} human • {bots} bot cached)", inline=True)
        embed.add_field(name="Channels", value=f"{text_channels} text • {voice_channels} voice • {len(guild.channels)} total", inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="Boosts", value=f"Tier {guild.premium_tier} • {guild.premium_subscription_count or 0} boosts", inline=True)
        embed.add_field(name="Verification", value=str(guild.verification_level).replace("_", " ").title(), inline=True)
        await _replace(interaction, embed=embed, view=InfoView(self.owner_id))

    @discord.ui.button(label="Community Tools", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))


def _permission_line(label: str, allowed: bool) -> str:
    return f"{'✅' if allowed else '❌'} {label}"


async def show_permission_check(interaction: discord.Interaction) -> None:
    channel = _text_channel(interaction)
    guild = interaction.guild
    member = interaction.user
    if channel is None or guild is None or guild.me is None or not isinstance(member, discord.Member):
        return await _private(interaction, "❌ Permission Check needs a normal server text channel.")
    bot = channel.permissions_for(guild.me)
    user = channel.permissions_for(member)

    bot_checks = [
        ("View Channel", bot.view_channel),
        ("Send Messages", bot.send_messages),
        ("Embed Links", bot.embed_links),
        ("Read Message History", bot.read_message_history),
        ("Manage Messages", bot.manage_messages),
        ("Send/Create Polls", _poll_permission(bot)),
    ]
    user_checks = [
        ("View Channel", user.view_channel),
        ("Send Messages", user.send_messages),
        ("Manage Messages", user.manage_messages or member.guild_permissions.administrator),
        ("Send/Create Polls", _poll_permission(user)),
    ]
    post_ok = bool(bot.view_channel and bot.send_messages and bot.read_message_history)
    embed_ok = bool(post_ok and bot.embed_links)
    native_poll_ok = bool(bot.view_channel and bot.send_messages and _poll_permission(bot))

    embed = discord.Embed(
        title=f"🔐 Permission Check • #{channel.name}",
        description="Effective channel permissions after role/category/channel overwrites are applied.",
        color=discord.Color.green() if post_ok and embed_ok else discord.Color.orange(),
    )
    embed.add_field(name="Dank Shield", value="\n".join(_permission_line(*item) for item in bot_checks), inline=False)
    embed.add_field(name="You", value="\n".join(_permission_line(*item) for item in user_checks), inline=False)
    embed.add_field(
        name="Feature readiness",
        value=(
            f"{'✅' if post_ok else '❌'} Post messages\n"
            f"{'✅' if embed_ok else '❌'} Embed posts and quiet notices\n"
            f"{'✅' if native_poll_ok else '❌'} Native Discord poll"
        ),
        inline=False,
    )
    embed.set_footer(text="Quiet Server Notice posts with the same message permissions")
    await _private(interaction, embed=embed)


def _fun_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎲 Fun & Lookup",
        description="Network lookups are bounded and fail cleanly; quick games are local and instant.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Lookups",
        value="Weather • Wikipedia • random Wikipedia • random WikiHow • Urban Dictionary (age-restricted channels only)",
        inline=False,
    )
    embed.add_field(name="Quick games", value="Custom dice notation • coin flip • deterministic-for-the-same-pair compatibility joke", inline=False)
    embed.set_footer(text="Only currently available tools are shown")
    return embed


class FunLookupView(_OwnedView):
    @discord.ui.button(label="Weather", emoji="🌦️", style=discord.ButtonStyle.primary, row=0)
    async def weather(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(WeatherModal())

    @discord.ui.button(label="Wikipedia", emoji="📚", style=discord.ButtonStyle.primary, row=0)
    async def wiki(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(WikipediaModal())

    @discord.ui.button(label="Random Wikipedia", emoji="🎲", style=discord.ButtonStyle.secondary, row=0)
    async def random_wiki(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            result = await random_wikipedia()
        except CommunityLookupError as exc:
            return await _private(interaction, f"❌ {exc}")
        await _private(interaction, embed=discord.Embed(title=result.title, description=result.summary, url=result.url, color=discord.Color.blurple()))

    @discord.ui.button(label="Random WikiHow", emoji="🛠️", style=discord.ButtonStyle.secondary, row=0)
    async def wikihow(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            result = await random_wikihow()
        except CommunityLookupError as exc:
            return await _private(interaction, f"❌ {exc}")
        await _private(interaction, embed=discord.Embed(title=result.title, description=result.summary, url=result.url, color=discord.Color.blurple()))

    @discord.ui.button(label="Urban Dictionary", emoji="🔞", style=discord.ButtonStyle.secondary, row=1)
    async def urban(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = _text_channel(interaction)
        if channel is None or not channel.is_nsfw():
            return await _private(interaction, "❌ Urban Dictionary is restricted to Discord channels marked **Age-Restricted / NSFW**.")
        await interaction.response.send_modal(UrbanDictionaryModal())

    @discord.ui.button(label="Roll Dice", emoji="🎲", style=discord.ButtonStyle.success, row=1)
    async def dice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(DiceModal())

    @discord.ui.button(label="Coin Flip", emoji="🪙", style=discord.ButtonStyle.success, row=1)
    async def coin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _private(interaction, f"🪙 **{random.choice(('Heads', 'Tails'))}!**")

    @discord.ui.button(label="Compatibility", emoji="💘", style=discord.ButtonStyle.success, row=1)
    async def love(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(CompatibilityModal())

    @discord.ui.button(label="Community Tools", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))


class WeatherModal(discord.ui.Modal, title="Weather lookup"):
    location = discord.ui.TextInput(label="City, region, or postal code", required=True, max_length=120)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await weather_lookup(str(self.location.value))
        except CommunityLookupError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        condition = WEATHER_LABELS.get(result.weather_code, "Current conditions")
        embed = discord.Embed(title=f"🌦️ {result.location}", description=condition, color=discord.Color.blurple())
        embed.add_field(name="Temperature", value=f"{result.temperature_f:.0f}°F / {result.temperature_c:.0f}°C", inline=True)
        embed.add_field(name="Feels like", value=f"{result.apparent_f:.0f}°F / {result.apparent_c:.0f}°C", inline=True)
        embed.add_field(name="Humidity", value=f"{result.humidity}%", inline=True)
        embed.add_field(name="Wind", value=f"{result.wind_mph:.0f} mph / {result.wind_kmh:.0f} km/h", inline=True)
        if result.high_c is not None and result.low_c is not None:
            embed.add_field(name="Today", value=f"High {result.high_f:.0f}°F • Low {result.low_f:.0f}°F", inline=True)
        if result.precipitation_probability is not None:
            embed.add_field(name="Rain/snow chance", value=f"{result.precipitation_probability}%", inline=True)
        embed.set_footer(text="Weather data: Open-Meteo")
        await interaction.followup.send(embed=embed, ephemeral=True)


class WikipediaModal(discord.ui.Modal, title="Wikipedia lookup"):
    topic = discord.ui.TextInput(label="Topic", required=True, max_length=200)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await wikipedia_lookup(str(self.topic.value))
        except CommunityLookupError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        embed = discord.Embed(title=result.title, description=result.summary, url=result.url, color=discord.Color.blurple())
        embed.set_footer(text="Source: Wikipedia")
        await interaction.followup.send(embed=embed, ephemeral=True)


class UrbanDictionaryModal(discord.ui.Modal, title="Urban Dictionary lookup"):
    term = discord.ui.TextInput(label="Word or phrase", required=True, max_length=100)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = _text_channel(interaction)
        if channel is None or not channel.is_nsfw():
            return await _private(interaction, "❌ Urban Dictionary is restricted to age-restricted / NSFW channels.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await urban_dictionary_lookup(str(self.term.value))
        except CommunityLookupError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        embed = discord.Embed(
            title=f"🔞 {result.word}",
            description=result.definition or "No definition text.",
            url=result.permalink or None,
            color=discord.Color.blurple(),
        )
        if result.example:
            embed.add_field(name="Example", value=result.example, inline=False)
        embed.set_footer(text=f"Urban Dictionary • 👍 {result.thumbs_up} • 👎 {result.thumbs_down}")
        await interaction.followup.send(embed=embed, ephemeral=True)


def _parse_dice_notation(value: str) -> tuple[int, int, int, str]:
    raw = str(value or "").strip().replace(" ", "").lower()
    match = _DICE_PATTERN.fullmatch(raw)
    if match is None:
        raise InvalidCommunityToolValue("Use dice notation like `d20`, `2d6`, or `4d8+2`.")
    count_text = match.group("count")
    count = int(count_text) if count_text else 1
    sides = int(match.group("sides"))
    modifier = int(match.group("modifier") or 0)
    if not 1 <= count <= 50:
        raise InvalidCommunityToolValue("Roll between 1 and 50 dice at a time.")
    if not 2 <= sides <= 1000:
        raise InvalidCommunityToolValue("Dice can have between 2 and 1000 sides.")
    if not -10000 <= modifier <= 10000:
        raise InvalidCommunityToolValue("Keep the modifier between -10000 and +10000.")
    normalized = f"{count}d{sides}{modifier:+d}" if modifier else f"{count}d{sides}"
    return count, sides, modifier, normalized


def _dice_embed(notation: str) -> discord.Embed:
    count, sides, modifier, normalized = _parse_dice_notation(notation)
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier
    shown = ", ".join(str(value) for value in rolls[:20])
    if len(rolls) > 20:
        shown += f", … +{len(rolls) - 20} more"
    modifier_text = f" {modifier:+d}" if modifier else ""
    embed = discord.Embed(title=f"🎲 {normalized}", description=f"**Total: {total}**", color=discord.Color.blurple())
    embed.add_field(name="Rolls", value=shown, inline=False)
    if modifier:
        embed.add_field(name="Modifier", value=modifier_text.strip(), inline=True)
    return embed


class DiceModal(discord.ui.Modal, title="Roll dice"):
    notation = discord.ui.TextInput(label="Dice notation", required=True, max_length=20, default="2d6", placeholder="d20, 2d6, 4d8+2")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            _, _, _, normalized = _parse_dice_notation(str(self.notation.value))
            embed = _dice_embed(normalized)
        except InvalidCommunityToolValue as exc:
            return await _private(interaction, f"❌ {exc}")
        await _private(interaction, embed=embed, view=DiceView(int(interaction.user.id), normalized))


class DiceView(_OwnedView):
    def __init__(self, owner_id: int, notation: str = "2d6") -> None:
        super().__init__(owner_id, timeout=300)
        self.notation = notation

    @discord.ui.button(label="Roll Again", emoji="🎲", style=discord.ButtonStyle.success)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_dice_embed(self.notation), view=DiceView(self.owner_id, self.notation))

    @discord.ui.button(label="Change Dice", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def change(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(DiceModal())


class CompatibilityModal(discord.ui.Modal, title="Compatibility"):
    first = discord.ui.TextInput(label="First name", required=True, max_length=80)
    second = discord.ui.TextInput(label="Second name", required=True, max_length=80)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        a = " ".join(str(self.first.value).split()).casefold()
        b = " ".join(str(self.second.value).split()).casefold()
        if not a or not b:
            return await _private(interaction, "❌ Enter two names.")
        pair = "\0".join(sorted((a, b))).encode("utf-8")
        score = int.from_bytes(hashlib.sha256(pair).digest()[:4], "big") % 101
        await _private(
            interaction,
            f"💘 **{self.first.value} + {self.second.value}: {score}% compatible**\n*For entertainment only; the same pair gets the same silly score.*",
        )


async def open_community_tools(interaction: discord.Interaction, *, replace_message: bool = False) -> None:
    if interaction.guild is None:
        return await _private(interaction, "❌ Community Tools are available inside servers.")
    ensure_community_tools_runtime(interaction.client)
    if replace_message and not interaction.response.is_done():
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(int(interaction.user.id)))
    else:
        await _private(interaction, embed=_center_embed(), view=CommunityToolsView(int(interaction.user.id)))


__all__ = [
    "CommunityToolsView",
    "DiceView",
    "FunLookupView",
    "InfoView",
    "NativePollPreviewView",
    "ensure_community_tools_runtime",
    "open_community_tools",
    "show_permission_check",
]
