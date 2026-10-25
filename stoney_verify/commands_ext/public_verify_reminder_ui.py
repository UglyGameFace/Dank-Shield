from __future__ import annotations

"""Staff-only reminder controls inside the existing Verification Center."""

from dataclasses import replace
from typing import Any, Mapping

import discord

from ..guild_config import compare_and_swap_guild_config_key, get_guild_config
from ..setup_engine.verification_modes import basic_verify_allowed_for_guild
from ..verification_reminders import (
    REMINDER_CONFIG_KEY,
    ReminderPolicy,
    ledger_ready,
    parse_policy,
    reminder_text,
    verification_channel,
    delivery_channel,
)
from .public_verify_command_center import _OwnedView, _private, _replace, _require_staff
from ..ui.current_channel import current_text_channel
from ..ui.resource_browser import DankGuildResourceBrowserView


async def _load(guild: discord.Guild) -> tuple[Mapping[str, Any], ReminderPolicy]:
    raw = await get_guild_config(guild.id, refresh=True)
    return raw, parse_policy(raw)


def _role_id(cfg: Mapping[str, Any], key: str) -> int:
    try:
        return int(str(cfg.get(key) or "0"))
    except (TypeError, ValueError):
        return 0


def _can_enable(guild: discord.Guild, cfg: Mapping[str, Any]) -> str:
    if str(cfg.get("source") or "").startswith(("unavailable:", "unconfigured:")):
        return "The server's saved configuration is unavailable."
    if not basic_verify_allowed_for_guild(guild, cfg):
        return "This server does not currently have Simple Verify enabled."
    if not guild.get_role(_role_id(cfg, "unverified_role_id")):
        return "Map the Unverified role in Verification Center first."
    if not guild.get_role(_role_id(cfg, "verified_role_id")):
        return "Map the Verified role in Verification Center first."
    if verification_channel(guild, cfg) is None:
        return "Save an existing text channel as the verification channel first."
    policy = parse_policy(cfg)
    if policy.delivery == "channel":
        channel = delivery_channel(guild, policy)
        if channel is None:
            return "Choose a reminder text channel below."
        me = guild.me
        if me is None or not channel.permissions_for(me).view_channel or not channel.permissions_for(me).send_messages:
            return "Dank Shield needs View Channel and Send Messages in the reminder channel."
    return ""


