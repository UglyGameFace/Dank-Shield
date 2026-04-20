from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc

from ..tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
)

from .common import (
    _staff_check,
    reply_once,
    mark_ticket_activity,
)

try:
    from ..tickets_new.repository import (
        get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id,
        safe_optional_update_by_channel_id,
    )
except Exception:
    async def repo_get_ticket_by_any_channel_id(channel_id: int | str):  # type: ignore
        return None

    async def safe_optional_update_by_channel_id(channel_id: int | str, patch: Dict[str, Any]) -> bool:  # type: ignore
        return False


_CANONICAL_TICKET_NAME_RE = re.compile(r"^(ticket|closed)-(\d+)$", re.I)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        return _safe_str((row or {}).get("status"), "unknown").lower()
    except Exception:
        return "unknown"


def _ticket_number(row: Optional[Dict[str, Any]], channel: Optional[discord.TextChannel] = None) -> int:
    try:
        num = _safe_int((row or {}).get("ticket_number"), 0)
        if num > 0:
            return num
    except Exception:
        pass

    try:
        if channel is not None:
            match = _CANONICAL_TICKET_NAME_RE.match(_safe_str(channel.name))
            if match:
                return _safe_int(match.group(2), 0)
    except Exception:
        pass

    return 0


def _is_canonical_ticket_name(name: str) -> bool:
    try:
        return bool(_CANONICAL_TICKET_NAME_RE.match(_safe_str(name)))
    except Exception:
        return False


