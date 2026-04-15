# stoney_verify/transcripts.py
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import discord

from .globals import *  # noqa
from .tickets import (
    find_ticket_owner_retry,
    is_verification_ticket_channel,
    wait_for_channel_ready,
)
from .verify_ui import post_or_replace_verify_ui

from .tickets_new.service import (
    attach_transcript_to_ticket,
    mark_ticket_closed,
    reopen_ticket_channel,
)
from .tickets_new.transcript_service import (
    delete_ticket_with_optional_transcript,
    generate_transcript_files,
    post_transcript_to_channel,
    staff_delete_closed_ticket,
)

# ============================================================
# Compatibility / safety
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
# Markers / locks
# ============================================================

_CLOSE_PROMPT_MARKER = "stoney_verify:close_prompt:v4"
_STAFF_CLOSED_MARKER = "stoney_verify:staff_closed:v4"
_CLOSE_REOPENED_MARKER = "stoney_verify:ticket_reopened:v3"
_STAFF_REVIEW_PANEL_MARKER = "stoney_verify:staff_review_panel:v2"

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
# Small helpers
# ============================================================

def _safe_text(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _vc_channel_id() -> int:
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


def _ticket_number_from_name(name: str) -> str:
    m = re.search(r"(\d{3,})$", name or "")
    return m.group(1) if m else "0000"


def _closed_ticket_name(name: str) -> str:
    return f"closed-{_ticket_number_from_name(name)}"


def _open_ticket_name(name: str) -> str:
    return f"ticket-{_ticket_number_from_name(name)}"


def safe_user_display(user: discord.abc.User) -> str:
    try:
        return str(user)
    except Exception:
        return "Unknown"


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


# ============================================================
# Close prompt / staff closed panel helpers
# ============================================================

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
# Transcript wrapper helpers
# ============================================================

async def build_html_transcript(
    channel: discord.TextChannel,
    limit: int = 2000,
) -> Tuple[str, Dict[str, int], Dict[int, int], Dict[int, int]]:
    """
    Compatibility wrapper.

    Old code expected HTML + count maps from this module.
    New transcript generation lives in tickets_new.transcript_service.
    """
    counts_label: Dict[str, int] = {}
    counts_uid: Dict[int, int] = {}
    mentions_uid: Dict[int, int] = {}

    scanned = 0

    try:
        async for m in channel.history(limit=limit, oldest_first=True):
            scanned += 1
            author = m.author
            author_label = f"{getattr(author, 'display_name', str(author))} - {str(author)}" if author else "Unknown"
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
    except Exception as e:
        print("⚠️ build_html_transcript history scan failed:", repr(e))

    try:
        _txt_file, html_file, _message_count = await generate_transcript_files(channel)
        buffer = getattr(html_file, "fp", None)
        if buffer is None:
            raise RuntimeError("html transcript file buffer missing")
        try:
            buffer.seek(0)
        except Exception:
            pass
        raw = buffer.read()
        if isinstance(raw, bytes):
            html_text = raw.decode("utf-8", errors="replace")
        else:
            html_text = str(raw)
    except Exception as e:
        print("⚠️ build_html_transcript wrapper failed:", repr(e))
        html_text = (
            "<!doctype html><html><body>"
            f"<h1>Transcript unavailable for #{channel.name}</h1>"
            f"<p>Scanned messages: {scanned}</p>"
            "</body></html>"
        )

    return html_text, counts_label, counts_uid, mentions_uid


async def send_tickettool_style_transcript(
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
    owner_id: Optional[int] = None,
    closed_by: Optional[discord.Member] = None,
    decision: Optional[str] = None,
):
    """
    Wrapper around tickets_new.transcript_service.post_transcript_to_channel.

    Keeps the old public function name so existing callers do not break.
    """
    try:
        reason = decision or "Ticket transcript requested"
        posted, transcript_url = await post_transcript_to_channel(
            ticket_channel=channel,
            deleted_by=closed_by,
            reason=reason,
        )

        if posted is None:
            return

        try:
            await attach_transcript_to_ticket(
                channel_id=channel.id,
                transcript_url=transcript_url or getattr(posted, "jump_url", None),
                transcript_message_id=posted.id,
                transcript_channel_id=posted.channel.id,
                actor=closed_by,
            )
        except Exception as e:
            print("⚠️ Failed attaching transcript metadata to ticket:", e)

    except Exception as e:
        print("⚠️ send_tickettool_style_transcript wrapper failed:", repr(e))


async def auto_close_after_decision(
    channel: discord.TextChannel,
    closer: Optional[discord.Member] = None,
    decision: Optional[str] = None,
):
    """
    Auto-close wrapper that fully delegates transcript/delete execution
    to tickets_new.transcript_service.
    """
    try:
        if int(AUTO_DELETE_TICKET_SECONDS or 0) <= 0:  # type: ignore[name-defined]
            return
    except Exception:
        return

    try:
        await channel.send(
            f"🕒 Decision made. Ticket will auto-close in **{AUTO_DELETE_TICKET_SECONDS} seconds**."  # type: ignore[name-defined]
        )
        await asyncio.sleep(int(AUTO_DELETE_TICKET_SECONDS))  # type: ignore[name-defined]

        is_ghost = False
        try:
            is_ghost = "ghost" in str(channel.name or "").lower()
        except Exception:
            is_ghost = False

        result = await delete_ticket_with_optional_transcript(
            channel=channel,
            deleted_by=closer,
            is_ghost=is_ghost,
            force_transcript_for_ghost=False,
            reason=decision or "Verification ticket closed after staff decision",
        )

        if bool(result.get("deleted")):
            try:
                RUNTIME_STATS["tickets_closed"] = int(RUNTIME_STATS.get("tickets_closed", 0) or 0) + 1
            except Exception:
                pass
        else:
            print("⚠️ auto_close_after_decision did not fully delete ticket:", result)

    except Exception as e:
        print("⚠️ Auto-close failed:", e)


# ============================================================
# Staff review panel for existing verification ticket
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
# Staff closed actions view
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

        try:
            reopened = await reopen_ticket_channel(
                channel=channel,
                owner=owner,
                actor=interaction.user,
                reason="Reopened from Discord ticket controls",
            )
        except Exception as e:
            print("⚠️ reopen_ticket_channel failed:", e)
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

        is_ghost = False
        try:
            is_ghost = "ghost" in str(channel.name or "").lower()
        except Exception:
            is_ghost = False

        try:
            result = await staff_delete_closed_ticket(
                channel=channel,
                staff_member=interaction.user,
                is_ghost=is_ghost,
                reason="Deleted by staff",
            )

            if bool(result.get("deleted")):
                try:
                    RUNTIME_STATS["tickets_closed"] = int(RUNTIME_STATS.get("tickets_closed", 0) or 0) + 1
                except Exception:
                    pass
                return

            await _reply_ephemeral(
                interaction,
                f"❌ Failed to delete ticket: {result.get('reason') or 'Unknown error'}",
            )
        except Exception as e:
            print("⚠️ staff_delete_closed_ticket failed:", e)
            await _reply_ephemeral(interaction, f"❌ Failed to delete ticket: {e}")


# ============================================================
# Confirm close view
# ============================================================

class ConfirmCloseTicketView(discord.ui.View):
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
# UI helpers
# ============================================================

VERIFY_EMBED_TITLE = "Stoney Balonney Verification"
VERIFY_EMBED_DESC = "Token-scoped upload. Staff review happens inside your private Discord ticket."


def build_verify_ui_view(*, token: str | None = None) -> discord.ui.View:
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
# Permission checking
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
# Persistent views
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


__all__ = [
    "VerificationStaffReviewView",
    "StaffClosedTicketView",
    "ConfirmCloseTicketView",
    "prompt_ticket_close_confirmation",
    "send_tickettool_style_transcript",
    "auto_close_after_decision",
    "build_html_transcript",
    "build_verify_ui_view",
    "find_last_verify_ui_message",
    "ensure_verify_ui_present",
    "check_bot_can_assign_roles",
    "post_or_replace_verification_staff_panel",
]
