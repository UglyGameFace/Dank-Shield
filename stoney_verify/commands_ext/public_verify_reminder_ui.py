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
)
from .public_verify_command_center import _OwnedView, _private, _replace, _require_staff


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
    return ""


def _embed(guild: discord.Guild, cfg: Mapping[str, Any], policy: ReminderPolicy) -> discord.Embed:
    channel = verification_channel(guild, cfg)
    blocker = _can_enable(guild, cfg)
    embed = discord.Embed(
        title="🔔 Unverified Member Reminders",
        description=(
            "Uses Dank Shield's **existing Simple Verify status and role mapping**, not a separate member list. "
            "This server controls its own settings. Members who verify stop qualifying automatically."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Status",
        value="Enabled" if policy.enabled else "Disabled (no messages are sent)",
        inline=True,
    )
    embed.add_field(
        name="Schedule",
        value=f"First after **{policy.delay_hours}h** • repeat at most every **{policy.repeat_hours}h** • max **{policy.max_reminders}** per join",
        inline=False,
    )
    embed.add_field(
        name="Destination",
        value=channel.mention if channel else "Not configured",
        inline=True,
    )
    embed.add_field(
        name="Delivery",
        value="Private DM • no public pings • atomic duplicate prevention",
        inline=False,
    )
    if blocker:
        embed.add_field(name="Setup required", value=blocker, inline=False)
    embed.set_footer(text="Disabled by default • Supabase delivery ledger required before enabling")
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
        label="Friendly DM wording (optional)", style=discord.TextStyle.paragraph,
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


class ReminderSettingsView(_OwnedView):
    def __init__(self, owner_id: int, policy: ReminderPolicy) -> None:
        super().__init__(owner_id)
        self.toggle.label = "Disable" if policy.enabled else "Enable"
        self.toggle.style = discord.ButtonStyle.danger if policy.enabled else discord.ButtonStyle.success

    @discord.ui.button(label="Enable", emoji="🔔", style=discord.ButtonStyle.success, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        cfg, policy = await _load(interaction.guild)
        if not policy.enabled:
            blocker = _can_enable(interaction.guild, cfg)
            if blocker:
                return await _private(interaction, f"❌ Cannot enable: {blocker}")
            if not await ledger_ready():
                return await _private(interaction, "❌ Reminder delivery table is unavailable. Apply the new Supabase migration before enabling.")
        if not await _save(interaction, cfg, replace(policy, enabled=not policy.enabled)):
            return
        fresh, current = await _load(interaction.guild)
        await _replace(interaction, embed=_embed(interaction.guild, fresh, current), view=ReminderSettingsView(self.owner_id, current))

    @discord.ui.button(label="Configure", emoji="⚙️", style=discord.ButtonStyle.primary, row=0)
    async def configure(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        _, policy = await _load(interaction.guild)
        await interaction.response.send_modal(SettingsModal(self.owner_id, policy))

    @discord.ui.button(label="Preview DM", emoji="✉️", style=discord.ButtonStyle.secondary, row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        cfg, policy = await _load(interaction.guild)
        channel = verification_channel(interaction.guild, cfg)
        if channel is None:
            return await _private(interaction, "❌ Configure the verification channel before previewing.")
        await _private(interaction, reminder_text(interaction.guild, channel, cfg, policy))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        cfg, policy = await _load(interaction.guild)
        await _replace(interaction, embed=_embed(interaction.guild, cfg, policy), view=ReminderSettingsView(self.owner_id, policy))

    @discord.ui.button(label="Back to Verification", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        from .public_verify_command_center import VerifyCenterView, _center_embed
        await _replace(interaction, embed=_center_embed(), view=VerifyCenterView(self.owner_id))


async def open_reminder_settings(interaction: discord.Interaction, owner_id: int) -> None:
    if not await _require_staff(interaction):
        return
    cfg, policy = await _load(interaction.guild)
    await _replace(interaction, embed=_embed(interaction.guild, cfg, policy), view=ReminderSettingsView(owner_id, policy))