def _embed(guild: discord.Guild, cfg: Mapping[str, Any], policy: ReminderPolicy) -> discord.Embed:
    channel = verification_channel(guild, cfg)
    blocker = _can_enable(guild, cfg)
    embed = discord.Embed(
        title="🔔 Unverified Member Reminders",
        description=(
            "Uses Dank Shield's **existing Simple Verify status and role mapping**, not a separate member list. "
            "This server controls its own settings. Members who verify stop qualifying automatically. "
            "Enabling does not send instantly; reminders begin after the configured delay."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Status",
        value="Enabled (delivery requires a healthy database)" if policy.enabled else "Disabled (no messages are sent)",
        inline=True,
    )
    embed.add_field(
        name="Schedule",
        value=f"First after **{policy.delay_hours}h** • repeat at most every **{policy.repeat_hours}h** • max **{policy.max_reminders}** per join",
        inline=False,
    )
    embed.add_field(
        name="Verification channel",
        value=channel.mention if channel else "Not configured",
        inline=True,
    )
    embed.add_field(
        name="Delivery",
        value=(
            "Private DM (no public ping)" if policy.delivery == "dm"
            else "Member mention in " + (
                delivery_channel(guild, policy).mention
                if delivery_channel(guild, policy) else "an unconfigured channel"
            )
        ) + " • atomic duplicate prevention",
        inline=False,
    )
    if blocker:
        embed.add_field(name="Setup required", value=blocker, inline=False)
    embed.set_footer(
        text="Use This Channel sends reminders here; Choose Other Channel opens Search. "
             "No messages are sent immediately."
    )
    return embed


async def _save(interaction: discord.Interaction, cfg: Mapping[str, Any], updated: ReminderPolicy) -> bool:
    try:
        applied, saved = await compare_and_swap_guild_config_key(
            interaction.guild.id,
            REMINDER_CONFIG_KEY,
            expected=cfg.get(REMINDER_CONFIG_KEY),
            value=updated.to_payload(),
            source="verify_unverified_reminder_manager",
        )
        if not applied:
            await _private(interaction, "⚠️ Another staff member changed these settings. Refresh before editing.")
            return False
        if parse_policy(saved) != updated:
            await _private(interaction, "❌ The saved reminder settings could not be confirmed.")
            return False
        return True
    except Exception as exc:
        await _private(interaction, f"❌ Could not safely save reminder settings ({type(exc).__name__}).")
        return False


class SettingsModal(discord.ui.Modal, title="Verification Reminder Settings"):
    first_delay = discord.ui.TextInput(label="First reminder after (hours, 1-720)", max_length=3)
    repeat_delay = discord.ui.TextInput(label="Repeat cooldown (hours, 12-720)", max_length=3)
    max_times = discord.ui.TextInput(label="Maximum reminders per join (1-5)", max_length=1)
    message = discord.ui.TextInput(
        label="Friendly reminder wording (optional)", style=discord.TextStyle.paragraph,
        max_length=240, required=False,
        placeholder="Please verify to unlock access to our server.",
    )

    def __init__(self, owner_id: int, policy: ReminderPolicy) -> None:
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.first_delay.default = str(policy.delay_hours)
        self.repeat_delay.default = str(policy.repeat_hours)
        self.max_times.default = str(policy.max_reminders)
        self.message.default = policy.message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id or not await _require_staff(interaction):
            return
        try:
            first, repeat, maximum = int(self.first_delay.value), int(self.repeat_delay.value), int(self.max_times.value)
        except ValueError:
            return await _private(interaction, "❌ All three scheduling fields must contain whole numbers.")
        if not (1 <= first <= 720 and 12 <= repeat <= 720 and 1 <= maximum <= 5):
            return await _private(interaction, "❌ First delay must be 1-720h, repeat 12-720h, max 1-5.")
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        cfg, old = await _load(interaction.guild)
        updated = replace(
            old,
            delay_hours=first,
            repeat_hours=repeat,
            max_reminders=maximum,
            message=str(self.message.value or "").strip()[:240],
        )
        if await _save(interaction, cfg, updated):
            fresh, current = await _load(interaction.guild)
            await _private(interaction, embed=_embed(interaction.guild, fresh, current), view=ReminderSettingsView(self.owner_id, current))


async def _save_delivery_channel(
    interaction: discord.Interaction, owner_id: int, channel: discord.TextChannel,
) -> None:
    """Identical save path for the current-channel shortcut and Search-Safe."""
    if interaction.guild is None or not await _require_staff(interaction):
        return
    guild = interaction.guild
    actual = guild.get_channel(int(channel.id))
    if not isinstance(actual, discord.TextChannel) or actual.guild.id != guild.id:
        return await _private(interaction, "❌ Choose a text channel in this server.")
    bot_member = guild.me
    if bot_member is None:
        return await _private(interaction, "❌ Dank Shield cannot verify its channel access.")
    perms = actual.permissions_for(bot_member)
    if not (perms.view_channel and perms.send_messages):
        return await _private(interaction, "❌ Dank Shield needs View Channel and Send Messages there.")
    if not interaction.response.is_done():
        await interaction.response.defer()
    cfg, policy = await _load(guild)
    updated = replace(policy, reminder_channel_id=int(actual.id), delivery="channel")
    if await _save(interaction, cfg, updated):
        fresh, current = await _load(guild)
        await _replace(
            interaction,
            embed=_embed(guild, fresh, current),
            view=ReminderSettingsView(owner_id, current),
        )


async def _choose_reminder_channel(interaction: discord.Interaction, owner_id: int) -> None:
    if interaction.guild is None or not await _require_staff(interaction):
        return
    guild = interaction.guild

    async def picked(pick: discord.Interaction, channel: discord.TextChannel) -> None:
        await _save_delivery_channel(pick, owner_id, channel)

    browser = DankGuildResourceBrowserView(
        guild=guild, author_id=owner_id, resource_kinds=("text",),
        on_pick=picked, custom_id="dank:verify:reminders:text:search_safe",
        title="Choose Reminder Channel",
        predicate=lambda channel: (
            isinstance(channel, discord.TextChannel)
            and guild.me is not None
            and channel.permissions_for(guild.me).view_channel
            and channel.permissions_for(guild.me).send_messages
        ),
        on_home=lambda pick: open_reminder_settings(pick, owner_id),
    )
    await _replace(interaction, embed=browser.embed(), view=browser)


class ReminderSettingsView(_OwnedView):
    def __init__(self, owner_id: int, policy: ReminderPolicy) -> None:
        super().__init__(owner_id)
        self.policy = policy
        self.toggle.label = "Disable" if policy.enabled else "Enable"
        self.toggle.style = discord.ButtonStyle.danger if policy.enabled else discord.ButtonStyle.success

    @discord.ui.button(label="Enable", emoji="🔔", style=discord.ButtonStyle.success, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        cfg, policy = await _load(interaction.guild)
        if not policy.enabled:
            blocker = _can_enable(interaction.guild, cfg)
            if blocker:
                return await _private(interaction, f"❌ Cannot enable: {blocker}")
            if not await ledger_ready():
                return await _private(interaction, "❌ Discloud PostgreSQL reminder ledger is unavailable. Verify the private database URL and apply the PostgreSQL reminder migration.")
        if not await _save(interaction, cfg, replace(policy, enabled=not policy.enabled)):
            return
        fresh, current = await _load(interaction.guild)
        await _replace(interaction, embed=_embed(interaction.guild, fresh, current), view=ReminderSettingsView(self.owner_id, current))

    @discord.ui.button(label="Configure", emoji="⚙️", style=discord.ButtonStyle.primary, row=0)
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        # Opening a modal must acknowledge the click immediately. Use the
        # current displayed defaults and reload authoritative settings on submit.
        await interaction.response.send_modal(SettingsModal(self.owner_id, self.policy))

    @discord.ui.button(label="Preview", emoji="✉️", style=discord.ButtonStyle.secondary, row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        cfg, policy = await _load(interaction.guild)
        channel = verification_channel(interaction.guild, cfg)
        if channel is None:
            return await _private(interaction, "❌ Configure the verification channel before previewing.")
        await _private(interaction, reminder_text(interaction.guild, channel, cfg, policy))

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.success, row=1)
    async def use_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        channel = current_text_channel(interaction)
        if channel is None:
            return await _private(
                interaction,
                "❌ Open this menu in the text channel you want to use, "
                "then tap Use This Channel. Or choose another channel with Search."
            )
        await _save_delivery_channel(interaction, self.owner_id, channel)

    @discord.ui.button(label="Choose Other Channel", emoji="🔎", style=discord.ButtonStyle.secondary, row=1)
    async def choose_other_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _choose_reminder_channel(interaction, self.owner_id)

    @discord.ui.button(label="Use DMs", emoji="✉️", style=discord.ButtonStyle.secondary, row=1)
    async def use_dms(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        cfg, policy = await _load(interaction.guild)
        if await _save(interaction, cfg, replace(policy, delivery="dm")):
            fresh, current = await _load(interaction.guild)
            await _replace(interaction, embed=_embed(interaction.guild, fresh, current), view=ReminderSettingsView(self.owner_id, current))

    @discord.ui.button(label="Check Database", emoji="🩺", style=discord.ButtonStyle.secondary, row=3)
    async def check_database(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        if await ledger_ready():
            await _private(interaction, "✅ Private PostgreSQL reminder ledger is reachable and readable. This checks storage only, not whether a member has been sent a reminder.")
        else:
            await _private(interaction, "❌ Private PostgreSQL reminder ledger is unavailable. Reminder sending pauses until the connection and table are healthy. Check the private database URL, VLAN and PostgreSQL service.")

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        cfg, policy = await _load(interaction.guild)
        await _replace(interaction, embed=_embed(interaction.guild, cfg, policy), view=ReminderSettingsView(self.owner_id, policy))

    @discord.ui.button(label="Back to Verification", emoji="◀️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        from .public_verify_command_center import VerifyCenterView, _center_embed
        await _replace(interaction, embed=_center_embed(), view=VerifyCenterView(self.owner_id))


async def open_reminder_settings(interaction: discord.Interaction, owner_id: int) -> None:
    if not await _require_staff(interaction):
        return
    if not interaction.response.is_done():
        await interaction.response.defer()
    cfg, policy = await _load(interaction.guild)
    await _replace(interaction, embed=_embed(interaction.guild, cfg, policy), view=ReminderSettingsView(owner_id, policy))
