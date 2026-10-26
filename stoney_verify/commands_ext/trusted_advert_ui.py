from __future__ import annotations

"""Staff-owned trusted advertising bot setup in the canonical Invite Shield UI."""

import re
from typing import Any

import discord

from ..invite_trusted_advertisers import clean_rules, load_rules, save_rule
from ..ui import DankGuildResourceBrowserView


async def _notice(interaction: discord.Interaction, message: str) -> None:
    allowed = discord.AllowedMentions.none()
    if interaction.response.is_done():
        await interaction.followup.send(message[:1900], ephemeral=True, allowed_mentions=allowed)
    else:
        await interaction.response.send_message(message[:1900], ephemeral=True, allowed_mentions=allowed)


def _staff(interaction: discord.Interaction, owner_id: int) -> bool:
    guild = getattr(interaction, "guild", None)
    user = getattr(interaction, "user", None)
    perms = getattr(user, "guild_permissions", None)
    return bool(
        guild is not None and user is not None
        and int(user.id) == owner_id
        and (getattr(perms, "manage_guild", False) or getattr(perms, "administrator", False))
    )


async def _resolve_bot(guild: discord.Guild, raw: str) -> discord.Member | None:
    candidate = re.fullmatch(r"(?:<@!?)?([0-9]{15,20})>?", str(raw or "").strip())
    if not candidate:
        return None
    bid = int(candidate.group(1))
    member = guild.get_member(bid)
    if member is None:
        try:
            member = await guild.fetch_member(bid)
        except discord.HTTPException:
            return None
    if not isinstance(member, discord.Member) or not member.bot or bid == guild.me.id:
        return None
    return member


def _embed(guild: discord.Guild, channel_id: int, rules: list[dict[str, Any]]) -> discord.Embed:
    channel = guild.get_channel(channel_id)
    mention = channel.mention if isinstance(channel, discord.TextChannel) else "Choose a text channel"
    embed = discord.Embed(
        title="🤝 Trusted Advertising Bots",
        description=(
            "Invite Shield stays **ON everywhere**. Allow only an exact trusted bot "
            "to post advertisements in one approved channel. Members and bots elsewhere "
            "are still checked. Start by selecting a channel, then approve the bot's Discord ID."
        ), color=discord.Color.blurple(),
    )
    embed.add_field(name="Selected ad channel", value=mention, inline=False)
    if isinstance(channel, discord.TextChannel):
        me = getattr(guild, "me", None)
        if me is not None:
            perms = channel.permissions_for(me)
            needed = [
                label for flag, label in (
                    ("view_channel", "View Channel"),
                    ("read_message_history", "Read Message History"),
                    ("manage_messages", "Manage Messages"),
                )
                if not getattr(perms, flag, False)
            ]
            embed.add_field(
                name="Cleanup permissions",
                value=("✅ Ready" if not needed else "⚠️ Missing: " + ", ".join(needed)),
                inline=False,
            )
        default_role = getattr(guild, "default_role", None)
        if default_role is not None:
            public_view = bool(getattr(channel.permissions_for(default_role), "view_channel", False))
            embed.add_field(
                name="OneBump visibility",
                value=("✅ Members can view the advertising channel." if public_view
                       else "⚠️ The default server role cannot view this channel. OneBump fair-play rules require visible adverts."),
                inline=False,
            )
    lines = []
    for rule in rules[:10]:
        lines.append(
            f"Bot `{rule['bot_id']}` • <#{rule['channel_id']}> • "
            + ("Previews hidden • " if rule.get("suppress_embeds") else "Previews visible • ")
            + (f"Cleanup after {rule['retention_hours']}h + 10m" if rule["cleanup_enabled"] else "No automatic deletion")
        )
    embed.add_field(
        name=f"Approved bot/channel pairs ({len(rules)}/10)",
        value="\n".join(lines)[:1024] or "No trusted advertising bots configured.",
        inline=False,
    )
    embed.add_field(
        name="Collapse ad previews (optional)",
        value=(
            "Hide the rich embed card of an approved advertising bot in the selected channel. "
            "The original post stays; **its large preview and Join card may disappear**. "
            "This is NOT selective text blur, and OneBump may consider a collapsed advert "
            "less visible under its fair-play policy. Default: OFF. "
            "Other bots, members and channels are unchanged."
        ),
        inline=False,
    )
    embed.add_field(
        name="OneBump and 24-hour cleanup",
        value=(
            "OneBump says adverts must stay visible **at least 24 hours** and warns "
            "against bulk/purge deletion. The optional cleanup waits **24 hours + 10 minutes**, "
            "is OFF by default and might still affect OneBump fair-play standing. "
            "Never enable it unless you accept that risk. More info: bump.ltd/guides/fair-play"
        ),
        inline=False,
    )
    embed.add_field(
        name="Before setting up",
        value="Copy the real advertising bot's ID in Discord (Developer Mode → Copy User ID). "
              "No username-based trust, no channel-wide whitelist.",
        inline=False,
    )
    return embed


