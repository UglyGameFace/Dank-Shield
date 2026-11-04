from __future__ import annotations

"""Native birthday setup inside /dank profile and /dank birthdays."""

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from .public_owner_authority import interaction_has_manage_guild_authority
from .public_self_roles_group import profile_group
from .public_setup_group import dank_group
from stoney_verify.birthday_service import (
    BirthdayStorageUnavailable, delete_member, get_guild, get_member,
    save_guild, save_member, upcoming, valid_date, valid_zone,
)

_REGISTERED = False
birthday_group = app_commands.Group(name="birthdays", description="Celebrate birthdays, privately and by choice.")


async def _reply(interaction: discord.Interaction, content: str) -> None:
    payload = dict(content=content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _defer(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    if interaction.type == discord.InteractionType.component:
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)


async def _update(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    await _defer(interaction)
    await interaction.edit_original_response(
        content=None, embed=embed, view=view, attachments=[],
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _manager(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction_has_manage_guild_authority(interaction)


def _safe_birthday_role(guild: discord.Guild, role: discord.Role) -> bool:
    me = guild.me
    if me is None or role.managed or role.is_default() or role >= me.top_role:
        return False
    if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
        return False
    # Never offer an existing access-control / staff / verification role as a birthday perk.
    if not any(label in role.name.lower() for label in ("birthday", "bday", "🎂")):
        return False
    perms = role.permissions
    return not any((
        perms.administrator, perms.manage_guild, perms.manage_roles,
        perms.manage_channels, perms.manage_messages, perms.ban_members,
        perms.kick_members, perms.moderate_members, perms.mention_everyone,
        perms.manage_webhooks, perms.view_audit_log,
    ))


def _member_embed(interaction: discord.Interaction, member: dict[str, Any] | None, settings: dict[str, Any] | None) -> discord.Embed:
    embed = discord.Embed(
        title="🎂 My Birthday",
        description="Your birthday is private by default. Only your month and day are saved, never your birth year.",
        color=discord.Color.gold(),
    )
    display = f"{int(member['birth_month']):02d}-{int(member['birth_day']):02d}" if member else "Not set"
    embed.add_field(name="Saved date", value=display, inline=False)
    embed.add_field(name="Public wishes", value="On ✅" if member and member.get("public_wishes") else "Off", inline=True)
    embed.add_field(name="Temporary birthday role", value="On ✅" if member and member.get("birthday_role") else "Off", inline=True)
    embed.add_field(name="Private birthday DM", value="On ✅" if member and member.get("dm_wishes") else "Off", inline=True)
    embed.add_field(
        name="Server celebrations",
        value=("Enabled" if settings and settings.get("enabled") else "Not enabled by server staff") +
              " · " + str((settings or {}).get("timezone") or "UTC"),
        inline=False,
    )
    embed.set_footer(text="Your birthday is not proof of age or Discord verification.")
    return embed


async def open_member_birthday(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "Open birthdays inside a server.")
    await _defer(interaction)
    try:
        member = await get_member(guild.id, interaction.user.id)
        settings = await get_guild(guild.id)
    except BirthdayStorageUnavailable:
        return await interaction.edit_original_response(content="Birthday storage is not ready. No changes were made.", embed=None, view=None)
    await interaction.edit_original_response(
        content=None, embed=_member_embed(interaction, member, settings),
        view=MemberBirthdayView(owner_id=interaction.user.id),
        allowed_mentions=discord.AllowedMentions.none(),
    )


class BirthdayDateModal(discord.ui.Modal, title="🎂 Set My Birthday"):
    month_day = discord.ui.TextInput(
        label="Month and day (MM-DD)",
        placeholder="06-20",
        min_length=4, max_length=5,
    )

    def __init__(self, original: dict[str, Any] | None) -> None:
        super().__init__()
        self.original = original
        if original:
            self.month_day.default = f"{int(original['birth_month']):02d}-{int(original['birth_day']):02d}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await _reply(interaction, "Use this inside a server.")
        try:
            parts = str(self.month_day.value).strip().split("-")
            if len(parts) != 2:
                raise ValueError("Use MM-DD, for example 06-20.")
            month, day = valid_date(int(parts[0]), int(parts[1]))
            old = await get_member(interaction.guild.id, interaction.user.id)
            await save_member(
                interaction.guild.id, interaction.user.id, month=month, day=day,
                public_wishes=bool((old or {}).get("public_wishes")),
                birthday_role=bool((old or {}).get("birthday_role")),
                dm_wishes=bool((old or {}).get("dm_wishes")),
            )
        except ValueError as exc:
            return await _reply(interaction, str(exc))
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Birthday storage is unavailable. Nothing changed.")
        await _reply(interaction, "🎂 Birthday saved privately. Open the birthday panel to choose your celebration preferences.")


class MemberBirthdayView(discord.ui.View):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _reply(interaction, "Only the member who opened this panel can edit it.")
            return False
        return True

    @discord.ui.button(label="Set Birthday", emoji="📅", style=discord.ButtonStyle.primary, row=0)
    async def set_date(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            saved = await get_member(interaction.guild.id, interaction.user.id)
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Birthday storage is unavailable.")
        await interaction.response.send_modal(BirthdayDateModal(saved))

    async def _toggle(self, interaction: discord.Interaction, field: str) -> None:
        guild = interaction.guild
        try:
            member = await get_member(guild.id, interaction.user.id)
            if not member:
                return await _reply(interaction, "Set your birthday first.")
            payload = {
                "public_wishes": bool(member.get("public_wishes")),
                "birthday_role": bool(member.get("birthday_role")),
                "dm_wishes": bool(member.get("dm_wishes")),
            }
            payload[field] = not payload[field]
            await save_member(
                guild.id, interaction.user.id, month=int(member["birth_month"]),
                day=int(member["birth_day"]), **payload,
            )
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Birthday storage is unavailable. Nothing changed.")
        await open_member_birthday(interaction)

    @discord.ui.button(label="Public Wishes", emoji="📣", row=0)
    async def public(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._toggle(interaction, "public_wishes")

    @discord.ui.button(label="Birthday Role", emoji="🏷️", row=0)
    async def role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._toggle(interaction, "birthday_role")

    @discord.ui.button(label="Birthday DM", emoji="💌", row=1)
    async def dm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._toggle(interaction, "dm_wishes")

    @discord.ui.button(label="Remove My Birthday", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await delete_member(interaction.guild.id, interaction.user.id)
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Storage is unavailable. Nothing was removed.")
        await open_member_birthday(interaction)

    @discord.ui.button(label="Back to Profile", emoji="↩️", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_profile_cards import profile_settings
        await profile_settings(interaction)


def _settings_embed(guild: discord.Guild, row: dict[str, Any] | None) -> discord.Embed:
    settings = row or {}
    channel = guild.get_channel(int(settings.get("channel_id") or 0))
    role = guild.get_role(int(settings.get("role_id") or 0))
    embed = discord.Embed(
        title="🎉 Birthday Server Setup",
        description="Staff configure this once. Members then opt in through /dank profile birthday.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Celebrations", value="Enabled ✅" if settings.get("enabled") else "Disabled", inline=True)
    embed.add_field(name="Time zone", value=str(settings.get("timezone") or "UTC"), inline=True)
    embed.add_field(name="Daily announcement", value=f"{int(settings.get('announce_hour') or 9):02d}:00 local", inline=True)
    embed.add_field(name="Channel", value=channel.mention if channel else "Not selected", inline=True)
    embed.add_field(name="Birthday-only role", value=role.mention if role else "Optional / not set", inline=True)
    embed.add_field(name="Leap-day birthdays", value="March 1" if settings.get("leap_day_policy") == "mar1" else "February 28", inline=True)
    embed.set_footer(text="No birth year collected · celebration is not age verification")
    return embed


async def open_staff_birthday(interaction: discord.Interaction) -> None:
    if not _manager(interaction):
        return await _reply(interaction, "Only server owners or managers can configure birthdays.")
    await _defer(interaction)
    try:
        row = await get_guild(interaction.guild.id)
    except BirthdayStorageUnavailable:
        return await interaction.edit_original_response(content="Birthday schema is not installed. No settings changed.", embed=None, view=None)
    await interaction.edit_original_response(
        content=None, embed=_settings_embed(interaction.guild, row),
        view=BirthdayStaffView(owner_id=interaction.user.id),
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _save_staff(interaction: discord.Interaction, **changes: Any) -> None:
    if not _manager(interaction):
        return await _reply(interaction, "Manage Server permission is required.")
    try:
        current = await get_guild(interaction.guild.id) or {}
        data = {
            "enabled": bool(current.get("enabled", False)),
            "zone_name": str(current.get("timezone") or "UTC"),
            "channel_id": int(current.get("channel_id") or 0),
            "role_id": int(current.get("role_id") or 0),
            "leap_policy": str(current.get("leap_day_policy") or "feb28"),
            "hour": int(current.get("announce_hour", 9)),
        }
        data.update(changes)
        await save_guild(interaction.guild.id, **data)
    except (BirthdayStorageUnavailable, ValueError) as exc:
        return await _reply(interaction, f"Birthday settings weren't changed: {exc}")
    await open_staff_birthday(interaction)


class BirthdayTimezoneModal(discord.ui.Modal, title="Birthday Announcement Schedule"):
    zone = discord.ui.TextInput(label="IANA timezone", placeholder="America/New_York", max_length=90)
    hour = discord.ui.TextInput(label="Daily announcement hour (0-23)", default="9", max_length=2)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            zone = valid_zone(str(self.zone.value))
            hour = int(str(self.hour.value))
            if not 0 <= hour <= 23:
                raise ValueError("Choose an hour from 0 to 23.")
        except ValueError as exc:
            return await _reply(interaction, str(exc))
        await _save_staff(interaction, zone_name=zone, hour=hour)


class BirthdayChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose birthday announcement channel", min_values=1, max_values=1,
            channel_types=[discord.ChannelType.text], row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        if not isinstance(channel, discord.TextChannel):
            return await _reply(interaction, "Select a normal text channel.")
        me = interaction.guild.me
        permissions = channel.permissions_for(me) if me else None
        if not permissions or not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            return await _reply(interaction, "Dank Shield needs View Channel, Send Messages, and Embed Links there.")
        await _save_staff(interaction, channel_id=channel.id)


class BirthdayRoleSelect(discord.ui.RoleSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="Choose an existing birthday-only role", min_values=1, max_values=1, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        if not isinstance(role, discord.Role) or not _safe_birthday_role(interaction.guild, role):
            return await _reply(interaction, "Choose a manageable cosmetic Birthday role, never a staff, verified, or privileged role.")
        await _save_staff(interaction, role_id=role.id)


class BirthdayStaffView(discord.ui.View):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.add_item(BirthdayChannelSelect())
        self.add_item(BirthdayRoleSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not _manager(interaction):
            await _reply(interaction, "Only the server manager who opened this setup can use it.")
            return False
        return True

    @discord.ui.button(label="Enable / Disable", emoji="🎂", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            current = await get_guild(interaction.guild.id) or {}
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Birthday storage is unavailable.")
        await _save_staff(interaction, enabled=not bool(current.get("enabled")))

    @discord.ui.button(label="Timezone & Hour", emoji="🕘", row=0)
    async def timezone(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(BirthdayTimezoneModal())

    @discord.ui.button(label="Feb 29 Policy", emoji="📅", row=0)
    async def leap(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            current = await get_guild(interaction.guild.id) or {}
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Birthday storage is unavailable.")
        await _save_staff(
            interaction, leap_policy="mar1" if current.get("leap_day_policy") != "mar1" else "feb28"
        )

    @discord.ui.button(label="Create Birthday Role", emoji="🏷️", style=discord.ButtonStyle.success, row=1)
    async def make_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return await _reply(interaction, "Dank Shield needs Manage Roles to create a birthday role.")
        try:
            role = await guild.create_role(
                name="🎂 Birthday", permissions=discord.Permissions.none(),
                mentionable=False, hoist=False, reason="Dank Shield opt-in birthday celebrations",
            )
        except (discord.Forbidden, discord.HTTPException):
            return await _reply(interaction, "Couldn't create the birthday role. Check Manage Roles and role hierarchy.")
        await _save_staff(interaction, role_id=role.id)


@birthday_group.command(name="me", description="Set your birthday and choose private or public celebrations.")
async def birthday_me(interaction: discord.Interaction) -> None:
    await open_member_birthday(interaction)


@birthday_group.command(name="setup", description="Manager: configure birthday channel, schedule, and temporary role.")
async def birthday_setup(interaction: discord.Interaction) -> None:
    await open_staff_birthday(interaction)


@birthday_group.command(name="upcoming", description="Show only birthdays members have chosen to share.")
async def birthday_upcoming(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "Open birthdays inside a server.")
    await _defer(interaction)
    try:
        settings = await get_guild(guild.id)
        if not settings or not settings.get("enabled"):
            return await interaction.edit_original_response(content="This server hasn't enabled birthdays.")
        zone = ZoneInfo(str(settings["timezone"]))
        today = datetime.now(timezone.utc).astimezone(zone).date()
        rows = await upcoming(guild.id)
    except BirthdayStorageUnavailable:
        return await interaction.edit_original_response(content="Birthday storage is unavailable.")
    from stoney_verify.birthday_service import celebrated_on
    results = []
    for offset in range(367):
        from datetime import timedelta
        candidate = today + timedelta(days=offset)
        for row in rows:
            if celebrated_on(row["birth_month"], row["birth_day"], candidate, str(settings.get("leap_day_policy") or "feb28")):
                member = guild.get_member(int(row["user_id"]))
                if member is not None and not member.bot:
                    results.append((candidate, member.display_name))
        if len(results) >= 10:
            break
    description = "\n".join(
        f"**{day.strftime('%b %d')}** · {discord.utils.escape_markdown(name)}"
        for day, name in results[:10]
    ) or "No shared birthdays are coming up."
    embed = discord.Embed(title="🎂 Upcoming Birthdays", description=description, color=discord.Color.gold())
    await interaction.edit_original_response(embed=embed, content=None)


def register_public_birthdays(bot: Any, tree: Any) -> None:
    del bot, tree
    global _REGISTERED
    if _REGISTERED:
        return
    if profile_group.get_command("birthday") is None:
        profile_group.add_command(app_commands.Command(
            name="birthday",
            description="Set your birthday and choose celebration preferences.",
            callback=open_member_birthday,
        ))
    if dank_group.get_command("birthdays") is None:
        dank_group.add_command(birthday_group)
    _REGISTERED = True
