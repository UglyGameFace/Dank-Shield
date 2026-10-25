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
    ManualReminderUnavailable,
    send_due_now_in_designated_channel,
)
from ..verification_reminder_manual import (
    ManualPreview,
    preview_manual_reminders,
    send_previewed_manual_reminders,
    select_manual_group,
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
            "**Automatic reminders** follow the saved hours and repeat limits. "
            "**Check Scheduled Due** checks that schedule now. "
            "**Send Reminder Now** scans members against your server's saved Unverified role, "
            "previews eligible people, and can remind them before the automatic delay "
            "by channel or DM. No permanent manual reminder attempt limit."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Status",
        value="Automatic reminders: enabled (requires healthy database)" if policy.enabled
        else "Automatic reminders: disabled (staff manual send is separate)",
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
        text="Use This Channel SAVES the destination (no instant post). "
             "Scheduled checks respect the delay. Manual sends need preview and "
             "confirmation and do not change automatic reminder limits."
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


class ConfirmDueReminderView(_OwnedView):
    """Explicit staff confirmation before trying the saved due-only workflow."""

    def __init__(self, owner_id: int, guild_id: int, channel_id: int) -> None:
        super().__init__(owner_id)
        self.guild_id = int(guild_id)
        self.channel_id = int(channel_id)
        self.used = False

    @discord.ui.button(
        label="Confirm Scheduled Check", emoji="📣",
        style=discord.ButtonStyle.success, row=0,
    )
    async def confirm_due(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        _ = button
        if (interaction.guild is None
                or int(interaction.guild.id) != self.guild_id
                or not await _require_staff(interaction)):
            return
        if self.used:
            return await _private(
                interaction, "This check was already requested. Use Refresh before sending again."
            )
        self.used = True
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            claimed = await send_due_now_in_designated_channel(
                interaction.client, interaction.guild, channel_id=self.channel_id,
            )
        except ManualReminderUnavailable as exc:
            return await _private(interaction, f"❌ No reminders sent: {exc}")
        except Exception as exc:
            print(f"⚠️ verify manual due trigger unavailable kind={type(exc).__name__}")
            return await _private(
                interaction, "❌ Couldn't safely check the due reminders. "
                             "No additional retry was started; automatic delivery stays active."
            )
        if claimed == 0:
            return await _private(
                interaction,
                f"✅ Checked the next page of up to 100 guild members for <#{self.channel_id}>. "
                "No scheduled reminder attempts were claimed on that page. "
                "This is not a count of all unverified members in the server; "
                "the first delay, repeat interval and maximum per join still apply."
            )
        await _private(
            interaction,
            f"✅ Requested {claimed} due reminder attempt(s) in <#{self.channel_id}>. "
            "Only still-unverified members who met the saved schedule qualified. "
            "Each attempt is counted even if Discord could not deliver its message. "
            "Automatic checks will continue normally."
        )

    @discord.ui.button(
        label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary, row=0,
    )
    async def cancel_due(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        _ = button
        self.used = True
        await _private(interaction, "Cancelled. No manual reminder check was started.")




def _manual_preview_embed(guild: discord.Guild, preview: ManualPreview) -> discord.Embed:
    names = ", ".join(f"<@{member_id}>" for member_id in preview.candidates)
    destination = (
        f"<#{preview.channel_id}> (one grouped announcement)"
        if preview.delivery == "channel"
        else "Private DM to each selected member"
    )
    start = preview.after + 1 if preview.candidates else 0
    end = preview.after + len(preview.candidates)
    card = discord.Embed(
        title="📣 Review unverified reminders",
        description=(
            f"**Your server's Unverified role:** <@&{preview.role_id}>\n"
            f"**Send via:** {destination}\n"
            f"**Server members checked:** **{preview.scanned}** (complete live scan)\n"
            f"**Members with that role:** **{preview.mapped_pending}**\n"
            f"**Still needing verification:** **{preview.pending}**\n"
            f"**Also marked verified/staff:** **{preview.already_verified_or_staff}**\n"
            f"**Cannot access verification:** **{preview.without_access}**\n"
            f"**Already reminded / cooling down:** **{preview.manual_limited}**\n"
            f"**Ready for a manual reminder:** **{preview.potential}**\n\n"
            f"**Group {start}–{end} of {preview.potential}:** {names or 'No eligible members'}\n\n"
            "Everyone in this group is selected by default. You can choose fewer "
            "from the menu before confirming. Additional eligible people can be "
            "reviewed with Next Group. Membership, roles, access and manual cooldown "
            "are checked again before delivery."
        ),
        color=discord.Color.blurple(),
    )
    card.add_field(
        name="Message to send", value=preview.message[:1000], inline=False,
    )
    card.set_footer(
        text="Manual reminders: no per-join limit • at least 6 hours between "
             "manual attempts to the same member • up to 25 per confirmed group. "
             "Automatic reminder history is unchanged."
    )
    return card

class ManualPreviewView(_OwnedView):
    """Owner-only snapshot group: optional recipient selection and safe confirmation."""

    def __init__(self, owner_id: int, preview: ManualPreview) -> None:
        super().__init__(owner_id, timeout=180)
        self.preview = preview
        self.used = False
        self.selected_ids = tuple(preview.candidates)
        self.confirm_manual.disabled = not bool(preview.candidates)
        self.next_page.disabled = not bool(preview.next_after)
        if preview.candidates:
            name_by_id = dict(zip(preview.eligible_ids, preview.eligible_names))
            self.recipient_selector = discord.ui.Select(
                placeholder="Recipients: all selected (or choose fewer)",
                min_values=1,
                max_values=len(preview.candidates),
                options=[
                    discord.SelectOption(
                        label=name_by_id.get(uid, str(uid))[:90],
                        value=str(uid), description=f"Member ID: {uid}"[:100],
                        default=True,
                    )
                    for uid in preview.candidates
                ],
                row=1,
            )
            self.recipient_selector.callback = self.choose_recipients
            self.add_item(self.recipient_selector)

    async def choose_recipients(self, interaction: discord.Interaction) -> None:
        if not await _require_staff(interaction):
            return
        if self.used:
            return await _private(interaction, "This preview was already used.")
        self.selected_ids = tuple(int(value) for value in self.recipient_selector.values)
        await _private(
            interaction,
            f"✅ **{len(self.selected_ids)}** selected. "
            "Press Confirm Manual Send to send, or Cancel.",
        )

    @discord.ui.button(
        label="Confirm Manual Send", emoji="📣",
        style=discord.ButtonStyle.success, row=0,
    )
    async def confirm_manual(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        _ = button
        if (interaction.guild is None or interaction.guild.id != self.preview.guild_id
                or not await _require_staff(interaction)):
            return
        if self.used:
            return await _private(interaction, "This preview has already been used. Reopen Send Reminder Now.")
        if not self.selected_ids:
            return await _private(interaction, "Choose at least one member first.")
        self.used = True
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            report = await send_previewed_manual_reminders(
                interaction.client, interaction.guild,
                replace(self.preview, candidates=self.selected_ids),
            )
        except ManualReminderUnavailable as exc:
            return await _private(interaction, f"❌ No manual messages authorized: {exc}")
        except Exception as exc:
            print(f"⚠️ verify manual send unavailable kind={type(exc).__name__}")
            return await _private(
                interaction, "❌ Couldn't confirm manual reminders. "
                "Check the database and channel before retrying; uncertain "
                "attempts may already have consumed their safety cooldown."
            )
        await _private(
            interaction,
            f"**Manual reminder result ({self.preview.delivery.upper()})**\n"
            f"Selected: **{report.selected}** • Reserved: **{report.reserved}** • "
            f"Successfully sent: **{report.delivered}**\n"
            f"Failed sends: **{report.failed}** • "
            f"No longer eligible: **{report.skipped}** • "
            f"On cooldown: **{report.blocked_by_manual_limits}**.\n"
            "Manual attempts use their own six-hour per-member cooldown; "
            "scheduled reminders and their configuration are unchanged."
        )

    @discord.ui.button(
        label="Next Eligible Group", emoji="➡️",
        style=discord.ButtonStyle.secondary, row=0,
    )
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        _ = button
        if (interaction.guild is None or interaction.guild.id != self.preview.guild_id
                or not await _require_staff(interaction)):
            return
        if self.used or not self.preview.next_after:
            return await _private(interaction, "No more groups in this preview.")
        self.used = True
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            next_preview = select_manual_group(self.preview)
        except ManualReminderUnavailable as exc:
            return await _private(interaction, f"❌ Cannot open next group: {exc}")
        await _replace(
            interaction, embed=_manual_preview_embed(interaction.guild, next_preview),
            view=ManualPreviewView(self.owner_id, next_preview),
        )

    @discord.ui.button(
        label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary, row=0,
    )
    async def cancel_manual(
        self, interaction: discord.Interaction, button: discord.ui.Button,
    ) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        self.used = True
        await _private(interaction, "Cancelled. No manual reminders were authorized.")

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

    @discord.ui.button(
        label="Check Scheduled Due", emoji="🔎",
        style=discord.ButtonStyle.primary, row=2,
    )
    async def send_due(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None or not await _require_staff(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        cfg, policy = await _load(interaction.guild)
        if not policy.enabled:
            return await _private(interaction, "❌ Enable reminders before sending due notifications.")
        if policy.delivery != "channel":
            return await _private(
                interaction, "❌ Choose channel delivery and save the designated "
                             "reminder channel first. This button never sends DMs."
            )
        channel = delivery_channel(interaction.guild, policy)
        if channel is None:
            return await _private(interaction, "❌ Choose a valid designated reminder channel first.")
        if _can_enable(interaction.guild, cfg):
            return await _private(
                interaction, "❌ Reminder setup is incomplete: " + _can_enable(interaction.guild, cfg)
            )
        await _private(
            interaction,
            f"**Check scheduled reminders now in {channel.mention}?**\n\n"
            f"• First reminder only after **{policy.delay_hours} hours** in the server.\n"
            f"• Repeat no sooner than **{policy.repeat_hours} hours**.\n"
            f"• Up to **{policy.max_reminders}** attempts per member per join.\n"
            "• Only members still awaiting verification and able to see both channels.\n"
            "• At most **3 attempts** per tap, from one page of up to 100 members.\n\n"
            "This uses the normal database duplicate protection and existing worker. "
            "**It doesn't restart anyone's schedule or send reminders early.**",
            view=ConfirmDueReminderView(
                self.owner_id, interaction.guild.id, channel.id,
            ),
        )


    @discord.ui.button(
        label="Send Reminder Now", emoji="📣",
        style=discord.ButtonStyle.primary, row=2,
    )
    async def send_manual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None or not await _require_staff(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cfg, policy = await _load(interaction.guild)
        except Exception:
            return await _private(interaction, "❌ Could not read saved reminder settings.")
        if policy.delivery == "channel" and not policy.reminder_channel_id:
            return await _private(
                interaction, "❌ Save a reminder channel first, or select Use DMs."
            )
        destination_id = policy.reminder_channel_id if policy.delivery == "channel" else 0
        try:
            preview = await preview_manual_reminders(
                interaction.client, interaction.guild, channel_id=destination_id,
            )
        except ManualReminderUnavailable as exc:
            return await _private(interaction, f"❌ Manual preview unavailable: {exc}")
        await _private(
            interaction,
            embed=_manual_preview_embed(interaction.guild, preview),
            view=ManualPreviewView(self.owner_id, preview),
        )

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
