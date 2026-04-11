# stoney_verify/transcripts.py
from __future__ import annotations

from .globals import *  # noqa

import asyncio
import re
import io
import discord

from typing import Tuple, Dict, List, Optional, Any, Union


# ============================================================
# Compatibility / safety (prevents NameError across versions)
# ============================================================

try:
    TRANSCRIPT_PANEL_NAME  # type: ignore[name-defined]
except Exception:
    TRANSCRIPT_PANEL_NAME = "Verification Transcript"

try:
    VERIFY_EMBED_COLOR  # type: ignore[name-defined]
except Exception:
    VERIFY_EMBED_COLOR = discord.Color.dark_green()

try:
    VERIFY_EMBED_THUMBNAIL_URL  # type: ignore[name-defined]
except Exception:
    VERIFY_EMBED_THUMBNAIL_URL = ""

try:
    RUNTIME_STATS  # type: ignore[name-defined]
except Exception:
    RUNTIME_STATS = {}

try:
    AUTO_DELETE_TICKET_SECONDS  # type: ignore[name-defined]
except Exception:
    AUTO_DELETE_TICKET_SECONDS = 0


# ============================================================
# VC channel id compatibility
# ============================================================

def _vc_channel_id() -> int:
    """
    Prefer VC_VERIFY_CHANNEL_ID (current standard),
    but also support older VC_VERIFY_VC_ID.
    """
    try:
        v = int(globals().get("VC_VERIFY_CHANNEL_ID", 0) or 0)
        if v > 0:
            return v
    except Exception:
        pass
    try:
        v2 = int(globals().get("VC_VERIFY_VC_ID", 0) or 0)
        if v2 > 0:
            return v2
    except Exception:
        pass
    return 0


from .tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
    wait_for_channel_ready,
)

try:
    from .tickets import _overwrite_target_id  # type: ignore
except Exception:
    def _overwrite_target_id(target: Any) -> int:
        try:
            return int(getattr(target, "id", 0) or 0)
        except Exception:
            return 0


from .verify_ui import post_or_replace_verify_ui


def _get_ticket_service_fns():
    try:
        from .tickets_new.service import (
            mark_ticket_closed,
            reopen_ticket_channel as service_reopen_ticket_channel,
            mark_ticket_deleted,
            attach_transcript_to_ticket,
        )
        return {
            "mark_ticket_closed": mark_ticket_closed,
            "service_reopen_ticket_channel": service_reopen_ticket_channel,
            "mark_ticket_deleted": mark_ticket_deleted,
            "attach_transcript_to_ticket": attach_transcript_to_ticket,
        }
    except Exception as e:
        print("⚠️ Failed importing ticket service helpers from transcripts.py:", repr(e))
        return {
            "mark_ticket_closed": None,
            "service_reopen_ticket_channel": None,
            "mark_ticket_deleted": None,
            "attach_transcript_to_ticket": None,
        }


# ============================================================
# Internal markers / guards
# ============================================================

_CLOSE_PROMPT_MARKER = "stoney_verify:close_prompt:v2"
_STAFF_CLOSED_MARKER = "stoney_verify:staff_closed:v2"
_TRANSCRIPT_MARKER_PREFIX = "stoney_verify:transcript_for:"
_CLOSE_REOPENED_MARKER = "stoney_verify:ticket_reopened:v1"
_STAFF_REVIEW_PANEL_MARKER = "stoney_verify:staff_review_panel:v1"

_TRANSCRIPT_POST_LOCKS: Dict[int, asyncio.Lock] = {}
_CLOSE_PROMPT_LOCKS: Dict[int, asyncio.Lock] = {}
_STAFF_REVIEW_PANEL_LOCKS: Dict[int, asyncio.Lock] = {}


def _lock_for(container: Dict[int, asyncio.Lock], channel_id: int) -> asyncio.Lock:
    cid = int(channel_id)
    lock = container.get(cid)
    if lock is None:
        lock = asyncio.Lock()
        container[cid] = lock
    return lock


# ============================================================
# Ticket close / reopen / transcript / delete helpers
# ============================================================

def _is_staff_member(member: Optional[discord.Member]) -> bool:
    try:
        return bool(member and is_staff(member))  # type: ignore[name-defined]
    except Exception:
        try:
            return bool(
                member and (
                    member.guild_permissions.manage_channels
                    or member.guild_permissions.manage_messages
                    or member.guild_permissions.administrator
                )
            )
        except Exception:
            return False


