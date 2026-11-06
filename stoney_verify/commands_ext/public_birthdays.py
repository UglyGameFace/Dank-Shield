from __future__ import annotations

"""Native birthday setup inside /dank profile and /dank birthdays."""

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from .public_owner_authority import interaction_has_manage_guild_authority
from .public_self_roles_group import profile_group
from .public_setup_group import dank_group
from stoney_verify.birthday_service import (
    BirthdayStorageUnavailable, delete_member, get_guild, get_member,
    save_guild, save_member, upcoming, valid_birth_date, valid_zone,
)
from stoney_verify.birthday_runtime import _manageable_role as _safe_birthday_role

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
    if interaction.type == discord.InteractionType.component and bool(
        getattr(getattr(interaction.message, "flags", None), "ephemeral", False)
    ):
        await interaction.response.defer()
    else:
        # Persistent public profile panels must NEVER be replaced by private data.
        await interaction.response.defer(ephemeral=True, thinking=True)


def _manager(interaction: discord.Interaction) -> bool:
    return interaction.guild is not None and interaction_has_manage_guild_authority(interaction)


def _member_embed(interaction: discord.Interaction, member: dict[str, Any] | None, settings: dict[str, Any] | None) -> discord.Embed:
    embed = discord.Embed(
        title="🎂 My Birthday",
        description="Your full birth date is private. Public celebrations only show the month and day.",
        color=discord.Color.gold(),
    )
    if member and member.get("birth_year"):
        display = (
            f"{int(member['birth_month']):02d}-{int(member['birth_day']):02d}-"
            f"{int(member['birth_year']):04d}"
        )
    elif member:
        display = (
            f"{int(member['birth_month']):02d}-{int(member['birth_day']):02d}"
            " (add birth year)"
        )
    else:
        display = "Not set"
    embed.add_field(name="Saved date", value=display, inline=False)
    preferences = (
        f"📣 Public wishes: {'On ✅' if member and member.get('public_wishes') else 'Off'}\n"
        f"🏷️ Birthday role: {'On ✅' if member and member.get('birthday_role') else 'Off'}\n"
        f"💌 Private DM: {'On ✅' if member and member.get('dm_wishes') else 'Off'}"
    )
    embed.add_field(name="My celebration choices", value=preferences, inline=False)
    embed.add_field(
        name="Server celebrations",
        value=("Enabled" if settings and settings.get("enabled") else "Not enabled by server staff")
              + " · " + str((settings or {}).get("timezone") or "UTC"),
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
        view=MemberBirthdayView(owner_id=interaction.user.id, saved=member),
        allowed_mentions=discord.AllowedMentions.none(),
    )