def _is_ticket_channel(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> bool:
    if isinstance(row, dict):
        return True
    try:
        return bool(is_verification_ticket_channel(channel))
    except Exception:
        return False


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    try:
        row = await repo_get_ticket_by_any_channel_id(int(channel.id))
        if isinstance(row, dict):
            return dict(row)
    except Exception:
        pass
    return None


async def _ensure_ticket_context(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> Tuple[Optional[discord.TextChannel], Optional[Dict[str, Any]]]:
    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        await reply_once(
            interaction,
            {"content": "❌ Must be used in a ticket text channel.", "ephemeral": True},
        )
        return None, None

    row = await _ticket_row_for_channel(ch)
    if not _is_ticket_channel(ch, row):
        await reply_once(
            interaction,
            {
                "content": f"❌ `{ch.name}` is not recognized as a ticket channel.",
                "ephemeral": True,
            },
        )
        return None, None

    return ch, row


async def _ticket_owner(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> Optional[discord.Member | discord.User]:
    try:
        owner_id = _safe_int((row or {}).get("owner_id") or (row or {}).get("user_id"), 0)
        if owner_id > 0:
            member = channel.guild.get_member(owner_id)
            if member:
                return member
            try:
                return await channel.guild.fetch_member(owner_id)
            except Exception:
                pass
    except Exception:
        pass

    try:
        return await find_ticket_owner_retry(channel)
    except Exception:
        return None


def _member_is_ticket_owner(member: discord.Member, row: Optional[Dict[str, Any]]) -> bool:
    try:
        owner_id = _safe_int((row or {}).get("owner_id") or (row or {}).get("user_id"), 0)
        return owner_id > 0 and int(member.id) == owner_id
    except Exception:
        return False


def _member_is_staff_like(member: discord.Member) -> bool:
    try:
        if member.guild_permissions.administrator:
            return True
        if member.guild_permissions.manage_channels:
            return True
    except Exception:
        pass

    try:
        staff_role_id = _safe_int(globals().get("STAFF_ROLE_ID"), 0)
        if staff_role_id > 0:
            return any(int(role.id) == staff_role_id for role in member.roles)
    except Exception:
        pass

    return False


def _build_member_overwrite(
    existing: discord.PermissionOverwrite,
    *,
    can_view: bool,
    can_send: bool,
) -> discord.PermissionOverwrite:
    overwrite = discord.PermissionOverwrite.from_pair(*existing.pair())
    overwrite.view_channel = can_view
    overwrite.send_messages = can_send
    overwrite.attach_files = can_send
    overwrite.embed_links = can_send
    overwrite.read_message_history = True
    return overwrite


async def _persist_channel_name(channel: discord.TextChannel) -> None:
    try:
        await safe_optional_update_by_channel_id(channel.id, {"channel_name": channel.name})
    except Exception:
        pass


async def _touch_ticket_channel(channel: discord.TextChannel) -> None:
    try:
        mark_ticket_activity(channel.id)
    except Exception:
        pass


def register_ticket_channel_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_add",
        description="Grant a member access to the current ticket.",
    )
    @app_commands.describe(
        member="Member to add to the ticket",
        channel="Ticket channel to update (leave empty to use current channel)",
    )
    async def ticket_add(
        interaction: discord.Interaction,
        member: discord.Member,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        status = _ticket_status(row)
        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Cannot modify access on a deleted ticket.", "ephemeral": True},
            )

        if _member_is_ticket_owner(member, row):
            return await reply_once(
                interaction,
                {"content": "ℹ️ That member is already the ticket owner.", "ephemeral": True},
            )

        try:
            existing = ch.overwrites_for(member)
            overwrite = _build_member_overwrite(existing, can_view=True, can_send=(status != "closed"))
            await ch.set_permissions(
                member,
                overwrite=overwrite,
                reason=f"Ticket access granted by {interaction.user}",
            )
        except Exception as e:
            return await reply_once(
                interaction,
                {"content": f"❌ Failed adding member to ticket: {e}", "ephemeral": True},
            )

        try:
            if status == "closed":
                await ch.send(
                    f"➕ {member.mention} was added to the ticket by {interaction.user.mention}. "
                    f"They can view it, but the ticket is closed."
                )
            else:
                await ch.send(f"➕ {member.mention} was added to the ticket by {interaction.user.mention}.")
        except Exception:
            pass

        await _touch_ticket_channel(ch)
        await reply_once(interaction, {"content": f"✅ Added {member.mention} to {ch.mention}.", "ephemeral": True})

    @tree.command(
        name="ticket_remove",
        description="Remove a member's access from the current ticket.",
    )
    @app_commands.describe(
        member="Member to remove from the ticket",
        channel="Ticket channel to update (leave empty to use current channel)",
    )
    async def ticket_remove(
        interaction: discord.Interaction,
        member: discord.Member,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        status = _ticket_status(row)
        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Cannot modify access on a deleted ticket.", "ephemeral": True},
            )

        if _member_is_ticket_owner(member, row):
            return await reply_once(
                interaction,
                {"content": "❌ You cannot remove the ticket owner. Transfer ownership first if needed.", "ephemeral": True},
            )

        if _member_is_staff_like(member):
            return await reply_once(
                interaction,
                {
                    "content": "❌ This member has staff-level access. Remove their staff access separately if that is what you want.",
                    "ephemeral": True,
                },
            )

        try:
            await ch.set_permissions(
                member,
                overwrite=None,
                reason=f"Ticket access removed by {interaction.user}",
            )
        except Exception as e:
            return await reply_once(
                interaction,
                {"content": f"❌ Failed removing member from ticket: {e}", "ephemeral": True},
            )

        try:
            await ch.send(f"➖ {member.mention} was removed from the ticket by {interaction.user.mention}.")
        except Exception:
            pass

        await _touch_ticket_channel(ch)
        await reply_once(interaction, {"content": f"✅ Removed {member.mention} from {ch.mention}.", "ephemeral": True})

    @tree.command(
        name="ticket_rename",
        description="Rename the current ticket channel.",
    )
    @app_commands.describe(
        name="New ticket channel name",
        channel="Ticket channel to rename (leave empty to use current channel)",
    )
    async def ticket_rename(
        interaction: discord.Interaction,
        name: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        ticket_num = _ticket_number(row, ch)
        if ticket_num > 0 or _is_canonical_ticket_name(ch.name):
            return await reply_once(
                interaction,
                {
                    "content": (
                        "❌ Manual renaming is disabled for numbered tickets.\n"
                        "This bot keeps canonical names like `ticket-0032` / `closed-0032` so close/reopen/delete state stays reliable."
                    ),
                    "ephemeral": True,
                },
            )

        new_name = _safe_str(name).lower().replace(" ", "-")
        if not new_name:
            return await reply_once(
                interaction,
                {"content": "❌ New channel name cannot be empty.", "ephemeral": True},
            )

        try:
            await ch.edit(name=new_name, reason=f"Ticket renamed by {interaction.user}")
            await _persist_channel_name(ch)
        except Exception as e:
            return await reply_once(
                interaction,
                {"content": f"❌ Failed renaming ticket: {e}", "ephemeral": True},
            )

        try:
            await ch.send(f"✏️ Ticket renamed to `{new_name}` by {interaction.user.mention}.")
        except Exception:
            pass

        await _touch_ticket_channel(ch)
        await reply_once(interaction, {"content": f"✅ Renamed ticket to `{new_name}`.", "ephemeral": True})

    @tree.command(
        name="ticket_lock",
        description="Lock the ticket so the owner cannot reply.",
    )
    @app_commands.describe(channel="Ticket channel to lock (leave empty to use current channel)")
    async def ticket_lock(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        status = _ticket_status(row)
        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Deleted tickets cannot be locked.", "ephemeral": True},
            )
        if status == "closed":
            return await reply_once(
                interaction,
                {"content": "ℹ️ This ticket is already closed, so the owner is already locked from replying.", "ephemeral": True},
            )

        owner = await _ticket_owner(ch, row)
        if owner is None or not isinstance(owner, discord.Member):
            return await reply_once(
                interaction,
                {"content": "❌ Could not resolve the ticket owner for this channel.", "ephemeral": True},
            )

        try:
            existing = ch.overwrites_for(owner)
            overwrite = _build_member_overwrite(existing, can_view=True, can_send=False)
            await ch.set_permissions(
                owner,
                overwrite=overwrite,
                reason=f"Ticket locked by {interaction.user}",
            )
        except Exception as e:
            return await reply_once(
                interaction,
                {"content": f"❌ Failed locking ticket: {e}", "ephemeral": True},
            )

        try:
            await ch.send(f"🔒 Ticket locked by {interaction.user.mention}. {owner.mention} can no longer reply.")
        except Exception:
            pass

        await _touch_ticket_channel(ch)
        await reply_once(interaction, {"content": f"✅ Locked {ch.mention}.", "ephemeral": True})

    @tree.command(
        name="ticket_unlock",
        description="Unlock the ticket so the owner can reply again.",
    )
    @app_commands.describe(channel="Ticket channel to unlock (leave empty to use current channel)")
    async def ticket_unlock(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        status = _ticket_status(row)
        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Deleted tickets cannot be unlocked.", "ephemeral": True},
            )
        if status == "closed":
            return await reply_once(
                interaction,
                {
                    "content": (
                        "❌ Closed tickets should stay reply-locked.\n"
                        "Use `/ticket_reopen` if you want the owner to speak again."
                    ),
                    "ephemeral": True,
                },
            )

        owner = await _ticket_owner(ch, row)
        if owner is None or not isinstance(owner, discord.Member):
            return await reply_once(
                interaction,
                {"content": "❌ Could not resolve the ticket owner for this channel.", "ephemeral": True},
            )

        try:
            existing = ch.overwrites_for(owner)
            overwrite = _build_member_overwrite(existing, can_view=True, can_send=True)
            await ch.set_permissions(
                owner,
                overwrite=overwrite,
                reason=f"Ticket unlocked by {interaction.user}",
            )
        except Exception as e:
            return await reply_once(
                interaction,
                {"content": f"❌ Failed unlocking ticket: {e}", "ephemeral": True},
            )

        try:
            await ch.send(f"🔓 Ticket unlocked by {interaction.user.mention}. {owner.mention} can reply again.")
        except Exception:
            pass

        await _touch_ticket_channel(ch)
        await reply_once(interaction, {"content": f"✅ Unlocked {ch.mention}.", "ephemeral": True})

    @tree.command(
        name="ticket_owner",
        description="Show the owner of the current ticket.",
    )
    @app_commands.describe(channel="Ticket channel to inspect (leave empty to use current channel)")
    async def ticket_owner(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        owner = await _ticket_owner(ch, row)
        if owner is None:
            return await reply_once(interaction, {"content": "❌ Could not resolve the ticket owner.", "ephemeral": True})

        embed = discord.Embed(
            title="🎫 Ticket Owner",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
        embed.add_field(
            name="Owner",
            value=f"{getattr(owner, 'mention', _safe_str(owner))}\n`{getattr(owner, 'id', 'unknown')}`",
            inline=False,
        )

        row = row or {}
        if row:
            embed.add_field(name="Category", value=f"`{_safe_str(row.get('category'), 'unknown')}`", inline=True)
            embed.add_field(name="Status", value=f"`{_safe_str(row.get('status'), 'unknown')}`", inline=True)
            embed.add_field(name="Priority", value=f"`{_safe_str(row.get('priority'), 'medium')}`", inline=True)
            ticket_num = _ticket_number(row, ch)
            if ticket_num > 0:
                embed.add_field(name="Ticket Number", value=f"`{ticket_num}`", inline=True)

        await reply_once(interaction, {"embed": embed, "ephemeral": True})