class ApproveAdvertBotModal(discord.ui.Modal, title="Approve an Advertising Bot"):
    bot_id = discord.ui.TextInput(
        label="Bot user ID", placeholder="Paste the real bot's Discord user ID",
        min_length=15, max_length=25,
    )
    confirm = discord.ui.TextInput(
        label="Type TRUST to confirm",
        placeholder="TRUST", max_length=5,
    )
    def __init__(self, owner_id: int, *, channel_id: int):
        super().__init__()
        self.owner_id = owner_id
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _staff(interaction, self.owner_id):
            return await _notice(interaction, "Only the server manager who opened this setup may approve bots.")
        if str(self.confirm.value).strip().upper() != "TRUST":
            return await _notice(interaction, "Nothing saved. Type TRUST to approve the exact bot account.")
        guild = interaction.guild
        channel = guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await _notice(interaction, "Choose an existing text channel first.")
        await interaction.response.defer(ephemeral=True)
        member = await _resolve_bot(guild, self.bot_id.value)
        if member is None:
            return await _notice(interaction, "Bot not verified in this server. Copy its Discord User ID; names aren't sufficient.")
        # Refuse misleading setup if an older broad channel exemption would
        # also let *ordinary members* post invitations here.
        from ..invite_policy_engine import load_invite_policy
        from ..invite_scope_settings import parse_ids
        _cfg, settings = await load_invite_policy(guild, refresh=True)
        allowed = settings.get("allowed_invite_channel_ids", settings.get("invite_allowed_channel_ids", []))
        if str(channel.id) in parse_ids(allowed):
            return await _notice(
                interaction,
                "⚠️ This channel is already on Invite Shield's allowed-channel list, "
                "which lets ordinary members post invites here. Remove that broad "
                "channel exemption under Spam Guard / Invite Allow Lists first. "
                "Nothing was changed.",
            )
        try:
            rules = await save_rule(guild.id, bot_id=member.id, channel_id=channel.id)
        except (RuntimeError, ValueError) as exc:
            return await _notice(interaction, f"Could not save: {str(exc)[:250]}")
        await _notice(interaction, f"✅ Trusted **{member.display_name}** (`{member.id}`) only in {channel.mention}. "
                      "Invite Shield remains enabled for everyone else. Cleanup is OFF. "
                      "Reopen Trusted Advertising Bots to opt into 24-hour cleanup if desired.")


class ConfirmAdvertCleanupModal(discord.ui.Modal, title="Optional Advertisement Cleanup"):
    bot_id = discord.ui.TextInput(label="Trusted bot ID", placeholder="Paste an approved bot ID", min_length=15, max_length=25)
    hours = discord.ui.TextInput(label="Hours (24, 48, or 72)", default="24", min_length=2, max_length=2)
    consent = discord.ui.TextInput(
        label="Type DELETE to accept fair-play risk",
        placeholder="DELETE", min_length=6, max_length=6,
    )

    def __init__(self, owner_id: int, *, channel_id: int):
        super().__init__()
        self.owner_id = owner_id
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _staff(interaction, self.owner_id):
            return await _notice(interaction, "You cannot change this server's advertising cleanup.")
        if str(self.consent.value).strip().upper() != "DELETE":
            return await _notice(interaction, "No change. Explicit DELETE confirmation required.")
        try:
            hours = int(self.hours.value)
        except (TypeError, ValueError):
            return await _notice(interaction, "Choose 24, 48 or 72 hours.")
        if hours not in (24, 48, 72):
            return await _notice(interaction, "Choose 24, 48 or 72 hours.")
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = await _resolve_bot(guild, self.bot_id.value)
        if member is None:
            return await _notice(interaction, "That is not a verified bot in this server.")
        current = await load_rules(guild.id, refresh=True)
        existing = next((r for r in current
                         if r["bot_id"] == member.id and r["channel_id"] == self.channel_id), None)
        if existing is None:
            return await _notice(interaction, "First approve this exact bot and channel. Cleanup wasn't enabled.")
        try:
            await save_rule(guild.id, bot_id=member.id, channel_id=self.channel_id,
                            cleanup_enabled=True, retention_hours=hours)
        except (RuntimeError, ValueError) as exc:
            return await _notice(interaction, f"No change: {str(exc)[:240]}")
        await _notice(interaction, f"✅ Optional cleanup enabled for bot `{member.id}` in <#{self.channel_id}> "
                      f"after each NEW advert is {hours} hours + 10 minutes old. "
                      "OneBump may penalize automated deletion; consider leaving cleanup off.")