async def _reply_ephemeral(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        try:
            await interaction.followup.send(content, ephemeral=True)
        except Exception:
            pass


async def _resolve_ticket_owner(channel: discord.TextChannel) -> Optional[discord.Member]:
    try:
        return await find_ticket_owner_retry(channel)
    except Exception:
        return None


async def _user_can_close_ticket(interaction: discord.Interaction, channel: discord.TextChannel) -> bool:
    if not isinstance(interaction.user, discord.Member):
        return False

    if _is_staff_member(interaction.user):
        return True

    owner = await _resolve_ticket_owner(channel)
    if owner and int(owner.id) == int(interaction.user.id):
        return True

    return False


def _extract_ticket_number(name: str) -> str:
    m = re.search(r"(\d+)$", name or "")
    return m.group(1) if m else "0000"


def _closed_ticket_name(name: str) -> str:
    num = _extract_ticket_number(name)
    return f"closed-{num}"


def _open_ticket_name(name: str) -> str:
    num = _extract_ticket_number(name)
    return f"ticket-{num}"


async def _rename_channel_closed(channel: discord.TextChannel) -> None:
    try:
        new_name = _closed_ticket_name(channel.name)
        if channel.name != new_name:
            await channel.edit(name=new_name, reason="Ticket closed")
    except Exception as e:
        print(f"⚠️ Failed to rename ticket closed: {e}")


async def _rename_channel_open(channel: discord.TextChannel) -> None:
    try:
        new_name = _open_ticket_name(channel.name)
        if channel.name != new_name:
            await channel.edit(name=new_name, reason="Ticket reopened")
    except Exception as e:
        print(f"⚠️ Failed to rename ticket open: {e}")


async def _lock_ticket_for_owner(channel: discord.TextChannel, owner: Optional[discord.Member]) -> None:
    if not owner:
        return
    try:
        overwrite = channel.overwrites_for(owner)
        overwrite.view_channel = True
        overwrite.read_message_history = True
        overwrite.send_messages = False
        overwrite.attach_files = False
        overwrite.embed_links = False
        await channel.set_permissions(owner, overwrite=overwrite, reason="Ticket closed")
    except Exception as e:
        print(f"⚠️ Failed to lock ticket owner perms: {e}")


async def _unlock_ticket_for_owner(channel: discord.TextChannel, owner: Optional[discord.Member]) -> None:
    if not owner:
        return
    try:
        overwrite = channel.overwrites_for(owner)
        overwrite.view_channel = True
        overwrite.read_message_history = True
        overwrite.send_messages = True
        overwrite.attach_files = True
        overwrite.embed_links = True
        await channel.set_permissions(owner, overwrite=overwrite, reason="Ticket reopened")
    except Exception as e:
        print(f"⚠️ Failed to unlock ticket owner perms: {e}")


async def _has_staff_closed_message(channel: discord.TextChannel, limit: int = 50) -> bool:
    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
        async for msg in channel.history(limit=limit):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue

            try:
                embeds = getattr(msg, "embeds", None) or []
                for e in embeds:
                    footer_text = str(getattr(getattr(e, "footer", None), "text", "") or "")
                    if _STAFF_CLOSED_MARKER in footer_text:
                        return True
            except Exception:
                pass

            try:
                content = str(getattr(msg, "content", "") or "")
                if _STAFF_CLOSED_MARKER in content:
                    return True
            except Exception:
                pass

            try:
                comps = getattr(msg, "components", None) or []
                for row in comps:
                    for child in (getattr(row, "children", None) or []):
                        cid = str(getattr(child, "custom_id", "") or "")
                        if cid in ("sv:ticket:reopen", "sv:ticket:transcript", "sv:ticket:delete"):
                            return True
            except Exception:
                pass
    except Exception:
        pass
    return False


async def _post_staff_closed_message(channel: discord.TextChannel, closed_by: discord.abc.User) -> None:
    if await _has_staff_closed_message(channel):
        return

    embed = discord.Embed(
        title="🔒 Ticket Closed",
        description=(
            f"This ticket was closed by {closed_by.mention}.\n"
            "The ticket owner is now locked from replying until staff reopens it or deletes it."
        ),
        color=discord.Color.orange(),
        timestamp=now_utc(),  # type: ignore[name-defined]
    )
    embed.set_footer(text=_STAFF_CLOSED_MARKER)

    await channel.send(embed=embed, view=StaffClosedTicketView())


# ============================================================
# STAFF REVIEW PANEL FOR EXISTING VERIFICATION TICKET
# ============================================================

class VerificationStaffReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _ensure_staff(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not _is_staff_member(interaction.user):
            await _reply_ephemeral(interaction, "❌ Staff only.")
            return False
        return True

    async def _resolve_ticket_context(
        self,
        interaction: discord.Interaction,
    ) -> tuple[Optional[str], Optional[int], Optional[str]]:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return (None, None, None)

        ticket_id: Optional[str] = None
        member_id: Optional[int] = None
        member_name: Optional[str] = None

        try:
            svc = get_supabase()
            if svc:
                res = (
                    svc.table("tickets")
                    .select("*")
                    .or_(f"channel_id.eq.{channel.id},discord_thread_id.eq.{channel.id}")
                    .order("created_at", desc=False)
                    .limit(1)
                    .execute()
                )
                rows = getattr(res, "data", None) or []
                if rows:
                    row = rows[0]
                    ticket_id = str(row.get("id") or "").strip() or None
                    try:
                        member_id = int(str(row.get("user_id") or "0").strip() or 0) or None
                    except Exception:
                        member_id = None
                    member_name = str(row.get("username") or "").strip() or None
        except Exception:
            pass

        if member_id is None:
            try:
                owner = await _resolve_ticket_owner(channel)
                if owner:
                    member_id = int(owner.id)
                    member_name = member_name or str(owner)
            except Exception:
                pass

        return (ticket_id, member_id, member_name)

    async def _queue_worker_action(
        self,
        *,
        interaction: discord.Interaction,
        action: str,
        reason: str,
    ) -> bool:
        if not await self._ensure_staff(interaction):
            return False

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await _reply_ephemeral(interaction, "❌ Invalid channel.")
            return False

        ticket_id, member_id, member_name = await self._resolve_ticket_context(interaction)
        if member_id is None:
            await _reply_ephemeral(interaction, "❌ Could not resolve the ticket member.")
            return False

        try:
            guild_id = str(channel.guild.id)
            staff_id = str(interaction.user.id)
            staff_name = getattr(interaction.user, "display_name", None) or getattr(interaction.user, "name", None) or str(interaction.user)

            payload = {
                "ticket_id": ticket_id,
                "channel_id": str(channel.id),
                "user_id": str(member_id),
                "username": member_name or str(member_id),
                "staff_id": staff_id,
                "staff_name": staff_name,
                "reason": reason,
                "verification_source": "discord_staff_panel",
                "approval_reason": reason,
            }

            sb = get_supabase()
            if sb is None:
                await _reply_ephemeral(interaction, "❌ Database is unavailable.")
                return False

            sb.table("bot_commands").insert({
                "guild_id": guild_id,
                "action": action,
                "status": "pending",
                "requested_by": staff_id,
                "payload": payload,
                "created_at": now_utc().isoformat(),  # type: ignore[name-defined]
            }).execute()

            return True
        except Exception as e:
            print(f"⚠️ Failed queueing {action} from staff review panel:", repr(e))
            await _reply_ephemeral(interaction, f"❌ Failed to queue action: {e}")
            return False

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="sv:verify:staff:approve",
        row=0,
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        ok = await self._queue_worker_action(
            interaction=interaction,
            action="approve_verification",
            reason="Approved from Discord staff review panel.",
        )
        if ok:
            await _reply_ephemeral(interaction, "✅ Approval queued.")

    @discord.ui.button(
        label="Deny",
        style=discord.ButtonStyle.danger,
        emoji="❌",
        custom_id="sv:verify:staff:deny",
        row=0,
    )
    async def deny(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        ok = await self._queue_worker_action(
            interaction=interaction,
            action="deny_verification",
            reason="Denied from Discord staff review panel.",
        )
        if ok:
            await _reply_ephemeral(interaction, "❌ Denial queued.")

    @discord.ui.button(
        label="Remove Unverified",
        style=discord.ButtonStyle.secondary,
        emoji="🧹",
        custom_id="sv:verify:staff:remove_unverified",
        row=1,
    )
    async def remove_unverified(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        ok = await self._queue_worker_action(
            interaction=interaction,
            action="remove_unverified_role",
            reason="Removed unverified role from Discord staff review panel.",
        )
        if ok:
            await _reply_ephemeral(interaction, "🧹 Remove-unverified queued.")

    @discord.ui.button(
        label="Repost Member Verify UI",
        style=discord.ButtonStyle.secondary,
        emoji="🔁",
        custom_id="sv:verify:staff:repost_user_ui",
        row=1,
    )
    async def repost_user_ui(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_staff(interaction):
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        ok = await ensure_verify_ui_present(
            channel,
            reason=f"staff_panel_repost:{getattr(interaction.user, 'id', 'staff')}",
        )
        if ok:
            return await _reply_ephemeral(interaction, "✅ Member verify UI reposted.")
        return await _reply_ephemeral(interaction, "❌ Failed to repost member verify UI.")


async def post_or_replace_verification_staff_panel(
    channel: discord.TextChannel,
    *,
    member: Optional[discord.Member] = None,
    user_id: Optional[int] = None,
    username: str = "",
    submitted_from: str = "website_submission",
    reason: str = "",
) -> str:
    if not isinstance(channel, discord.TextChannel):
        return ""

    lock = _lock_for(_STAFF_REVIEW_PANEL_LOCKS, channel.id)
    async with lock:
        target_user_id = int(user_id or getattr(member, "id", 0) or 0)
        target_name = (
            username
            or (str(member) if member else "")
            or (f"User {target_user_id}" if target_user_id else "Unknown User")
        )
        target_mention = (
            member.mention if member is not None
            else (f"<@{target_user_id}>" if target_user_id else "Unknown")
        )

        embed = discord.Embed(
            title="🛡️ Verification Staff Review",
            description="A verification submission was received and is ready for staff review in this same ticket.",
            color=discord.Color.blurple(),
            timestamp=now_utc(),  # type: ignore[name-defined]
        )

        embed.add_field(
            name="Member",
            value=(
                f"{target_mention}\n"
                f"`{target_name}`"
                + (f" • `{target_user_id}`" if target_user_id else "")
            ),
            inline=False,
        )
        embed.add_field(
            name="Submission Source",
            value=f"`{submitted_from}`",
            inline=True,
        )
        embed.add_field(
            name="Review Actions",
            value=(
                "Approve, deny, or remove the unverified role "
                "from this same verification ticket flow."
            ),
            inline=False,
        )

        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)

        embed.set_footer(text=_STAFF_REVIEW_PANEL_MARKER)

        view = VerificationStaffReviewView()

        try:
            me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
            async for msg in channel.history(limit=80):
                if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                    continue
                if not msg.embeds:
                    continue

                try:
                    footer_text = str(
                        getattr(getattr(msg.embeds[0], "footer", None), "text", "") or ""
                    )
                    if _STAFF_REVIEW_PANEL_MARKER in footer_text:
                        await msg.edit(embed=embed, view=view)
                        return "updated"
                except Exception:
                    continue
        except Exception:
            pass

        try:
            await channel.send(embed=embed, view=view)
            return "posted"
        except Exception as e:
            print("⚠️ Failed posting verification staff review panel:", repr(e))
            return ""


# ============================================================
# STAFF ACTION VIEW (shown after close)
# ============================================================

class StaffClosedTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _ensure_staff(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not _is_staff_member(interaction.user):
            await _reply_ephemeral(interaction, "❌ Staff only.")
            return False
        return True

    @discord.ui.button(
        label="Reopen Ticket",
        style=discord.ButtonStyle.success,
        emoji="🔓",
        custom_id="sv:ticket:reopen",
        row=0,
    )
    async def reopen_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_staff(interaction):
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        owner = await _resolve_ticket_owner(channel)
        reopened = False
        svc = _get_ticket_service_fns()
        service_reopen_ticket_channel = svc["service_reopen_ticket_channel"]

        if service_reopen_ticket_channel:
            try:
                reopened = await service_reopen_ticket_channel(
                    channel=channel,
                    owner=owner,
                )
            except Exception as e:
                print("⚠️ service reopen_ticket_channel failed:", e)
                reopened = False

        if not reopened:
            await _rename_channel_open(channel)
            await _unlock_ticket_for_owner(channel, owner)
            reopened = True

        try:
            await channel.send(f"🔓 Ticket reopened by {interaction.user.mention}.\n{_CLOSE_REOPENED_MARKER}")
        except Exception:
            pass

        await _reply_ephemeral(interaction, "✅ Ticket reopened." if reopened else "⚠️ Ticket reopen partially completed.")

    @discord.ui.button(
        label="Post Transcript",
        style=discord.ButtonStyle.secondary,
        emoji="🧾",
        custom_id="sv:ticket:transcript",
        row=0,
    )
    async def post_transcript(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_staff(interaction):
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        owner = await _resolve_ticket_owner(channel)

        try:
            await send_tickettool_style_transcript(
                channel,
                owner,
                closed_by=interaction.user,
                decision="MANUAL TRANSCRIPT",
            )
            await _reply_ephemeral(interaction, "✅ Transcript posted.")
        except Exception as e:
            print("⚠️ Manual transcript failed:", e)
            await _reply_ephemeral(interaction, f"❌ Failed to post transcript: {e}")

    @discord.ui.button(
        label="Delete Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        custom_id="sv:ticket:delete",
        row=0,
    )
    async def delete_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if not await self._ensure_staff(interaction):
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        owner = await _resolve_ticket_owner(channel)

        try:
            await send_tickettool_style_transcript(
                channel,
                owner,
                closed_by=interaction.user,
                decision="DELETED BY STAFF",
            )
        except Exception as e:
            print("⚠️ Transcript routing failed before delete:", e)

        try:
            svc = _get_ticket_service_fns()
            mark_ticket_deleted = svc["mark_ticket_deleted"]
            if mark_ticket_deleted:
                await mark_ticket_deleted(
                    channel_id=channel.id,
                    deleted_by=interaction.user,
                    reason="Deleted by staff",
                )
        except Exception as e:
            print("⚠️ mark_ticket_deleted failed:", e)

        try:
            await _reply_ephemeral(interaction, "✅ Transcript posted. Deleting ticket now.")
        except Exception:
            pass

        try:
            await channel.delete(reason=f"Ticket deleted by staff: {interaction.user} ({interaction.user.id})")
            try:
                RUNTIME_STATS["tickets_closed"] = int(RUNTIME_STATS.get("tickets_closed", 0) or 0) + 1
            except Exception:
                pass
        except discord.Forbidden:
            try:
                await channel.send("⚠️ I could not delete this ticket (missing **Manage Channels**).")
            except Exception:
                pass
        except Exception as e:
            print("⚠️ Channel delete failed:", e)


# ============================================================
# OWNER / STAFF CLOSE CONFIRM VIEW
# ============================================================

class ConfirmCloseTicketView(discord.ui.View):
    """
    Must be timeout=None to register as a persistent view on startup.
    """
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Confirm Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="sv:ticket:confirm_close",
        row=0,
    )
    async def confirm_close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        if not await _user_can_close_ticket(interaction, channel):
            return await _reply_ephemeral(interaction, "❌ Only the ticket owner or staff can close this ticket.")

        owner = await _resolve_ticket_owner(channel)
        closed = False
        svc = _get_ticket_service_fns()
        mark_ticket_closed = svc["mark_ticket_closed"]

        if mark_ticket_closed:
            try:
                closed = await mark_ticket_closed(
                    channel=channel,
                    closed_by=interaction.user if isinstance(interaction.user, (discord.Member, discord.User)) else None,
                    reason="Closed from ticket UI",
                )
            except Exception as e:
                print("⚠️ mark_ticket_closed failed:", e)
                closed = False

        if not closed:
            await _rename_channel_closed(channel)
            await _lock_ticket_for_owner(channel, owner)
            closed = True

        try:
            if interaction.message:
                await interaction.message.edit(view=None)
        except Exception:
            pass

        try:
            await _post_staff_closed_message(channel, interaction.user)
        except Exception as e:
            print("⚠️ Failed posting staff-closed message:", e)

        await _reply_ephemeral(interaction, "✅ Ticket closed." if closed else "⚠️ Ticket close partially completed.")

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        emoji="↩️",
        custom_id="sv:ticket:cancel_close",
        row=0,
    )
    async def cancel_close(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        if not await _user_can_close_ticket(interaction, channel):
            return await _reply_ephemeral(interaction, "❌ Only the ticket owner or staff can do that.")

        try:
            if interaction.message:
                await interaction.message.edit(content="❎ Ticket close cancelled.", view=None)
        except Exception:
            pass

        await _reply_ephemeral(interaction, "✅ Cancelled.")


# ============================================================
# Public helper to post close confirmation inside a ticket
# ============================================================

async def _find_existing_close_prompt(channel: discord.TextChannel, limit: int = 50) -> Optional[discord.Message]:
    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
        async for msg in channel.history(limit=limit):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue

            try:
                content = str(getattr(msg, "content", "") or "")
                if _CLOSE_PROMPT_MARKER in content:
                    return msg
            except Exception:
                pass

            try:
                comps = getattr(msg, "components", None) or []
                for row in comps:
                    for child in (getattr(row, "children", None) or []):
                        cid = str(getattr(child, "custom_id", "") or "")
                        if cid in ("sv:ticket:confirm_close", "sv:ticket:cancel_close"):
                            return msg
            except Exception:
                pass
    except Exception:
        pass
    return None


async def prompt_ticket_close_confirmation(
    channel: discord.TextChannel,
    requested_by: Optional[discord.Member] = None,
) -> Optional[discord.Message]:
    lock = _lock_for(_CLOSE_PROMPT_LOCKS, channel.id)
    async with lock:
        existing = await _find_existing_close_prompt(channel)
        if existing:
            return existing

        try:
            actor = requested_by.mention if requested_by else "the ticket owner"
            return await channel.send(
                f"⚠️ {actor} requested to close this ticket.\n"
                "Please confirm below.\n"
                f"{_CLOSE_PROMPT_MARKER}",
                view=ConfirmCloseTicketView(),
            )
        except Exception as e:
            print("⚠️ Failed to post close confirmation:", e)
            return None


# ============================================================
# TRANSCRIPT (TicketTool-style routing / preserved behavior)
# ============================================================

def _ticket_number_from_name(name: str) -> str:
    m = re.search(r"(\d{3,})$", name or "")
    return m.group(1) if m else "0000"


def _safe_html(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def safe_user_display(user: discord.abc.User) -> str:
    try:
        return str(user)
    except Exception:
        return "Unknown"


async def build_html_transcript(
    channel: discord.TextChannel,
    limit: int = 2000
) -> Tuple[str, Dict[str, int], Dict[int, int], Dict[int, int]]:
    counts_label: Dict[str, int] = {}
    counts_uid: Dict[int, int] = {}
    mentions_uid: Dict[int, int] = {}
    rows: List[str] = []

    async for m in channel.history(limit=limit, oldest_first=True):
        author = m.author
        author_label = f"{author.display_name} - {str(author)}" if author else "Unknown"
        counts_label[author_label] = counts_label.get(author_label, 0) + 1

        try:
            if author and isinstance(author, (discord.Member, discord.User)):
                counts_uid[int(author.id)] = counts_uid.get(int(author.id), 0) + 1
        except Exception:
            pass

        try:
            for u in (m.mentions or []):
                if not u or getattr(u, "bot", False):
                    continue
                mentions_uid[int(u.id)] = mentions_uid.get(int(u.id), 0) + 1
        except Exception:
            pass

        ts = m.created_at.isoformat() if m.created_at else ""
        content = _safe_html(m.clean_content or "")

        att_lines: List[str] = []
        for a in (m.attachments or []):
            try:
                att_lines.append(
                    f'<div class="att">📎 <a href="{_safe_html(a.url)}">{_safe_html(a.filename)}</a></div>'
                )
            except Exception:
                pass

        embed_lines: List[str] = []
        for e in (m.embeds or []):
            try:
                et = _safe_html(e.title or "")
                ed = _safe_html(e.description or "")
                ef = _safe_html((e.footer.text or "") if e.footer else "")
                if et or ed or ef:
                    embed_lines.append(
                        f'<div class="embed"><div class="et">{et}</div><div class="ed">{ed}</div><div class="ef">{ef}</div></div>'
                    )
            except Exception:
                pass

        rows.append(
            f"""
            <div class="msg">
              <div class="meta">
                <span class="author">{_safe_html(author_label)}</span>
                <span class="time">{_safe_html(ts)}</span>
              </div>
              <div class="content">{content}</div>
              {''.join(att_lines)}
              {''.join(embed_lines)}
            </div>
            """
        )

    note_html = ""
    try:
        if not bot.intents.message_content:
            note_html = '<div class="note">⚠️ Bot does not have <b>Message Content</b> intent enabled; some plain-text messages may appear blank in this transcript.</div>'
    except Exception:
        pass

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>Transcript - {_safe_html(channel.name)}</title>
<style>
body{{font-family:Arial,Helvetica,sans-serif;background:#0f0f0f;color:#e6e6e6;margin:0;padding:16px}}
h1{{font-size:18px;margin:0 0 12px 0}}
.note{{padding:10px;border:1px solid #3a3a3a;border-radius:10px;background:#141414;margin:0 0 12px 0;font-size:13px;opacity:.95}}
.msg{{border:1px solid #2a2a2a;border-radius:10px;padding:10px;margin:10px 0;background:#151515}}
.meta{{display:flex;gap:10px;align-items:center;font-size:12px;color:#bdbdbd;margin-bottom:6px}}
.author{{font-weight:700;color:#fff}}
.time{{opacity:.8}}
.content{{white-space:pre-wrap;word-wrap:break-word}}
.att{{margin-top:6px;font-size:12px}}
.embed{{margin-top:8px;padding:8px;border-left:3px solid #5865f2;background:#101225;border-radius:8px}}
.et{{font-weight:700}}
.ed{{margin-top:4px;opacity:.95}}
.ef{{margin-top:6px;font-size:12px;opacity:.75}}
a{{color:#7aa2ff}}
</style>
</head>
<body>
<h1>Transcript for #{_safe_html(channel.name)}</h1>
{note_html}
<div>{''.join(rows)}</div>
</body>
</html>
"""
    return html, counts_label, counts_uid, mentions_uid


async def _find_existing_transcript_message(
    transcripts_ch: discord.TextChannel,
    source_channel_id: int,
    limit: int = 100,
) -> Optional[discord.Message]:
    marker = f"{_TRANSCRIPT_MARKER_PREFIX}{int(source_channel_id)}"

    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
        async for msg in transcripts_ch.history(limit=limit):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue

            try:
                embeds = getattr(msg, "embeds", None) or []
                for e in embeds:
                    footer_text = str(getattr(getattr(e, "footer", None), "text", "") or "")
                    if marker in footer_text:
                        return msg
            except Exception:
                pass

            try:
                content = str(getattr(msg, "content", "") or "")
                if marker in content:
                    return msg
            except Exception:
                pass
    except Exception:
        pass

    return None


async def send_tickettool_style_transcript(
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
    owner_id: Optional[int] = None,
    closed_by: Optional[discord.Member] = None,
    decision: Optional[str] = None,
):
    lock = _lock_for(_TRANSCRIPT_POST_LOCKS, channel.id)
    async with lock:
        try:
            if int(TRANSCRIPTS_CHANNEL_ID or 0) <= 0:  # type: ignore[name-defined]
                return
        except Exception:
            return

        guild = channel.guild
        transcripts_ch = guild.get_channel(int(TRANSCRIPTS_CHANNEL_ID))  # type: ignore[name-defined]
        if not isinstance(transcripts_ch, discord.TextChannel):
            print("⚠️ TRANSCRIPTS_CHANNEL_ID invalid or not a text channel:", TRANSCRIPTS_CHANNEL_ID)  # type: ignore[name-defined]
            return

        existing = await _find_existing_transcript_message(transcripts_ch, channel.id)
        if existing:
            try:
                svc = _get_ticket_service_fns()
                attach_transcript_to_ticket = svc["attach_transcript_to_ticket"]
                if attach_transcript_to_ticket:
                    url = existing.jump_url
                    if existing.attachments:
                        url = existing.attachments[0].url
                    await attach_transcript_to_ticket(
                        channel_id=channel.id,
                        transcript_url=url,
                        transcript_message_id=existing.id,
                        transcript_channel_id=transcripts_ch.id,
                    )
            except Exception:
                pass
            return

        me = guild.me or (guild.get_member(int(bot.user.id)) if bot.user else None)
        if not me:
            return

        tp = transcripts_ch.permissions_for(me)
        if not (tp.view_channel and tp.send_messages and tp.attach_files):
            print("⚠️ Missing perms in transcripts channel: need view/send/attach_files")
            return

        cp = channel.permissions_for(me)
        if not cp.read_message_history:
            await transcripts_ch.send(
                f"🧾 Transcript for <#{channel.id}> could not be generated — missing **Read Message History** in that ticket channel/category."
            )
            return

        html, counts_label, counts_uid, mentions_uid = await build_html_transcript(channel, limit=2000)

        def _truncate_embed_value(s: str, max_len: int = 1024) -> str:
            if not s:
                return "—"
            if len(s) <= max_len:
                return s
            return s[: max_len - 1] + "…"

        def _mention_uid(uid: int) -> str:
            mem = guild.get_member(uid)
            if isinstance(mem, discord.Member):
                return mem.mention
            return f"<@{uid}>"

        num = _ticket_number_from_name(channel.name)
        ticket_name = f"closed-{num}"
        filename = f"transcript-closed-{num}.html"
        file = discord.File(fp=io.BytesIO(html.encode("utf-8")), filename=filename)

        sorted_users = sorted(counts_label.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        users_lines = [f"**{cnt} - {label}**" for label, cnt in sorted_users[:25]]
        users_value = "\n".join(users_lines) if users_lines else "—"

        access_uids = set()
        try:
            for target, overwrite in (channel.overwrites or {}).items():
                if overwrite.view_channel is not True:
                    continue
                if isinstance(target, discord.Role):
                    continue
                tid = _overwrite_target_id(target)
                if not tid:
                    continue
                if bot.user and tid == int(bot.user.id):
                    continue
                try:
                    mem = guild.get_member(int(tid))
                    if mem and getattr(mem, "bot", False):
                        continue
                except Exception:
                    pass
                access_uids.add(int(tid))
        except Exception:
            pass

        participant_uids = set(counts_uid.keys()) | set(mentions_uid.keys()) | access_uids
        if owner:
            participant_uids.add(int(owner.id))
        if owner_id:
            participant_uids.add(int(owner_id))
        if closed_by:
            participant_uids.add(int(closed_by.id))
        if bot.user:
            participant_uids.discard(int(bot.user.id))

        staff_mentions: List[str] = []
        try:
            def _score(uid: int) -> int:
                return (counts_uid.get(uid, 0) * 10) + (mentions_uid.get(uid, 0) * 2) + (1 if uid in access_uids else 0)

            for uid in sorted(participant_uids, key=_score, reverse=True):
                if (owner and uid == owner.id) or (owner_id and uid == int(owner_id)):
                    continue
                mem = guild.get_member(uid)
                if isinstance(mem, discord.Member) and (not getattr(mem, "bot", False)) and is_staff(mem):  # type: ignore[name-defined]
                    staff_mentions.append(mem.mention)
            if closed_by and isinstance(closed_by, discord.Member) and is_staff(closed_by):  # type: ignore[name-defined]
                staff_mentions.insert(0, closed_by.mention)
            staff_mentions = list(dict.fromkeys(staff_mentions))
        except Exception:
            pass
        staff_value = " ".join(staff_mentions[:20]) if staff_mentions else "—"

        extra_uids = [
            uid for uid in participant_uids
            if uid not in counts_uid and (not owner or uid != owner.id) and (not owner_id or uid != int(owner_id))
        ]
        extra_mentions: List[str] = []
        try:
            for uid in sorted(extra_uids, key=lambda x: (mentions_uid.get(x, 0), 1 if x in access_uids else 0), reverse=True):
                mem = guild.get_member(uid)
                if isinstance(mem, discord.Member) and (not getattr(mem, "bot", False)) and is_staff(mem):  # type: ignore[name-defined]
                    continue
                extra_mentions.append(_mention_uid(uid))
            extra_mentions = list(dict.fromkeys(extra_mentions))
        except Exception:
            pass
        if extra_mentions:
            users_value = _truncate_embed_value(users_value + "\n\n**Also involved:** " + " ".join(extra_mentions[:15]))
        else:
            users_value = _truncate_embed_value(users_value)

        embed = discord.Embed(color=discord.Color.dark_green())

        owner_val = "Unknown"
        try:
            if owner:
                owner_val = f"{owner.mention} (`{owner.display_name}` | `{owner}` | `{owner.id}`)"
            elif owner_id:
                owner_val = f"<@{owner_id}> (`{owner_id}`) — left server"
        except Exception:
            owner_val = (f"<@{owner_id}> (`{owner_id}`)" if owner_id else "Unknown")

        embed.add_field(name="Ticket Owner", value=owner_val, inline=False)
        embed.add_field(name="Ticket Name", value=ticket_name, inline=False)
        embed.add_field(name="Panel Name", value=str(TRANSCRIPT_PANEL_NAME), inline=False)

        if decision:
            embed.add_field(name="Decision", value=f"`{decision}`", inline=False)
        if closed_by:
            embed.add_field(name="Closed By", value=f"{closed_by.mention} (`{closed_by.id}`)", inline=False)
        try:
            embed.add_field(name="Closed At", value=f"`{fmt_utc()}`", inline=False)  # type: ignore[name-defined]
        except Exception:
            embed.add_field(name="Closed At", value="`(time unavailable)`", inline=False)

        try:
            if not bot.intents.message_content:
                embed.add_field(
                    name="Transcript Note",
                    value="⚠️ Bot does not have **Message Content** intent enabled; some plain-text messages may appear blank in the transcript.",
                    inline=False,
                )
        except Exception:
            pass

        embed.add_field(name="Staff Involved", value=_truncate_embed_value(staff_value), inline=False)
        embed.add_field(name="Direct Transcript", value="Use Button", inline=False)
        embed.add_field(name="Users in transcript", value=users_value, inline=False)
        embed.set_footer(text=f"{_TRANSCRIPT_MARKER_PREFIX}{int(channel.id)}")

        msg = await transcripts_ch.send(embed=embed, file=file)

        transcript_url: Optional[str] = None

        try:
            if msg.attachments:
                dl_url = msg.attachments[0].url
                transcript_url = dl_url
                view = discord.ui.View(timeout=None)
                view.add_item(discord.ui.Button(label="Download Transcript", style=discord.ButtonStyle.link, url=dl_url))
                view.add_item(discord.ui.Button(label="Direct Link", style=discord.ButtonStyle.link, url=msg.jump_url))
                await msg.edit(view=view)
            else:
                transcript_url = msg.jump_url
        except Exception as e:
            print("⚠️ Failed to add transcript buttons:", e)
            try:
                transcript_url = msg.jump_url
            except Exception:
                transcript_url = None

        try:
            svc = _get_ticket_service_fns()
            attach_transcript_to_ticket = svc["attach_transcript_to_ticket"]
            if attach_transcript_to_ticket:
                await attach_transcript_to_ticket(
                    channel_id=channel.id,
                    transcript_url=transcript_url,
                    transcript_message_id=msg.id,
                    transcript_channel_id=transcripts_ch.id,
                )
        except Exception as e:
            print("⚠️ Failed attaching transcript metadata to ticket:", e)


# ============================================================
# AUTO CLOSE (transcript routing BEFORE delete)
# ============================================================

async def auto_close_after_decision(
    channel: discord.TextChannel,
    closer: Optional[discord.Member] = None,
    decision: Optional[str] = None,
):
    """
    If AUTO_DELETE_TICKET_SECONDS > 0:
    - announce timer
    - post transcript first
    - mark deleted in DB
    - delete channel
    """
    try:
        if int(AUTO_DELETE_TICKET_SECONDS or 0) <= 0:  # type: ignore[name-defined]
            return
    except Exception:
        return

    try:
        owner = await find_ticket_owner_retry(channel)

        await channel.send(f"🕒 Decision made. Ticket will auto-close in **{AUTO_DELETE_TICKET_SECONDS} seconds**.")  # type: ignore[name-defined]
        await asyncio.sleep(int(AUTO_DELETE_TICKET_SECONDS))  # type: ignore[name-defined]

        try:
            await send_tickettool_style_transcript(channel, owner, closed_by=closer, decision=decision)
        except Exception as e:
            print("⚠️ Transcript routing failed:", e)

        try:
            svc = _get_ticket_service_fns()
            mark_ticket_deleted = svc["mark_ticket_deleted"]
            if mark_ticket_deleted:
                await mark_ticket_deleted(
                    channel_id=channel.id,
                    deleted_by=closer,
                    reason=decision or "Verification ticket closed after staff decision",
                )
        except Exception as e:
            print("⚠️ mark_ticket_deleted during auto-close failed:", e)

        try:
            await channel.delete(reason="Verification ticket closed after staff decision")
            try:
                RUNTIME_STATS["tickets_closed"] = int(RUNTIME_STATS.get("tickets_closed", 0) or 0) + 1
            except Exception:
                pass
        except discord.Forbidden:
            try:
                await channel.send("⚠️ I could not delete this ticket (missing **Manage Channels**). Transcript was still posted.")
            except Exception:
                pass
        except Exception as e:
            print("⚠️ Channel delete failed:", e)

    except Exception as e:
        print("⚠️ Auto-close failed:", e)


# ============================================================
# UI HELPERS (kept for existing behavior)
# ============================================================

VERIFY_EMBED_TITLE = "Stoney Balonney Verification"
VERIFY_EMBED_DESC = "Token-scoped upload. Staff review happens inside your private Discord ticket."


def build_verify_ui_view(*, token: str | None = None) -> discord.ui.View:
    """
    Compatibility helper only.
    verify_ui.py owns the real active verify buttons.
    """
    view = discord.ui.View(timeout=None)

    view.add_item(
        discord.ui.Button(
            label="Get Secure Upload",
            style=discord.ButtonStyle.primary,
            custom_id="verify:get_upload",
            emoji="🔐",
            row=0,
        )
    )

    vc_id = _vc_channel_id()
    if vc_id:
        view.add_item(
            discord.ui.Button(
                label="Verify in VC",
                style=discord.ButtonStyle.secondary,
                custom_id="verify:vc",
                emoji="🎙️",
                row=0,
            )
        )

    try:
        view.add_item(
            discord.ui.Button(
                label="Tap to view website",
                style=discord.ButtonStyle.link,
                url=VERIFY_SITE_URL,  # type: ignore[name-defined]
                emoji="🌐",
                row=1,
            )
        )
    except Exception:
        pass

    view.add_item(
        discord.ui.Button(
            label="Reveal Raw Link",
            style=discord.ButtonStyle.secondary,
            custom_id="verify:reveal_raw",
            emoji="🔎",
            row=1,
        )
    )

    try:
        if bool(ALLOW_USER_VERIFYLINK):  # type: ignore[name-defined]
            view.add_item(
                discord.ui.Button(
                    label="Generate New Link",
                    style=discord.ButtonStyle.secondary,
                    custom_id="verify:regen",
                    emoji="🔁",
                    row=1,
                )
            )
    except Exception:
        pass

    return view


async def find_last_verify_ui_message(channel: discord.TextChannel, limit: int = 80) -> Optional[discord.Message]:
    try:
        async for msg in channel.history(limit=limit):
            if not msg.author or not bot.user or msg.author.id != bot.user.id:
                continue
            if msg.embeds:
                for e in msg.embeds:
                    title = (e.title or "").strip()
                    footer_text = str(getattr(getattr(e, "footer", None), "text", "") or "")
                    if title == VERIFY_EMBED_TITLE or title == "Stoney Balonney Verification" or "stoney_verify:verify_ui:" in footer_text:
                        return msg
            if msg.content and "🌿 **Verification Required**" in msg.content:
                return msg
    except Exception:
        pass
    return None


def _build_verify_embed(*, user: discord.abc.User, ttl_minutes: int, reason: str) -> discord.Embed:
    e = discord.Embed(
        title=VERIFY_EMBED_TITLE,
        color=VERIFY_EMBED_COLOR,
        timestamp=now_utc(),  # type: ignore[name-defined]
    )

    try:
        if VERIFY_EMBED_THUMBNAIL_URL:
            e.set_thumbnail(url=VERIFY_EMBED_THUMBNAIL_URL)
    except Exception:
        pass

    e.description = f"👋 **User:** {user.mention}\n({safe_user_display(user)} • `{user.id}`)"

    e.add_field(
        name="🌿 Verification Required",
        value=(
            "Press **Get Secure Upload** to receive your secure upload link.\n"
            "You may redact private info before submitting.\n"
            "If you don't trust uploads, you can request **Verify in VC**."
        ),
        inline=False,
    )
    e.add_field(
        name="⏳ Expiration",
        value=f"Link expires in **{ttl_minutes} minutes**.",
        inline=False,
    )
    e.add_field(
        name="⚠️ Approval",
        value="Roles are granted only after **staff approval**.",
        inline=False,
    )
    e.add_field(
        name="🔒 Privacy",
        value="The upload link is only shown to the ticket owner (ephemeral).",
        inline=False,
    )

    e.set_footer(text=f"Reason: {reason}")
    return e


async def ensure_verify_ui_present(channel: Union[discord.abc.GuildChannel, discord.Thread], reason: str = "ensure") -> bool:
    """
    Idempotently ensure the verification UI exists in a ticket channel/thread.
    """
    try:
        ch_any: Union[discord.TextChannel, discord.Thread, None] = None

        if isinstance(channel, discord.Thread):
            ch_any = channel
        elif isinstance(channel, discord.TextChannel):
            ch_any = channel
        else:
            return False

        if not getattr(ch_any, "guild", None):
            return False

        if not is_verification_ticket_channel(ch_any):
            return False

        post_channel: Optional[discord.TextChannel] = None
        if isinstance(ch_any, discord.TextChannel):
            post_channel = ch_any
        else:
            try:
                parent = ch_any.parent
                if isinstance(parent, discord.TextChannel):
                    post_channel = parent
            except Exception:
                post_channel = None

        if not post_channel:
            return False

        try:
            ready = await wait_for_channel_ready(post_channel)
            if not ready:
                print(f"⚠️ ensure_verify_ui_present: perms not ready for channel={post_channel.id} reason={reason}")
                return False
        except Exception:
            pass

        existing = await find_last_verify_ui_message(post_channel, limit=80)
        if existing:
            return True

        owner = None
        try:
            owner = await find_ticket_owner_retry(post_channel)
        except Exception:
            owner = None

        requester_id: Optional[int] = None
        try:
            if owner is None:
                requester_id = None
            elif isinstance(owner, discord.Member):
                requester_id = int(owner.id)
            else:
                requester_id = int(getattr(owner, "id", owner))
        except Exception:
            requester_id = None

        result = await post_or_replace_verify_ui(
            post_channel,
            requester_id=requester_id,
            reason=reason,
            site_url=VERIFY_SITE_URL,  # type: ignore[name-defined]
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),  # type: ignore[name-defined]
            allow_regen=bool(ALLOW_USER_VERIFYLINK),  # type: ignore[name-defined]
        )
        return bool(result)
    except Exception as e:
        try:
            print(f"⚠️ ensure_verify_ui_present failed (channel={getattr(channel,'id',0)} reason={reason}): {e}")
        except Exception:
            pass
        return False


# ============================================================
# PERMISSION CHECKING
# ============================================================

async def check_bot_can_assign_roles(guild: discord.Guild) -> Tuple[bool, str, List[discord.Role]]:
    bot_member = guild.me

    if not bot_member or not bot_member.guild_permissions.manage_roles:
        return False, "Bot lacks **Manage Roles** permission", []

    verified_role = guild.get_role(int(VERIFIED_ROLE_ID or 0))  # type: ignore[name-defined]
    resident_role = guild.get_role(int(RESIDENT_ROLE_ID or 0))  # type: ignore[name-defined]

    roles_to_assign: List[discord.Role] = []
    missing_roles: List[str] = []

    if not verified_role:
        missing_roles.append(f"Verified (ID: {VERIFIED_ROLE_ID})")  # type: ignore[name-defined]
    else:
        roles_to_assign.append(verified_role)

    if not resident_role:
        missing_roles.append(f"Resident (ID: {RESIDENT_ROLE_ID})")  # type: ignore[name-defined]
    else:
        roles_to_assign.append(resident_role)

    if missing_roles:
        return False, f"Missing roles: {', '.join(missing_roles)}", roles_to_assign

    hierarchy_issues: List[str] = []
    for role in roles_to_assign:
        if bot_member.top_role <= role:
            hierarchy_issues.append(f"Bot role must be **above** {role.name} in role list")

    if hierarchy_issues:
        return False, "; ".join(hierarchy_issues), roles_to_assign

    managed_issues: List[str] = []
    for role in roles_to_assign:
        if role.managed:
            managed_issues.append(f"{role.name} is managed by an integration/bot")

    if managed_issues:
        return False, "; ".join(managed_issues), roles_to_assign

    return True, "OK", roles_to_assign


# ============================================================
# Register persistent views so buttons survive restarts
# ============================================================

@bot.listen("on_ready")
async def _register_transcript_views():
    try:
        bot.add_view(ConfirmCloseTicketView())
    except Exception as e:
        print("⚠️ Failed to register ConfirmCloseTicketView:", e)

    try:
        bot.add_view(StaffClosedTicketView())
    except Exception as e:
        print("⚠️ Failed to register StaffClosedTicketView:", e)

    try:
        bot.add_view(VerificationStaffReviewView())
    except Exception as e:
        print("⚠️ Failed to register VerificationStaffReviewView:", e)
