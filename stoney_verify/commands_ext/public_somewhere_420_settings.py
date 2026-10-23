from __future__ import annotations

"""Interactive per-server and per-person 4:20 options using existing Search-Safe picker."""

from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from stoney_verify.somewhere_420_schedules import (
    Somewhere420StorageUnavailable, get_schedule, next_daily_fire, save_schedule, valid_timezone,
)
from stoney_verify.ui.resource_browser import DankGuildResourceBrowserView
from stoney_verify.somewhere_420_role_headsup import (
    configure_role_heads_up, get_role_optout, set_role_optout,
)
from .public_owner_authority import interaction_has_manage_guild_authority

def _truth(value: str) -> bool:
    raw = str(value or "").strip().lower()
    if raw not in ("on", "off"):
        raise ValueError("Enter exactly on or off.")
    return raw == "on"


async def _reply(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    else:
        await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def _panel(interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View) -> None:
    args = {"embed": embed, "view": view, "content": None, "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.edit_original_response(**args)
    elif interaction.message is not None:
        await interaction.response.edit_message(**args)
    else:
        await interaction.response.send_message(**args, ephemeral=True)


def _time_summary(data: dict | None) -> str:
    if not data:
        return "Not configured"
    try:
        at = next_daily_fire(str(data.get("timezone") or "UTC"))
        return f"<t:{int(at.timestamp())}:F> (<t:{int(at.timestamp())}:R>)"
    except ValueError:
        return "Timezone needs repair"


def _settings_embed(kind: str, data: dict | None) -> discord.Embed:
    saved = data or {}
    title = "⚙️ 4:20 Server Celebrations" if kind == "guild" else "🕓 My Personal 4:20"
    embed = discord.Embed(title=title, color=discord.Color.green())
    enabled = bool(saved.get("enabled", False))
    embed.description = (
        "✅ Enabled" if enabled else "▫️ Off by default"
    ) + "\nSchedule: **4:20 PM** in the chosen timezone, once per local day."
    embed.add_field(name="Timezone", value=str(saved.get("timezone") or "Not chosen"), inline=True)
    embed.add_field(name="Next 4:20", value=_time_summary(data), inline=False)
    if kind == "guild":
        rid = int(saved.get("role_reminder_role_id") or 0)
        embed.add_field(
            name="🌿 Stoners heads-up (5 min early)",
            value=("✅ On" if saved.get("role_reminder_enabled") else "▫️ Off")
            + (f" • <@&{rid}>" if rid else " • Choose a role first")
            + "\\nAll role holders are included automatically; individual Stop reminders is available.",
            inline=False,
        )
        cid = int(saved.get("channel_id") or 0)
        embed.add_field(name="Channel", value=f"<#{cid}>" if cid else "Choose a channel", inline=True)
        embed.add_field(
            name="Notification",
            value="Optional Community & Pings /toke notification role" if saved.get("notify_role") else "No ping",
            inline=False,
        )
        embed.add_field(
            name="Quiet hours",
            value=f"{int(saved.get('quiet_start', 22)):02}:00 to {int(saved.get('quiet_end', 8)):02}:00 (24h local). Same start/end disables quiet hours.",
        )
        embed.set_footer(text="A daily event never automatically starts /toke. Only staff enable posting.")
    else:
        embed.add_field(name="Favorite city", value=str(saved.get("favorite_city") or "None"), inline=True)
        embed.set_footer(text="Personal DM reminders are private and opt-in across shared servers.")
    return embed


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _reply(interaction, "Open your own 4:20 preferences panel.")
            return False
        return True


async def open_personal_420(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        if interaction.message is not None:
            await interaction.response.defer()
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        saved = await get_schedule("member", interaction.user.id)
    except Somewhere420StorageUnavailable:
        return await _reply(interaction, "Personal settings need the private 4:20 schedule migration before they can save.")
    embed = _settings_embed("member", saved)
    gid = int(interaction.guild.id) if interaction.guild is not None else 0
    if gid:
        try:
            muted = await get_role_optout(gid, int(interaction.user.id))
            embed.add_field(
                name="🌿 @Stoners heads-up (this server)",
                value=("🔕 Muted" if muted else "✅ Automatic while you have the selected role")
                + "\\nAt most one private DM per day, five minutes early; opt out anytime.",
                inline=False,
            )
        except Somewhere420StorageUnavailable:
            embed.add_field(name="Stoners heads-up", value="Settings database unavailable.", inline=False)
    await _panel(interaction, embed, Personal420View(interaction.user.id, saved, guild_id=gid))


class PersonalSettingsModal(discord.ui.Modal, title="Personal 4:20 Preferences"):
    zone = discord.ui.TextInput(label="IANA timezone", placeholder="America/New_York", max_length=90)
    city = discord.ui.TextInput(label="Favorite world-clock city (optional)", placeholder="Tokyo", required=False, max_length=65)
    dms = discord.ui.TextInput(label="Daily private 4:20 DM: on or off", placeholder="off", max_length=3)

    def __init__(self, owner_id: int, previous: dict | None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        cfg = previous or {}
        self.zone.default = str(cfg.get("timezone") or "UTC")
        self.city.default = str(cfg.get("favorite_city") or "")
        self.dms.default = "on" if cfg.get("enabled") else "off"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            return await _reply(interaction, "These are not your personal settings.")
        try:
            from stoney_verify.somewhere_420_clock import PLACES
            favorite = str(self.city.value or "").strip()
            matching = {name.casefold(): name for name, _ in PLACES}
            if favorite:
                favorite = matching.get(favorite.casefold(), "")
                if not favorite:
                    raise ValueError("Choose a city from the existing 4:20 world board.")
            zone = valid_timezone(str(self.zone.value))
            enabled = _truth(str(self.dms.value))
        except ValueError as exc:
            return await _reply(interaction, str(exc))
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            saved = await save_schedule(
                "member", interaction.user.id, zone_name=zone, enabled=enabled, favorite_city=favorite,
            )
        except (Somewhere420StorageUnavailable, ValueError):
            return await _reply(interaction, "Couldn't save private reminders. Check the 4:20 database migration.")
        await open_personal_420(interaction)



class Personal420View(_OwnedView):
    def __init__(self, owner_id: int, saved: dict | None, *, guild_id: int = 0):
        super().__init__(owner_id)
        self.saved = saved
        self.guild_id = int(guild_id)

    @discord.ui.button(label="Edit Timezone, City & DMs", emoji="🕓", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PersonalSettingsModal(self.owner_id, self.saved))

    @discord.ui.button(label="Stop My Daily DMs", emoji="🔕", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        cfg = self.saved or {}
        try:
            await save_schedule("member", self.owner_id, zone_name=str(cfg.get("timezone") or "UTC"),
                                enabled=False, favorite_city=str(cfg.get("favorite_city") or ""))
        except (Somewhere420StorageUnavailable, ValueError):
            return await _reply(interaction, "Couldn't save your daily DM opt-out. Try again when the database returns.")
        await open_personal_420(interaction)

    @discord.ui.button(label="Stop Stoners Heads-Up", emoji="🔕", style=discord.ButtonStyle.danger, row=1)
    async def mute_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_role_muted(interaction, True)

    @discord.ui.button(label="Resume Stoners Heads-Up", emoji="🔔", style=discord.ButtonStyle.secondary, row=1)
    async def unmute_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_role_muted(interaction, False)

    async def _set_role_muted(self, interaction: discord.Interaction, muted: bool) -> None:
        if not self.guild_id or interaction.guild is None or interaction.guild.id != self.guild_id:
            return await _reply(interaction, "Open My 4:20 Preferences inside your server.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await set_role_optout(self.guild_id, self.owner_id, opted_out=muted)
        except (Somewhere420StorageUnavailable, ValueError):
            return await _reply(interaction, "Couldn't save your Stoners preference. Check the database migration.")
        await open_personal_420(interaction)

    @discord.ui.button(label="Back to World Clock", emoji="🌍", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_somewhere_420 import open_somewhere_420
        await open_somewhere_420(interaction)


def _can_post(guild: discord.Guild, channel: discord.TextChannel) -> bool:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False
    p = channel.permissions_for(me)
    return bool(p.view_channel and p.send_messages and p.embed_links)


async def open_staff_420(interaction: discord.Interaction) -> None:
    if not interaction_has_manage_guild_authority(interaction) or interaction.guild is None:
        return await _reply(interaction, "Only server owners and managers can change scheduled announcements.")
    if not interaction.response.is_done():
        if interaction.message is not None:
            await interaction.response.defer()
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        data = await get_schedule("guild", interaction.guild.id)
    except Somewhere420StorageUnavailable:
        return await _reply(interaction, "Daily server settings need the private 4:20 database migration first.")
    await _panel(interaction, _settings_embed("guild", data), Staff420View(interaction.user.id, data))


class ServerSettingsModal(discord.ui.Modal, title="Daily Server 4:20 Settings"):
    zone = discord.ui.TextInput(label="IANA timezone", placeholder="America/New_York", max_length=90)
    quiet = discord.ui.TextInput(label="Quiet hours start-end (24h)", placeholder="22-08 or 00-00 for none", max_length=5)
    ping = discord.ui.TextInput(label="Ping opt-in /toke role: on or off", placeholder="off", max_length=3)

    def __init__(self, owner_id: int, saved: dict | None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.saved = saved or {}
        self.zone.default = str(self.saved.get("timezone") or "UTC")
        self.quiet.default = f"{int(self.saved.get('quiet_start',22)):02}-{int(self.saved.get('quiet_end',8)):02}"
        self.ping.default = "on" if self.saved.get("notify_role") else "off"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id or not interaction_has_manage_guild_authority(interaction):
            return await _reply(interaction, "Server management permission is required.")
        try:
            zone = valid_timezone(str(self.zone.value))
            parts = str(self.quiet.value).strip().split("-")
            if len(parts) != 2 or not all(x.isdigit() for x in parts):
                raise ValueError("Quiet hours must look like 22-08, or 00-00 for none.")
            start, end = map(int, parts)
            if not (0 <= start <= 23 and 0 <= end <= 23):
                raise ValueError("Quiet hours must be 00-23.")
            ping = _truth(str(self.ping.value))
        except ValueError as exc:
            return await _reply(interaction, str(exc))
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            latest = await get_schedule("guild", interaction.guild.id) or {}
            channel_id = int(latest.get("channel_id") or 0)
            channel = interaction.guild.get_channel(channel_id)
            # If the channel vanished, fail closed and disable, never auto-route.
            active = bool(latest.get("enabled") and isinstance(channel, discord.TextChannel) and _can_post(interaction.guild, channel))
            data = await save_schedule(
                "guild", interaction.guild.id, zone_name=zone, enabled=active,
                channel_id=channel_id, notify_role=ping, quiet_start=start, quiet_end=end,
            )
            if latest.get("role_reminder_enabled"):
                data = await configure_role_heads_up(
                    interaction.guild.id, role_id=int(latest.get("role_reminder_role_id") or 0),
                    enabled=True, zone_name=zone,
                )
        except (Somewhere420StorageUnavailable, ValueError):
            return await _reply(interaction, "Could not save 4:20 settings. No announcement was enabled.")
        await _panel(interaction, _settings_embed("guild", data), Staff420View(self.owner_id, data))


class Staff420View(_OwnedView):
    def __init__(self, owner_id: int, saved: dict | None):
        super().__init__(owner_id)
        self.saved = saved or {}

    @discord.ui.button(label="Choose Announcement Channel", emoji="🔎", style=discord.ButtonStyle.primary, row=0)
    async def channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild is None or not interaction_has_manage_guild_authority(interaction):
            return await _reply(interaction, "You need server management permission.")
        guild, owner = interaction.guild, self.owner_id
        async def chosen(pick: discord.Interaction, channel: discord.TextChannel) -> None:
            if not interaction_has_manage_guild_authority(pick) or not _can_post(guild, channel):
                return await _reply(pick, "Dank Shield lacks the required channel permissions.")
            if not pick.response.is_done():
                await pick.response.defer()
            try:
                old = await get_schedule("guild", guild.id) or {}
                saved = await save_schedule(
                    "guild", guild.id,
                    zone_name=str(old.get("timezone") or "UTC"),
                    enabled=bool(old.get("enabled")),
                    channel_id=channel.id,
                    notify_role=bool(old.get("notify_role")),
                    quiet_start=int(old.get("quiet_start", 22)),
                    quiet_end=int(old.get("quiet_end", 8)),
                )
            except (Somewhere420StorageUnavailable, ValueError):
                return await _reply(pick, "Couldn't save the channel. No changes to scheduled delivery.")
            await _panel(pick, _settings_embed("guild", saved), Staff420View(owner, saved))
        browser = DankGuildResourceBrowserView(
            guild=guild, author_id=owner, resource_kinds=("text",),
            on_pick=chosen, custom_id="dank:420:channel",
            title="4:20 Announcement Channel",
            predicate=lambda channel: isinstance(channel, discord.TextChannel) and _can_post(guild, channel),
            on_home=open_staff_420,
        )
        await _panel(interaction, browser.embed(), browser)

    @discord.ui.button(label="Edit Timezone / Quiet Hours / Role", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not interaction_has_manage_guild_authority(interaction):
            return await _reply(interaction, "You need Manage Server permission.")
        await interaction.response.send_modal(ServerSettingsModal(self.owner_id, self.saved))

    @discord.ui.button(label="Enable Daily 4:20", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_enabled(interaction, True)

    @discord.ui.button(label="Disable Daily 4:20", emoji="🛑", style=discord.ButtonStyle.danger, row=1)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_enabled(interaction, False)

    async def _set_enabled(self, interaction: discord.Interaction, on: bool) -> None:
        guild = interaction.guild
        if guild is None or not interaction_has_manage_guild_authority(interaction):
            return await _reply(interaction, "You need Manage Server permission.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            old = await get_schedule("guild", guild.id) or {}
            channel_id = int(old.get("channel_id") or 0)
            channel = guild.get_channel(channel_id)
            if on and (not isinstance(channel, discord.TextChannel) or not _can_post(guild, channel)):
                return await _reply(interaction, "Choose an accessible announcement channel before enabling.")
            if on and old.get("notify_role"):
                from stoney_verify.guild_config import get_guild_config
                from stoney_verify.community_pings_service import parse_community_pings, toke_role_ids
                cfg = await get_guild_config(guild.id, refresh=True)
                if str(cfg.get("source", "")).startswith("unavailable:"):
                    return await _reply(interaction, "Community & Pings unavailable: role ping not enabled.")
                _, rid = toke_role_ids(parse_community_pings(cfg), cfg)
                role = guild.get_role(rid)
                perms = channel.permissions_for(guild.me)
                if not isinstance(role, discord.Role) or not (role.mentionable or perms.mention_everyone):
                    return await _reply(interaction, "Map a valid opt-in /toke notify role with mention permission first.")
            saved = await save_schedule(
                "guild", guild.id, zone_name=str(old.get("timezone") or "UTC"), enabled=on,
                channel_id=channel_id, notify_role=bool(old.get("notify_role")),
                quiet_start=int(old.get("quiet_start",22)), quiet_end=int(old.get("quiet_end",8)),
            )
        except (Somewhere420StorageUnavailable, ValueError):
            return await _reply(interaction, "Unable to safely update the scheduled announcement.")
        await _panel(interaction, _settings_embed("guild", saved), Staff420View(self.owner_id, saved))

    @discord.ui.button(label="Choose Stoners Role", emoji="🌿", style=discord.ButtonStyle.primary, row=2)
    async def choose_stoners(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None or not interaction_has_manage_guild_authority(interaction):
            return await _reply(interaction, "Manage Server permission is required.")
        async def picked(pick: discord.Interaction, role: discord.Role) -> None:
            if not interaction_has_manage_guild_authority(pick):
                return await _reply(pick, "Manage Server permission is required.")
            if not pick.response.is_done():
                await pick.response.defer()
            try:
                old = await get_schedule("guild", guild.id) or {}
                await configure_role_heads_up(
                    guild.id, role_id=role.id, enabled=bool(old.get("role_reminder_enabled")),
                    zone_name=str(old.get("timezone") or "UTC"),
                )
            except (Somewhere420StorageUnavailable, ValueError):
                return await _reply(pick, "Couldn't save Stoners role; check the database migration.")
            await open_staff_420(pick)
        browser = DankGuildResourceBrowserView(
            guild=guild, author_id=self.owner_id, resource_kinds=("role",),
            on_pick=picked, custom_id="dank:420:stoners-role",
            title="Choose Stoners Heads-Up Role", on_home=open_staff_420,
        )
        await _panel(interaction, browser.embed(), browser)

    @discord.ui.button(label="Enable Stoners Heads-Up", emoji="✅", style=discord.ButtonStyle.success, row=3)
    async def enable_stoners(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_stoners_enabled(interaction, True)

    @discord.ui.button(label="Disable Stoners Heads-Up", emoji="🛑", style=discord.ButtonStyle.danger, row=3)
    async def disable_stoners(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._set_stoners_enabled(interaction, False)

    async def _set_stoners_enabled(self, interaction: discord.Interaction, enabled: bool) -> None:
        guild = interaction.guild
        if guild is None or not interaction_has_manage_guild_authority(interaction):
            return await _reply(interaction, "Manage Server permission is required.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            old = await get_schedule("guild", guild.id) or {}
            role_id = int(old.get("role_reminder_role_id") or 0)
            if enabled and role_id <= 0:
                # Reuse the existing configured Stoners /toke starter role
                # before requiring an extra picker step on customized servers.
                from stoney_verify.guild_config import get_guild_config
                from stoney_verify.community_pings_service import parse_community_pings, toke_role_ids
                cfg = await get_guild_config(guild.id, refresh=True)
                if str(cfg.get("source", "")).startswith("unavailable:"):
                    return await _reply(interaction, "Community & Pings settings are unavailable. Try later.")
                role_id, _notify = toke_role_ids(parse_community_pings(cfg), cfg)
            if not isinstance(guild.get_role(role_id), discord.Role):
                return await _reply(interaction, "Choose your Stoners role first using Search-Safe.")
            await configure_role_heads_up(
                guild.id, role_id=role_id, enabled=enabled,
                zone_name=str(old.get("timezone") or "UTC"),
            )
        except (Somewhere420StorageUnavailable, ValueError):
            return await _reply(interaction, "Couldn't save Stoners reminder setting; check the database migration.")
        await open_staff_420(interaction)

    @discord.ui.button(label="Back to World Clock", emoji="🌍", style=discord.ButtonStyle.secondary, row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_somewhere_420 import open_somewhere_420
        await open_somewhere_420(interaction)