class TrustedPreviewModal(discord.ui.Modal, title="Collapse Trusted Ad Previews"):
    bot_id = discord.ui.TextInput(
        label="Approved bot User ID",
        placeholder="Exact ID of the bot already approved here",
        min_length=15, max_length=25,
    )
    action = discord.ui.TextInput(
        label="ON+RECENT, ON, OFF or RECENT",
        placeholder="ON+RECENT",
        default="ON+RECENT",
        max_length=10,
    )
    consent = discord.ui.TextInput(
        label="Type HIDE to confirm preview changes",
        placeholder="HIDE",
        min_length=4, max_length=4,
    )

    def __init__(self, owner_id: int, *, channel_id: int, bot_id: int = 0) -> None:
        super().__init__()
        self.owner_id = int(owner_id)
        self.channel_id = int(channel_id)
        if bot_id > 0:
            self.bot_id.default = str(bot_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _staff(interaction, self.owner_id):
            return await _notice(interaction, "Manage Server permission required.")
        action = str(self.action.value or "").strip().upper()
        if action not in {"ON+RECENT", "ON", "OFF", "RECENT"}:
            return await _notice(interaction, "Choose ON+RECENT, ON, OFF or RECENT.")
        if str(self.consent.value or "").strip().upper() != "HIDE":
            return await _notice(interaction, "No change. Type HIDE to confirm.")
        match = re.fullmatch(r"(?:<@!?)?([0-9]{15,20})>?", str(self.bot_id.value or "").strip())
        if match is None:
            return await _notice(interaction, "Paste the exact approved bot User ID.")
        bot_id = int(match.group(1))
        guild = interaction.guild
        channel = guild.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await _notice(interaction, "Choose a real server text channel first.")
        await interaction.response.defer(ephemeral=True)
        try:
            rules = await load_rules(guild.id, refresh=True)
        except RuntimeError:
            return await _notice(interaction, "Could not load current trusted bots; nothing changed.")
        old = next((x for x in rules if x["channel_id"] == self.channel_id and x["bot_id"] == bot_id), None)
        if old is None:
            return await _notice(interaction, "Approve this exact bot in this channel first. No changes made.")

        if action != "RECENT":
            try:
                await save_rule(
                    guild.id, bot_id=bot_id, channel_id=self.channel_id,
                    cleanup_enabled=old["cleanup_enabled"],
                    retention_hours=old["retention_hours"],
                    suppress_embeds=action != "OFF",
                )
            except (RuntimeError, ValueError) as exc:
                return await _notice(interaction, f"No changes saved: {str(exc)[:240]}")

        if action == "OFF":
            return await _notice(
                interaction,
                "✅ Future adverts will display normally. Existing suppressed previews "
                "are unchanged. Your bot trust and scheduled cleanup settings were preserved.",
            )
        message = "✅ Automatic preview collapse enabled for this exact bot/channel pair." if action != "RECENT" else "✅ Checking recent previews."
        if action in {"RECENT", "ON+RECENT"}:
            from ..trusted_advert_embeds import collapse_recent_trusted_adverts
            try:
                counts = await collapse_recent_trusted_adverts(guild, channel, bot_id)
            except (PermissionError, ValueError) as exc:
                return await _notice(interaction, message + " Recent posts were not changed: " + str(exc)[:190])
            except (discord.Forbidden, discord.HTTPException) as exc:
                return await _notice(interaction, message + " Discord denied the recent-history operation.")
            message += (
                f" Checked {counts['scanned']} recent messages and collapsed "
                f"{counts['collapsed']} approved previews (maximum 3 per request)."
            )
        message += " The original OneBump posts remain; large previews and Join cards may be hidden."
        await _notice(interaction, message)


class TrustedAdsView(discord.ui.View):
    def __init__(self, owner_id: int, guild: discord.Guild, channel_id: int):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.guild = guild
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _staff(interaction, self.owner_id):
            return True
        await _notice(interaction, "Open your own Invite Shield settings with Manage Server permission.")
        return False

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.primary, row=0)
    async def use_this_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = self.guild.get_channel(int(interaction.channel_id or 0))
        if not isinstance(channel, discord.TextChannel):
            return await _notice(interaction, "Open this setup from the advertising text channel, or choose another.")
        await show_trusted_ads(interaction, self.owner_id, channel_id=channel.id)

    @discord.ui.button(label="Choose Channel", emoji="🧭", style=discord.ButtonStyle.secondary, row=0)
    async def choose_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        async def pick(pick_interaction: discord.Interaction, channel: Any) -> None:
            await show_trusted_ads(pick_interaction, self.owner_id, channel_id=int(channel.id))
        async def back(back_interaction: discord.Interaction) -> None:
            await show_trusted_ads(back_interaction, self.owner_id, channel_id=self.channel_id)
        browser = DankGuildResourceBrowserView(
            guild=self.guild, author_id=self.owner_id, resource_kinds=("text",),
            on_pick=pick, on_home=back,
            custom_id="dank_protection:trusted_advert_channel",
            title="Choose Advertisement Channel",
            placeholder="Search channels by name, ID or emoji…",
            home_label="Back to Trusted Bots",
        )
        await interaction.response.edit_message(embed=browser.embed(), view=browser)

    @discord.ui.button(label="Find Bot", emoji="🔎", style=discord.ButtonStyle.secondary, row=0)
    async def find_bot(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        # Convenience only: usernames are NOT security identity. Staff must
        # approve the actual ID separately and each candidate is a real bot.
        candidates = [
            member for member in list(getattr(self.guild, "members", []) or [])
            if bool(getattr(member, "bot", False))
            and "onebump" in (str(getattr(member, "name", "")) + " " +
                               str(getattr(member, "display_name", ""))).casefold()
        ][:5]
        if candidates:
            rows = [f"• {m.display_name}: `{m.id}` (verify this exact ID)" for m in candidates]
            return await _notice(interaction, "Possible OneBump bot accounts in this server:\n"
                                  + "\\n".join(rows) +
                                  "\\nMatching a name is only a suggestion, never automatic trust.")
        await _notice(interaction, "No likely OneBump bot was found in the cached server members. "
                      "Open the real bot's profile in Discord → Copy User ID, then choose Approve Bot.")

    @discord.ui.button(label="Approve Bot", emoji="🤖", style=discord.ButtonStyle.success, row=1)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not isinstance(self.guild.get_channel(self.channel_id), discord.TextChannel):
            return await _notice(interaction, "Choose a text channel first.")
        await interaction.response.send_modal(ApproveAdvertBotModal(self.owner_id, channel_id=self.channel_id))

    @discord.ui.button(label="Enable Cleanup", emoji="🕒", style=discord.ButtonStyle.secondary, row=1)
    async def cleanup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(ConfirmAdvertCleanupModal(self.owner_id, channel_id=self.channel_id))

    @discord.ui.button(label="Collapse Previews", emoji="🫥", style=discord.ButtonStyle.secondary, row=1)
    async def collapse_previews(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not isinstance(self.guild.get_channel(self.channel_id), discord.TextChannel):
            return await _notice(interaction, "Choose the advertising channel first.")
        try:
            rules = await load_rules(self.guild.id)
        except RuntimeError:
            return await _notice(interaction, "Trusted bot settings are unavailable.")
        selected = [x for x in rules if x["channel_id"] == self.channel_id]
        if not selected:
            return await _notice(interaction, "Approve a real advertising bot in this channel first.")
        modal = TrustedPreviewModal(
            self.owner_id, channel_id=self.channel_id,
            bot_id=selected[0]["bot_id"] if len(selected) == 1 else 0,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Manage / Remove", emoji="🗑️", style=discord.ButtonStyle.secondary, row=1)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(RemoveAdvertRuleModal(self.owner_id, channel_id=self.channel_id))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await show_trusted_ads(interaction, self.owner_id, channel_id=self.channel_id)

    @discord.ui.button(label="Cleanup Status", emoji="📋", style=discord.ButtonStyle.secondary, row=2)
    async def cleanup_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not isinstance(self.guild.get_channel(self.channel_id), discord.TextChannel):
            return await _notice(interaction, "Choose an approved channel to check its cleanup.")
        await interaction.response.defer(ephemeral=True)
        from ..trusted_advert_cleanup import recent_cleanup_status
        try:
            result = await recent_cleanup_status(self.guild.id, self.channel_id)
        except Exception as exc:
            return await _notice(interaction, "Cleanup history unavailable. "
                                 "Make sure the private retention migration is installed.")
        counts = result["counts"]
        await _notice(
            interaction,
            f"🧹 **Recent advert cleanup in <#{self.channel_id}>**\n"
            f"Tracked (latest 50): {result['total_shown']}\n"
            f"Scheduled: {counts['pending']} • Processing: {counts['claimed']}\n"
            f"Deleted/already gone: {counts['done']} • Cancelled: {counts['cancelled']} • Failed: {counts['failed']}\n"
            f"Next scheduled deadline: {result['next_due']}\n"
            "Only approved bot adverts are eligible, and every delete is rechecked.",
        )

    @discord.ui.button(label="Back to Invite Shield", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_protection_invite_ui import _redraw
        from ..invite_scope_settings import load_invite_scope_settings
        scope = await load_invite_scope_settings(self.guild.id, refresh=True)
        await _redraw(interaction, author_id=self.owner_id, scope=scope, origin_channel_id=self.channel_id)


class RemoveAdvertRuleModal(discord.ui.Modal, title="Remove or Pause Trusted Bot"):
    bot_id = discord.ui.TextInput(label="Approved bot user ID", min_length=15, max_length=25)
    action = discord.ui.TextInput(
        label="Type REMOVE or PAUSE",
        placeholder="REMOVE = revoke trust, PAUSE = stop cleanup",
        min_length=5, max_length=6,
    )

    def __init__(self, owner_id: int, *, channel_id: int):
        super().__init__()
        self.owner_id = owner_id
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _staff(interaction, self.owner_id):
            return await _notice(interaction, "Manage Server permission required.")
        await interaction.response.defer(ephemeral=True)
        # Revocation must remain possible even after a bot leaves this guild.
        # The exact trusted ID is already known; do NOT require fetch_member.
        match = re.fullmatch(r"(?:<@!?)?([0-9]{15,20})>?", str(self.bot_id.value or "").strip())
        if match is None:
            return await _notice(interaction, "Enter the approved bot's Discord User ID.")
        bot_id = int(match.group(1))
        rules = await load_rules(interaction.guild.id, refresh=True)
        current = next((v for v in rules if v["bot_id"] == bot_id and v["channel_id"] == self.channel_id), None)
        if current is None:
            return await _notice(interaction, "That exact bot isn't approved in this channel.")
        action = str(self.action.value).strip().upper()
        if action not in {"REMOVE", "PAUSE"}:
            return await _notice(interaction, "Type REMOVE to revoke, or PAUSE to stop expiry cleanup.")
        try:
            await save_rule(interaction.guild.id, bot_id=bot_id, channel_id=self.channel_id,
                            remove=action == "REMOVE",
                            cleanup_enabled=False, retention_hours=current["retention_hours"])
        except (RuntimeError, ValueError) as exc:
            return await _notice(interaction, f"No change: {str(exc)[:240]}")
        try:
            from ..trusted_advert_cleanup import cancel_scheduled_for_pair
            await cancel_scheduled_for_pair(interaction.guild.id, self.channel_id, bot_id)
            stale_note = ""
        except Exception:
            # The updated guild rule remains authoritative; the due worker
            # checks it again rather than trusting the old ledger.
            stale_note = " Cleanup records could not be cancelled immediately, but deletion is disabled by current policy."
        await _notice(interaction, (
            "✅ Bot trust revoked; Invite Shield protection restored."
            if action == "REMOVE" else
            "✅ Automatic deletion paused; approved bot messages stay allowed."
        ) + stale_note)


async def show_trusted_ads(interaction: discord.Interaction, owner_id: int, *, channel_id: int = 0) -> None:
    guild = interaction.guild
    if guild is None or not _staff(interaction, owner_id):
        return await _notice(interaction, "Manage Server permission required.")
    # Acknowledge before any slow configuration fetch to avoid Discord's
    # three-second interaction expiry on mobile or under Discloud latency.
    if not interaction.response.is_done():
        await interaction.response.defer()
    try:
        rules = await load_rules(guild.id, refresh=True)
    except RuntimeError:
        return await _notice(interaction, "Guild settings unavailable. Cannot safely configure trusted bots.")
    view = TrustedAdsView(owner_id, guild, channel_id)
    embed = _embed(guild, channel_id, rules)
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view, content=None)
    else:
        await interaction.response.edit_message(embed=embed, view=view, content=None)
