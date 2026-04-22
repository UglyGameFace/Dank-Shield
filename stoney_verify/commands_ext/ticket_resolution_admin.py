from __future__ import annotations

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
    safe_defer,
    mark_ticket_activity,
)

from .kick_timers import (
    _cancel_kick_timer,
    kick_timer_persist_delete,
)

try:
    from ..transcripts import send_tickettool_style_transcript
except Exception:
    async def send_tickettool_style_transcript(*args, **kwargs) -> None:  # type: ignore
        return None

try:
    from ..tickets_new.repository import (
        get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id,
    )
except Exception:
    async def repo_get_ticket_by_any_channel_id(channel_id: int | str):  # type: ignore
        return None

try:
    from ..tickets_new.transcript_service import (
        post_transcript_to_channel as transcript_post_to_channel,
    )
except Exception:
    transcript_post_to_channel = None  # type: ignore

try:
    from ..tickets_new.service import (
        reopen_ticket_channel as service_reopen_ticket_channel,
        mark_ticket_closed as service_mark_ticket_closed,
        add_internal_note as service_add_internal_note,
    )
except Exception:
    service_reopen_ticket_channel = None  # type: ignore
    service_mark_ticket_closed = None  # type: ignore
    service_add_internal_note = None  # type: ignore


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


def _truncate(text: Any, limit: int = 220) -> str:
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
            value = int(globals().get(key, 0) or 0)
            if value > 0:
                return value
        except Exception:
            continue
    return 0


def _ticket_active_category_id() -> int:
    try:
        return int(globals().get("TICKET_CATEGORY_ID", 0) or 0)
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


def _location_label(channel: discord.TextChannel) -> str:
    archive_category = _resolve_archive_category(channel.guild)
    active_category = _resolve_active_ticket_category(channel.guild)

    if archive_category and _channel_is_in_category(channel, archive_category):
        return f"Archived in **{archive_category.name}**"
    if active_category and _channel_is_in_category(channel, active_category):
        return f"Active in **{active_category.name}**"
    if channel.category:
        return f"In **{channel.category.name}**"
    return "No category"


async def _move_ticket_to_archive_if_configured(channel: discord.TextChannel) -> bool:
    archive_category = _resolve_archive_category(channel.guild)
    if archive_category is None:
        return False

    if _channel_is_in_category(channel, archive_category):
        return True

    try:
        await channel.edit(
            category=archive_category,
            sync_permissions=False,
            reason="Ticket resolved -> move to archive category",
        )
        return True
    except Exception:
        return False


async def _move_ticket_to_active_if_configured(channel: discord.TextChannel) -> bool:
    active_category = _resolve_active_ticket_category(channel.guild)
    if active_category is None:
        return False

    if _channel_is_in_category(channel, active_category):
        return True

    try:
        await channel.edit(
            category=active_category,
            sync_permissions=False,
            reason="Ticket reopened -> move back to active ticket category",
        )
        return True
    except Exception:
        return False