class BirthdayDateModal(discord.ui.Modal, title="🎂 Set My Birthday"):
    full_date = discord.ui.TextInput(
        label="Birth date (MM-DD-YYYY)",
        placeholder="06-20-2002",
        min_length=10, max_length=10,
    )

    def __init__(self, original: dict[str, Any] | None) -> None:
        super().__init__()
        if original and original.get("birth_year"):
            self.full_date.default = (
                f"{int(original['birth_month']):02d}-{int(original['birth_day']):02d}-"
                f"{int(original['birth_year']):04d}"
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await _reply(interaction, "Use this inside a server.")
        try:
            parts = str(self.full_date.value).strip().split("-")
            if len(parts) != 3 or [len(part) for part in parts] != [2, 2, 4] or not all(
                part.isascii() and part.isdecimal() for part in parts
            ):
                raise ValueError("Use MM-DD-YYYY, for example 06-20-2002.")
            month, day, year = valid_birth_date(*(int(part) for part in parts))
            await _defer(interaction)
            old = await get_member(interaction.guild.id, interaction.user.id)
            await save_member(
                interaction.guild.id, interaction.user.id, month=month, day=day, year=year,
                public_wishes=bool((old or {}).get("public_wishes")),
                birthday_role=bool((old or {}).get("birthday_role")),
                dm_wishes=bool((old or {}).get("dm_wishes")),
            )
        except ValueError as exc:
            return await _reply(interaction, str(exc))
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Couldn't confirm your birthday save. Reopen My Birthday to check the saved date.")
        await open_member_birthday(interaction)


class MemberBirthdayView(discord.ui.View):
    def __init__(self, *, owner_id: int, saved: dict[str, Any] | None = None) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.saved = saved

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _reply(interaction, "Only the member who opened this panel can edit it.")
            return False
        return True

    @discord.ui.button(label="Set Birthday", emoji="📅", style=discord.ButtonStyle.primary, row=0)
    async def set_date(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Discord requires modals to be the first interaction response. Avoid a
        # potentially slow storage read before opening the member's form.
        await interaction.response.send_modal(BirthdayDateModal(self.saved))

    async def _revoke_lease(self, interaction: discord.Interaction) -> None:
        from stoney_verify.birthday_service import get_role_lease, clear_role_lease
        from stoney_verify.birthday_runtime import _can_remove_role
        guild = interaction.guild
        lease = await get_role_lease(guild.id, interaction.user.id)
        if not lease:
            return
        role = guild.get_role(int(lease["role_id"]))
        member = interaction.user
        if role is None or role not in member.roles:
            await clear_role_lease(guild.id, member.id)
            return
        if not _can_remove_role(guild, role):
            return  # Keep lease for later retry rather than losing cleanup.
        try:
            await member.remove_roles(role, reason="Member opted out of birthday role")
        except (discord.Forbidden, discord.HTTPException):
            return
        await clear_role_lease(guild.id, member.id)


    async def _toggle(self, interaction: discord.Interaction, field: str) -> None:
        guild = interaction.guild
        await _defer(interaction)
        try:
            member = await get_member(guild.id, interaction.user.id)
            if not member:
                return await _reply(interaction, "Set your birthday first.")
            if not member.get("birth_year"):
                return await _reply(interaction, "Add your birth year with Set Birthday first.")
            payload = {
                "public_wishes": bool(member.get("public_wishes")),
                "birthday_role": bool(member.get("birthday_role")),
                "dm_wishes": bool(member.get("dm_wishes")),
            }
            payload[field] = not payload[field]
            await save_member(
                guild.id, interaction.user.id, month=int(member["birth_month"]),
                day=int(member["birth_day"]), year=int(member["birth_year"]), **payload,
            )
            if field == "birthday_role" and not payload["birthday_role"]:
                await self._revoke_lease(interaction)
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Couldn't confirm the update. Reopen My Birthday to check your preferences.")
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
        await _defer(interaction)
        try:
            await delete_member(interaction.guild.id, interaction.user.id)
        except BirthdayStorageUnavailable:
            return await _reply(interaction, "Couldn't confirm removal. Reopen My Birthday to check your saved date.")
        try:
            await self._revoke_lease(interaction)
        except BirthdayStorageUnavailable:
            await _reply(interaction, "Your birthday was removed. Temporary role cleanup will retry when storage is available.")
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
        description="Staff configure this once. Members then opt in through /dank birthdays me.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Celebration schedule",
        value=("Enabled ✅" if settings.get("enabled") else "Disabled")
              + " · " + str(settings.get("timezone") or "UTC")
              + f" · {int(settings.get('announce_hour', 9)):02d}:00 local",
        inline=False,
    )
    embed.add_field(
        name="Discord destination",
        value=f"Channel: {channel.mention if channel else 'Not selected'}\n"
              f"Role: {role.mention if role else 'Optional / not set'}",
        inline=False,
    )
    embed.add_field(
        name="February 29 policy",
        value="March 1" if settings.get("leap_day_policy") == "mar1" else "February 28",
        inline=False,
    )
    embed.set_footer(text="Birth years stay private · celebration is not age verification")
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


async def _save_staff(interaction: discord.Interaction, *, toggle: str = "", **changes: Any) -> None:
    if not _manager(interaction):
        return await _reply(interaction, "Manage Server permission is required.")
    await _defer(interaction)
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
        if toggle == "enabled":
            data["enabled"] = not data["enabled"]
        elif toggle == "leap_policy":
            data["leap_policy"] = "mar1" if data["leap_policy"] == "feb28" else "feb28"
        await save_guild(interaction.guild.id, **data)
    except (BirthdayStorageUnavailable, ValueError) as exc:
        return await _reply(interaction, f"Couldn't confirm birthday settings. Reopen setup to check them. {exc}")
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


async def _save_birthday_channel(interaction: discord.Interaction, channel: Any) -> None:
    guild = interaction.guild
    channel = guild.get_channel(channel.id) if guild else None
    if not isinstance(channel, discord.TextChannel):
        return await _reply(interaction, "Select a normal text channel in this server.")
    me = guild.me
    permissions = channel.permissions_for(me) if me else None
    if not permissions or not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
        return await _reply(interaction, "Dank Shield needs View Channel, Send Messages, and Embed Links there.")
    await _save_staff(interaction, channel_id=channel.id)


async def _save_birthday_role(interaction: discord.Interaction, role: Any) -> None:
    guild = interaction.guild
    role = guild.get_role(role.id) if guild else None
    if not isinstance(role, discord.Role) or not _safe_birthday_role(guild, role):
        return await _reply(interaction, "Choose a manageable cosmetic Birthday role, never a staff, verified, or privileged role.")
    await _save_staff(interaction, role_id=role.id)


class BirthdayStaffView(discord.ui.View):
    def __init__(self, *, owner_id: int) -> None:
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not _manager(interaction):
            await _reply(interaction, "Only the server manager who opened this setup can use it.")
            return False
        return True

    async def _browse(self, interaction: discord.Interaction, *, roles: bool) -> None:
        from stoney_verify.ui import DankGuildResourceBrowserView
        if not _manager(interaction):
            return await _reply(interaction, "Manage Server permission is required.")
        guild = interaction.guild
        view = DankGuildResourceBrowserView(
            guild=guild, author_id=self.owner_id,
            resource_kinds=("role",) if roles else ("text",),
            on_pick=_save_birthday_role if roles else _save_birthday_channel,
            custom_id="dank:birthday:role" if roles else "dank:birthday:channel",
            title="Birthday Role" if roles else "Birthday Announcement Channel",
            predicate=(lambda role: _safe_birthday_role(guild, role)) if roles else None,
            on_home=open_staff_birthday,
        )
        await _defer(interaction)
        await interaction.edit_original_response(embed=view.embed(), view=view)

    @discord.ui.button(label="Announcement Channel", emoji="📣", row=2)
    async def channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._browse(interaction, roles=False)

    @discord.ui.button(label="Choose Birthday Role", emoji="🏷️", row=2)
    async def choose_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._browse(interaction, roles=True)

    @discord.ui.button(label="No Birthday Role", row=2)
    async def clear_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _save_staff(interaction, role_id=0)

    @discord.ui.button(label="Enable / Disable", emoji="🎂", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _save_staff(interaction, toggle="enabled")

    @discord.ui.button(label="Timezone & Hour", emoji="🕘", row=0)
    async def timezone(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(BirthdayTimezoneModal())

    @discord.ui.button(label="Feb 29 Policy", emoji="📅", row=0)
    async def leap(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _save_staff(interaction, toggle="leap_policy")

    @discord.ui.button(label="Create Birthday Role", emoji="🏷️", style=discord.ButtonStyle.success, row=1)
    async def make_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not _manager(interaction):
            return await _reply(interaction, "Manage Server permission is required.")
        guild = interaction.guild
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return await _reply(interaction, "Dank Shield needs Manage Roles to create a birthday role.")
        await _defer(interaction)
        role = next(
            (value for value in guild.roles
             if value.name == "🎂 Birthday" and _safe_birthday_role(guild, value)),
            None,
        )
        if role is None:
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


@birthday_group.command(name="set", description="Privately enter your full birth date including year.")
async def birthday_set(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        return await _reply(interaction, "Open birthdays inside a server.")
    await interaction.response.send_modal(BirthdayDateModal(None))


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
    from stoney_verify.birthday_service import next_birthday
    results = []
    for row in rows:
        member = guild.get_member(int(row["user_id"]))
        if member is not None and not member.bot:
            candidate = next_birthday(
                row["birth_month"], row["birth_day"], today,
                str(settings.get("leap_day_policy") or "feb28"),
            )
            results.append((candidate, member.display_name))
    results.sort(key=lambda item: item[0])
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
