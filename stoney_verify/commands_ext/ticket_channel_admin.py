from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc
from .. import globals as _g

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


def _truncate(text: Any, limit: int = 180) -> str:
    raw = _safe_str(text)
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)] + "…"


def _ticket_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        raw = _safe_str((row or {}).get("status"), "unknown").lower()
        if raw in {"open", "claimed", "closed", "deleted"}:
            return raw
        if raw in {"active", "reopened"}:
            return "open"
    except Exception:
        pass
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


def _channel_looks_closed(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("closed-")
    except Exception:
        return False


def _channel_looks_open(channel: discord.TextChannel) -> bool:
    try:
        return _safe_str(channel.name).lower().startswith("ticket-")
    except Exception:
        return False


def _ticket_archive_category_id() -> int:
    for key in (
        "TICKET_ARCHIVE_CATEGORY_ID",
        "TICKET_ARCHIVED_CATEGORY_ID",
        "ARCHIVED_TICKET_CATEGORY_ID",
        "ARCHIVE_TICKET_CATEGORY_ID",
    ):
        try:
            value = int(getattr(_g, key, 0) or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _ticket_active_category_id() -> int:
    try:
        return int(getattr(_g, "TICKET_CATEGORY_ID", 0) or 0)
    except Exception:
        return 0


def _looks_like_archive_category_name(name: str) -> bool:
    text = _safe_str(name).lower()
    if not text:
        return False

    markers = (
        "archive",
        "archived",
        "ticket archive",
        "tickets archive",
        "archived tickets",
        "closed tickets",
    )
    return any(marker in text for marker in markers)


def _resolve_category_by_id(
    guild: discord.Guild,
    category_id: int,
) -> Optional[discord.CategoryChannel]:
    try:
        if category_id <= 0:
            return None
        channel = guild.get_channel(int(category_id))
        if isinstance(channel, discord.CategoryChannel):
            return channel
    except Exception:
        pass
    return None


def _resolve_archive_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    explicit_id = _ticket_archive_category_id()
    if explicit_id > 0:
        explicit = _resolve_category_by_id(guild, explicit_id)
        if explicit is not None:
            return explicit

    try:
        for category in guild.categories:
            if _looks_like_archive_category_name(category.name):
                return category
    except Exception:
        pass

    return None


def _resolve_active_ticket_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    active_id = _ticket_active_category_id()
    if active_id > 0:
        active = _resolve_category_by_id(guild, active_id)
        if active is not None:
            return active
    return None


def _channel_is_in_category(
    channel: discord.TextChannel,
    category: Optional[discord.CategoryChannel],
) -> bool:
    try:
        if category is None:
            return False
        return int(getattr(channel.category, "id", 0) or 0) == int(category.id)
    except Exception:
        return False


def _channel_is_in_archive_category(channel: discord.TextChannel) -> bool:
    archive_category = _resolve_archive_category(channel.guild)
    if archive_category and _channel_is_in_category(channel, archive_category):
        return True
    try:
        if channel.category and _looks_like_archive_category_name(channel.category.name):
            return True
    except Exception:
        pass
    return False


def _channel_is_in_active_category(channel: discord.TextChannel) -> bool:
    active_category = _resolve_active_ticket_category(channel.guild)
    if active_category and _channel_is_in_category(channel, active_category):
        return True
    return False


def _ticket_effectively_closed(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> bool:
    status = _ticket_status(row)
    if status in {"closed", "deleted"}:
        return True
    if _channel_looks_closed(channel):
        return True
    if _channel_is_in_archive_category(channel):
        return True
    return False


def _ticket_effectively_open(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
) -> bool:
    status = _ticket_status(row)
    if status in {"open", "claimed"} and not _ticket_effectively_closed(channel=channel, row=row):
        return True
    if _channel_looks_open(channel) and not _channel_is_in_archive_category(channel):
        return True
    return False


def _ticket_location_label(channel: discord.TextChannel) -> str:
    archive_category = _resolve_archive_category(channel.guild)
    active_category = _resolve_active_ticket_category(channel.guild)

    if archive_category and _channel_is_in_category(channel, archive_category):
        return f"Archived in **{archive_category.name}**"
    if active_category and _channel_is_in_category(channel, active_category):
        return f"Active in **{active_category.name}**"
    if channel.category:
        return f"In **{channel.category.name}**"
    return "No category"


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
        staff_role_id = _safe_int(getattr(_g, "STAFF_ROLE_ID", 0), 0)
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


def _ticket_owner_value(owner: Optional[discord.Member | discord.User], guild: discord.Guild, row: Dict[str, Any]) -> str:
    if isinstance(owner, discord.Member):
        return f"{owner.mention}\n`{owner.id}`"
    fallback_id = _safe_int(row.get("owner_id") or row.get("user_id"), 0)
    if fallback_id > 0:
        member = guild.get_member(fallback_id)
        if member:
            return f"{member.mention}\n`{member.id}`"
        return f"`{fallback_id}`"
    return "Unknown"


def _note_lines(notes: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    lines: List[str] = []
    for note in notes[:limit]:
        preview = _truncate(note.get("note_body"), 120)
        author = _safe_str(note.get("author_name"), "unknown")
        pin_tag = "📌 " if bool(note.get("is_pinned")) else ""
        lines.append(f"{pin_tag}`{author}` — {preview}")
    return lines


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
        effectively_closed = _ticket_effectively_closed(channel=ch, row=row)

        if status == "deleted":
            return await reply_once(
                interaction,
                {"content": "❌ Cannot modify access on a deleted ticket.", "ephemeral": True},
            )

        if member.bot:
            return await reply_once(
                interaction,
                {"content": "❌ Adding bots to tickets this way is not supported.", "ephemeral": True},
            )

        if _member_is_ticket_owner(member, row):
            return await reply_once(
                interaction,
                {"content": "ℹ️ That member is already the ticket owner.", "ephemeral": True},
            )

        try:
            existing = ch.overwrites_for(member)
            overwrite = _build_member_overwrite(existing, can_view=True, can_send=(not effectively_closed))
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
            if effectively_closed:
                await ch.send(
                    f"➕ {member.mention} was added to the ticket by {interaction.user.mention}. "
                    f"They can view it, but the ticket is currently closed."
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

        if _ticket_effectively_closed(channel=ch, row=row):
            return await reply_once(
                interaction,
                {
                    "content": (
                        "ℹ️ This ticket is already closed/archived, so the owner should already be reply-locked.\n"
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

        if _ticket_effectively_closed(channel=ch, row=row):
            return await reply_once(
                interaction,
                {
                    "content": (
                        "❌ Closed/archived tickets should stay reply-locked.\n"
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

        row = row or {}
        embed = discord.Embed(
            title="🎫 Ticket Owner",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Channel", value=f"{ch.mention}\n`{ch.id}`", inline=False)
        embed.add_field(
            name="Owner",
            value=_ticket_owner_value(owner, ch.guild, row),
            inline=False,
        )
        embed.add_field(name="Status", value=f"`{_safe_str(row.get('status'), 'unknown')}`", inline=True)
        embed.add_field(name="Category", value=f"`{_safe_str(row.get('category'), 'unknown')}`", inline=True)
        embed.add_field(name="Location", value=_ticket_location_label(ch), inline=False)

        ticket_num = _ticket_number(row, ch)
        if ticket_num > 0:
            embed.add_field(name="Ticket Number", value=f"`{ticket_num}`", inline=True)

        matched = _safe_str(row.get("matched_category_name") or row.get("matched_category_slug"))
        if matched:
            embed.add_field(name="Matched Category", value=matched, inline=True)

        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    @tree.command(
        name="ticket_access",
        description="Show who currently has explicit access overrides on this ticket.",
    )
    @app_commands.describe(channel="Ticket channel to inspect (leave empty to use current channel)")
    async def ticket_access(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        row = row or {}
        owner_id = _safe_int(row.get("owner_id") or row.get("user_id"), 0)

        member_lines: List[str] = []
        role_lines: List[str] = []

        try:
            for target, overwrite in ch.overwrites.items():
                if isinstance(target, discord.Member):
                    bits: List[str] = []
                    if overwrite.view_channel is not None:
                        bits.append(f"view={overwrite.view_channel}")
                    if overwrite.send_messages is not None:
                        bits.append(f"send={overwrite.send_messages}")
                    if overwrite.attach_files is not None:
                        bits.append(f"files={overwrite.attach_files}")
                    if overwrite.embed_links is not None:
                        bits.append(f"embeds={overwrite.embed_links}")

                    prefix = "👑 " if int(target.id) == owner_id else "• "
                    member_lines.append(
                        f"{prefix}{target.mention} (`{target.id}`) — {', '.join(bits) if bits else 'custom overwrite'}"
                    )

                elif isinstance(target, discord.Role):
                    bits = []
                    if overwrite.view_channel is not None:
                        bits.append(f"view={overwrite.view_channel}")
                    if overwrite.send_messages is not None:
                        bits.append(f"send={overwrite.send_messages}")
                    if overwrite.attach_files is not None:
                        bits.append(f"files={overwrite.attach_files}")
                    if overwrite.embed_links is not None:
                        bits.append(f"embeds={overwrite.embed_links}")

                    role_lines.append(
                        f"• @{target.name} (`{target.id}`) — {', '.join(bits) if bits else 'custom overwrite'}"
                    )
        except Exception:
            pass

        embed = discord.Embed(
            title="🔐 Ticket Access",
            description=f"{ch.mention}\n{_ticket_location_label(ch)}",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )

        embed.add_field(
            name="Members",
            value=_truncate("\n".join(member_lines), 1024) if member_lines else "No explicit member overwrites found.",
            inline=False,
        )
        embed.add_field(
            name="Roles",
            value=_truncate("\n".join(role_lines), 1024) if role_lines else "No explicit role overwrites found.",
            inline=False,
        )

        await reply_once(interaction, {"embed": embed, "ephemeral": True})

    try:
        print("✅ commands_ext.ticket_channel_admin: registered ticket channel admin commands")
    except Exception:
        pass


__all__ = ["register_ticket_channel_admin_commands"]
