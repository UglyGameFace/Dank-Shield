from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc

from ..tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
)

from .common import _staff_check

from .kick_timers import (
    _cancel_kick_timer,
    kick_timer_persist_delete,
)

try:
    from ..tickets_new.panel import (
        send_ticket_panel,
        send_staff_ghost_ticket_panel,
    )
except Exception:
    send_ticket_panel = None  # type: ignore
    send_staff_ghost_ticket_panel = None  # type: ignore

try:
    from ..events_new.members import (
        run_full_member_sync_for_guild,
        run_departed_reconciliation_for_guild,
    )
except Exception:
    async def run_full_member_sync_for_guild(guild: discord.Guild):  # type: ignore
        return {"processed": 0, "failed": 0, "total_seen": 0}

    async def run_departed_reconciliation_for_guild(guild: discord.Guild):  # type: ignore
        return {"checked": 0, "marked_departed": 0}

try:
    from ..transcripts import send_tickettool_style_transcript
except Exception:
    async def send_tickettool_style_transcript(*args, **kwargs) -> None:  # type: ignore
        return None


def register_ticket_admin_commands(bot, tree) -> None:
    # ============================================================
    # /post_ticket_panel
    # ============================================================
    @tree.command(
        name="post_ticket_panel",
        description="Post the public ticket panel in this channel.",
    )
    async def post_ticket_panel(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ Must be used in a text channel.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        try:
            if send_ticket_panel is None:
                return await interaction.followup.send(
                    "❌ Ticket panel sender is unavailable.",
                    ephemeral=True,
                )

            await send_ticket_panel(channel)
            await interaction.followup.send(
                f"✅ Public ticket panel posted in {channel.mention}.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to post ticket panel: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /post_ghost_ticket_panel
    # ============================================================
    @tree.command(
        name="post_ghost_ticket_panel",
        description="Post the staff-only ghost ticket panel in this channel.",
    )
    async def post_ghost_ticket_panel(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ Must be used in a text channel.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        try:
            if send_staff_ghost_ticket_panel is None:
                return await interaction.followup.send(
                    "❌ Ghost ticket panel sender is unavailable.",
                    ephemeral=True,
                )

            await send_staff_ghost_ticket_panel(channel)
            await interaction.followup.send(
                f"✅ Staff-only ghost ticket panel posted in {channel.mention}.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to post ghost ticket panel: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /sync_members_now
    # ============================================================
    @tree.command(
        name="sync_members_now",
        description="Run a full member sync for this guild.",
    )
    async def sync_members_now(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This command must be run in the server.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        try:
            summary = await run_full_member_sync_for_guild(guild)
            await interaction.followup.send(
                "✅ Member sync complete. "
                f"Processed: {summary.get('processed', 0)} | "
                f"Failed: {summary.get('failed', 0)} | "
                f"Seen: {summary.get('total_seen', 0)}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Member sync failed: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /reconcile_departed_members
    # ============================================================
    @tree.command(
        name="reconcile_departed_members",
        description="Mark missing users as departed in the dashboard database.",
    )
    async def reconcile_departed_members(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "❌ This command must be run in the server.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        try:
            summary = await run_departed_reconciliation_for_guild(guild)
            await interaction.followup.send(
                "✅ Departed reconciliation complete. "
                f"Checked: {summary.get('checked', 0)} | "
                f"Marked departed: {summary.get('marked_departed', 0)}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Departed reconciliation failed: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /close_ticket
    # ============================================================
    @tree.command(
        name="close_ticket",
        description="(Staff) Post transcript then close/delete a verification ticket.",
    )
    @app_commands.describe(
        channel="Ticket channel to close (leave empty to use the current channel)",
        reason="Optional reason to store in transcript decision field",
    )
    async def close_ticket_slash(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        guild = interaction.guild
        if not guild:
            try:
                return await interaction.followup.send(
                    "❌ Invalid context (no guild).",
                    ephemeral=True,
                )
            except Exception:
                return

        ch = channel or interaction.channel
        if not isinstance(ch, discord.TextChannel):
            try:
                return await interaction.followup.send(
                    "❌ Invalid channel (must be a text channel).",
                    ephemeral=True,
                )
            except Exception:
                return

        try:
            print(
                f"🧨 close_ticket invoked by user={interaction.user.id} "
                f"in guild={guild.id} target_channel={ch.id} name='{ch.name}'"
            )
        except Exception:
            pass

        if not is_verification_ticket_channel(ch):
            try:
                return await interaction.followup.send(
                    f"❌ That channel isn’t a verification ticket.\n"
                    f"Target: {ch.mention} (`{ch.id}`)",
                    ephemeral=True,
                )
            except Exception:
                return

        me = guild.me
        if not me and bot.user:
            try:
                me = guild.get_member(bot.user.id) or await guild.fetch_member(bot.user.id)
            except Exception:
                me = None

        if not me:
            try:
                return await interaction.followup.send(
                    "❌ Bot member missing in guild cache.",
                    ephemeral=True,
                )
            except Exception:
                return

        perms = ch.permissions_for(me)
        if not perms.view_channel:
            try:
                return await interaction.followup.send(
                    "❌ I can’t even view that channel (permission issue).",
                    ephemeral=True,
                )
            except Exception:
                return

        if not perms.manage_channels:
            try:
                await interaction.followup.send(
                    "❌ I **cannot delete** this ticket because I’m missing **Manage Channels** "
                    "in that category/channel.\n"
                    "Fix: give my role **Manage Channels** (or Admin) for the ticket category.",
                    ephemeral=True,
                )
            except Exception:
                pass

            try:
                print(
                    f"⚠️ close_ticket blocked: missing manage_channels in "
                    f"channel={ch.id} category={getattr(ch.category, 'id', None)}"
                )
            except Exception:
                pass
            return

        owner = await find_ticket_owner_retry(ch)
        decision = (reason.strip() if reason else "STAFF CLOSED")

        # Stop any running no-response timer before closing
        try:
            _cancel_kick_timer(ch.id)
        except Exception:
            pass

        try:
            await kick_timer_persist_delete(int(ch.id))
        except Exception:
            pass

        try:
            await send_tickettool_style_transcript(
                ch,
                owner,
                closed_by=guild.get_member(int(interaction.user.id)),
                decision=decision,
            )
        except Exception as e:
            try:
                print("⚠️ close_ticket transcript failed:", repr(e))
            except Exception:
                pass

        try:
            await interaction.followup.send("✅ Closing ticket now…", ephemeral=True)
        except discord.NotFound:
            pass
        except Exception:
            pass

        try:
            await ch.delete(reason=f"Closed by staff: {decision}")
            try:
                RUNTIME_STATS["tickets_closed"] += 1
            except Exception:
                pass

            try:
                print(f"✅ close_ticket deleted channel={ch.id}")
            except Exception:
                pass
            return

        except discord.Forbidden as e:
            try:
                print("❌ close_ticket Forbidden:", repr(e))
            except Exception:
                pass

            try:
                await interaction.followup.send(
                    "⚠️ I tried to delete it but got **Forbidden**. "
                    "Check category overrides + role position.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        except discord.NotFound:
            try:
                print(f"ℹ️ close_ticket channel already gone (NotFound) channel={ch.id}")
            except Exception:
                pass
            return

        except Exception as e:
            try:
                print("❌ close_ticket unexpected error:", repr(e))
            except Exception:
                pass

            try:
                await interaction.followup.send(
                    f"⚠️ Failed to delete ticket: `{e}`",
                    ephemeral=True,
                )
            except Exception:
                pass
            return