async def _ticket_row_for_channel(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    try:
        row = await repo_get_ticket_by_any_channel_id(int(channel.id))
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        return None


def _is_ticket_channel(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> bool:
    if isinstance(row, dict):
        return True
    try:
        return bool(is_verification_ticket_channel(channel))
    except Exception:
        return False


async def _send_ephemeral(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    *,
    embed: Optional[discord.Embed] = None,
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=True)
    except Exception:
        pass


async def _ensure_ticket_context(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
) -> Tuple[Optional[discord.TextChannel], Optional[Dict[str, Any]]]:
    ch = channel or interaction.channel
    if not isinstance(ch, discord.TextChannel):
        await _send_ephemeral(interaction, "❌ Must be used in a ticket text channel.")
        return None, None

    row = await _ticket_row_for_channel(ch)
    if not _is_ticket_channel(ch, row):
        await _send_ephemeral(
            interaction,
            f"❌ `{ch.name}` is not recognized as a ticket channel.",
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


async def _ticket_assignee(channel: discord.TextChannel, row: Optional[Dict[str, Any]]) -> Optional[discord.Member]:
    try:
        assigned_to = _safe_int((row or {}).get("assigned_to") or (row or {}).get("claimed_by"), 0)
        if assigned_to <= 0:
            return None
        member = channel.guild.get_member(assigned_to)
        if member:
            return member
        try:
            return await channel.guild.fetch_member(assigned_to)
        except Exception:
            return None
    except Exception:
        return None


def _actor_member(guild: Optional[discord.Guild], user: discord.abc.User) -> Optional[discord.Member]:
    if guild is None:
        return None
    try:
        member = guild.get_member(int(user.id))
        if member:
            return member
    except Exception:
        pass
    return None


async def _refresh_ticket_row(channel: discord.TextChannel) -> Optional[Dict[str, Any]]:
    return await _ticket_row_for_channel(channel)


def _transcript_url_from_row(
    row: Optional[Dict[str, Any]],
    *,
    guild_id: int,
) -> Optional[str]:
    if not isinstance(row, dict):
        return None

    direct = _safe_str(row.get("transcript_url"))
    if direct:
        return direct

    transcript_channel_id = _safe_int(row.get("transcript_channel_id"), 0)
    transcript_message_id = _safe_int(row.get("transcript_message_id"), 0)
    if transcript_channel_id > 0 and transcript_message_id > 0:
        return f"https://discord.com/channels/{guild_id}/{transcript_channel_id}/{transcript_message_id}"

    return None


def _ticket_has_transcript(row: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(row, dict):
        return False
    return bool(
        _safe_str(row.get("transcript_url"))
        or _safe_str(row.get("transcript_message_id"))
        or _safe_str(row.get("transcript_channel_id"))
    )


async def _repair_closed_drift_for_resolve(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
    actor: Optional[discord.Member | discord.User],
) -> Optional[Dict[str, Any]]:
    """
    Older broken flows can leave the channel visibly closed/archive-moved while
    the DB still says open/claimed. Resolve should treat that as already closed.
    """
    status = _ticket_status(row)
    if status == "deleted":
        return row

    if _ticket_effectively_closed(channel=channel, row=row) and status in {"open", "claimed", "unknown"}:
        if service_mark_ticket_closed is None:
            return row
        try:
            await service_mark_ticket_closed(
                channel=channel,
                closed_by=actor,
                reason="State repaired before resolve",
            )
        except Exception:
            return row
        return await _refresh_ticket_row(channel)

    return row


async def _repair_open_drift_for_reopen(
    *,
    channel: discord.TextChannel,
    row: Optional[Dict[str, Any]],
    actor: Optional[discord.Member | discord.User],
) -> Optional[Dict[str, Any]]:
    """
    Older broken flows can leave the channel visibly active while the DB still
    says closed. Reopen should not pretend it is still closed.
    """
    status = _ticket_status(row)
    if status == "deleted":
        return row

    if _ticket_effectively_open(channel=channel, row=row) and status == "closed":
        if service_reopen_ticket_channel is None:
            return row

        owner = await _ticket_owner(channel, row)
        owner_member = owner if isinstance(owner, discord.Member) else None

        try:
            await service_reopen_ticket_channel(
                channel=channel,
                owner=owner_member,
                actor=actor,
                reason="State repaired before reopen check",
            )
        except Exception:
            return row

        return await _refresh_ticket_row(channel)

    return row


async def _post_ticket_transcript(
    *,
    channel: discord.TextChannel,
    owner: Optional[discord.Member | discord.User],
    actor: Optional[discord.Member | discord.User],
    reason: str,
    row: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    existing_url = _transcript_url_from_row(row, guild_id=channel.guild.id)
    if existing_url:
        return True, existing_url

    if callable(transcript_post_to_channel):
        try:
            _msg, jump_url = await transcript_post_to_channel(
                ticket_channel=channel,
                deleted_by=actor,
                reason=reason,
            )
            if jump_url:
                return True, jump_url
            refreshed = await _refresh_ticket_row(channel)
            return True, _transcript_url_from_row(refreshed, guild_id=channel.guild.id)
        except Exception:
            pass

    try:
        await send_tickettool_style_transcript(
            channel,
            owner if isinstance(owner, discord.Member) else None,
            closed_by=actor,
            decision=reason,
        )
        refreshed = await _refresh_ticket_row(channel)
        return True, _transcript_url_from_row(refreshed, guild_id=channel.guild.id)
    except Exception:
        return False, None


async def _add_resolution_note(channel_id: int, author: discord.abc.User, note: str) -> None:
    if service_add_internal_note is None:
        return
    try:
        await service_add_internal_note(
            channel_id=channel_id,
            author=author,
            note=note,
            is_pinned=False,
        )
    except Exception:
        pass


async def _cancel_ticket_kick_timer_if_any(channel_id: int) -> None:
    try:
        _cancel_kick_timer(channel_id)
    except Exception:
        pass

    try:
        await kick_timer_persist_delete(int(channel_id))
    except Exception:
        pass


def register_ticket_resolution_admin_commands(bot, tree) -> None:
    @tree.command(
        name="ticket_resolve",
        description="Resolve a ticket with a required reason, transcript, and close.",
    )
    @app_commands.describe(
        reason="Required closure reason",
        channel="Ticket channel to resolve (leave empty to use current channel)",
    )
    async def ticket_resolve(
        interaction: discord.Interaction,
        reason: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        clean_reason = _safe_str(reason)
        if not clean_reason:
            return await interaction.response.send_message(
                "❌ A close reason is required.",
                ephemeral=True,
            )

        await safe_defer(interaction, ephemeral=True)

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_mark_ticket_closed is None:
            return await interaction.followup.send(
                "❌ Ticket close service is unavailable.",
                ephemeral=True,
            )

        row = await _repair_closed_drift_for_resolve(
            channel=ch,
            row=row,
            actor=interaction.user,
        )
        status = _ticket_status(row)

        if status == "deleted":
            return await interaction.followup.send(
                "❌ This ticket is already marked deleted and cannot be resolved.",
                ephemeral=True,
            )

        if _ticket_effectively_closed(channel=ch, row=row):
            archive_note = ""
            if _channel_is_in_archive_category(ch):
                archive_note = f"\n📦 {_location_label(ch)}"
            return await interaction.followup.send(
                f"ℹ️ {ch.mention} is already closed.{archive_note}",
                ephemeral=True,
            )

        owner = await _ticket_owner(ch, row)
        actor_member = _actor_member(interaction.guild, interaction.user) or interaction.user

        await _cancel_ticket_kick_timer_if_any(ch.id)

        try:
            await _add_resolution_note(
                ch.id,
                interaction.user,
                f"Resolution note: {clean_reason}",
            )
        except Exception:
            pass

        transcript_ok, transcript_url = await _post_ticket_transcript(
            channel=ch,
            owner=owner,
            actor=actor_member,
            reason=clean_reason,
            row=row,
        )

        if not transcript_ok:
            return await interaction.followup.send(
                "❌ Transcript generation failed, so the ticket was not resolved yet.",
                ephemeral=True,
            )

        try:
            ok = await service_mark_ticket_closed(
                channel=ch,
                closed_by=actor_member,
                reason=clean_reason,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Failed resolving ticket state: `{e}`",
                ephemeral=True,
            )

        if not ok:
            return await interaction.followup.send(
                "❌ Failed to resolve this ticket.",
                ephemeral=True,
            )

        moved_to_archive = await _move_ticket_to_archive_if_configured(ch)

        try:
            lines = [
                f"✅ Ticket resolved by {interaction.user.mention}.",
                f"**Reason:** {clean_reason}",
                "The ticket owner can still view this ticket, but they cannot reply while it is resolved/closed.",
                "Use `/ticket_reopen_reason` if this needs to be reopened.",
            ]
            if moved_to_archive and ch.category is not None:
                lines.append(f"📦 Moved to archive category: **{ch.category.name}**")
            await ch.send("\n".join(lines))
        except Exception:
            pass

        try:
            mark_ticket_activity(ch.id)
        except Exception:
            pass

        msg_lines = [f"✅ Resolved {ch.mention}."]
        if moved_to_archive and ch.category is not None:
            msg_lines.append(f"📦 Moved to archive category: **{ch.category.name}**")
        elif _resolve_archive_category(ch.guild) is None:
            msg_lines.append("ℹ️ No archive category is configured, so the ticket stayed in its current category.")

        if transcript_url:
            msg_lines.append(f"🧾 Transcript: {transcript_url}")
        else:
            msg_lines.append("🧾 Transcript was generated, but no direct jump link was returned.")

        await interaction.followup.send("\n".join(msg_lines), ephemeral=True)

    @tree.command(
        name="ticket_reopen_reason",
        description="Reopen a ticket with a required reason.",
    )
    @app_commands.describe(
        reason="Required reopen reason",
        channel="Ticket channel to reopen (leave empty to use current channel)",
    )
    async def ticket_reopen_reason(
        interaction: discord.Interaction,
        reason: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        clean_reason = _safe_str(reason)
        if not clean_reason:
            return await interaction.response.send_message(
                "❌ A reopen reason is required.",
                ephemeral=True,
            )

        await safe_defer(interaction, ephemeral=True)

        ch, row = await _ensure_ticket_context(interaction, channel)
        if ch is None:
            return

        if service_reopen_ticket_channel is None:
            return await interaction.followup.send(
                "❌ Ticket reopen service is unavailable.",
                ephemeral=True,
            )

        row = await _repair_open_drift_for_reopen(
            channel=ch,
            row=row,
            actor=interaction.user,
        )
        status = _ticket_status(row)

        if status == "deleted":
            return await interaction.followup.send(
                "❌ Deleted tickets cannot be reopened.",
                ephemeral=True,
            )

        if _ticket_effectively_open(channel=ch, row=row) and status in {"open", "claimed"}:
            active_note = ""
            if _channel_is_in_active_category(ch):
                active_note = f"\n📂 {_location_label(ch)}"
            return await interaction.followup.send(
                f"ℹ️ {ch.mention} is already open.{active_note}",
                ephemeral=True,
            )

        owner = await _ticket_owner(ch, row)
        owner_member = owner if isinstance(owner, discord.Member) else None

        ok = await service_reopen_ticket_channel(
            channel=ch,
            owner=owner_member,
            actor=interaction.user,
            reason=clean_reason,
        )
        if not ok:
            return await interaction.followup.send(
                "❌ Failed to reopen this ticket.",
                ephemeral=True,
            )

        moved_to_active = await _move_ticket_to_active_if_configured(ch)

        try:
            await _add_resolution_note(
                ch.id,
                interaction.user,
                f"Reopen reason: {clean_reason}",
            )
        except Exception:
            pass

        try:
            lines = [
                f"♻️ Ticket reopened by {interaction.user.mention}.",
                f"**Reason:** {clean_reason}",
                "The ticket owner can reply again.",
            ]
            if moved_to_active and ch.category is not None:
                lines.append(f"📂 Moved back to active ticket category: **{ch.category.name}**")
            await ch.send("\n".join(lines))
        except Exception:
            pass

        try:
            mark_ticket_activity(ch.id)
        except Exception:
            pass

        msg_lines = [f"✅ Reopened {ch.mention}."]
        if moved_to_active and ch.category is not None:
            msg_lines.append(f"📂 Moved back to active ticket category: **{ch.category.name}**")
        elif _resolve_active_ticket_category(ch.guild) is None:
            msg_lines.append("ℹ️ No active ticket category is configured, so the ticket stayed in its current category.")
        await interaction.followup.send("\n".join(msg_lines), ephemeral=True)

    @tree.command(
        name="ticket_nudge_owner",
        description="Ping the ticket owner with an optional message.",
    )
    @app_commands.describe(
        message="Optional nudge message",
        channel="Ticket channel to use (leave empty to use current channel)",
    )
    async def ticket_nudge_owner(
        interaction: discord.Interaction,
        message: Optional[str] = None,
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
                {"content": "❌ Cannot nudge the owner of a deleted ticket.", "ephemeral": True},
            )

        owner = await _ticket_owner(ch, row)
        if owner is None:
            return await reply_once(interaction, {"content": "❌ Could not resolve the ticket owner.", "ephemeral": True})

        body = _safe_str(message, "Staff is waiting on your response.")
        if _ticket_effectively_closed(channel=ch, row=row):
            body = f"{body}\n\nℹ️ This ticket is currently closed, so this is just an informational ping until it is reopened."

        try:
            await ch.send(f"{getattr(owner, 'mention', '')} ⏰ {body}")
            mark_ticket_activity(ch.id)
        except Exception as e:
            return await reply_once(interaction, {"content": f"❌ Failed nudging owner: {e}", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Nudged the ticket owner in {ch.mention}.", "ephemeral": True})

    @tree.command(
        name="ticket_nudge_assignee",
        description="Ping the assigned staff member with an optional message.",
    )
    @app_commands.describe(
        message="Optional nudge message",
        channel="Ticket channel to use (leave empty to use current channel)",
    )
    async def ticket_nudge_assignee(
        interaction: discord.Interaction,
        message: Optional[str] = None,
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
                {"content": "❌ Deleted tickets cannot nudge an assignee.", "ephemeral": True},
            )

        if not _ticket_effectively_open(channel=ch, row=row):
            return await reply_once(
                interaction,
                {"content": "❌ Only active tickets can nudge an assignee.", "ephemeral": True},
            )

        assignee = await _ticket_assignee(ch, row)
        if assignee is None:
            return await reply_once(interaction, {"content": "❌ This ticket is currently unassigned.", "ephemeral": True})

        body = _safe_str(message, "This ticket needs staff attention.")
        try:
            await ch.send(f"{assignee.mention} 🔔 {body}")
            mark_ticket_activity(ch.id)
        except Exception as e:
            return await reply_once(interaction, {"content": f"❌ Failed nudging assignee: {e}", "ephemeral": True})

        await reply_once(interaction, {"content": f"✅ Nudged the assigned staff member in {ch.mention}.", "ephemeral": True})
