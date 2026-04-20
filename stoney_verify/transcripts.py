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
from .verify_ui import (
    post_or_replace_verify_ui,
    VERIFY_UI_TITLE,
    VERIFY_UI_FOOTER,
)

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

try:
    from .tickets_new.repository import get_ticket_by_any_channel_id
except Exception:
    async def get_ticket_by_any_channel_id(channel_id: int | str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None


# ============================================================
# Lazy service imports
# ============================================================

async def _approve_verification_service(*args, **kwargs) -> Dict[str, Any]:
    try:
        from .verification_new.service import approve_verification as _approve_verification
        return await _approve_verification(*args, **kwargs)
    except Exception as e:
        return {
            "ok": False,
            "message": f"verification_new.service import failed: {e}",
        }


async def _deny_verification_service(*args, **kwargs) -> Dict[str, Any]:
    try:
        from .verification_new.service import deny_verification as _deny_verification
        return await _deny_verification(*args, **kwargs)
    except Exception as e:
        return {
            "ok": False,
            "message": f"verification_new.service import failed: {e}",
        }


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

_CLOSE_PROMPT_MARKER = "stoney_verify:close_prompt:v8"
_STAFF_CLOSED_MARKER = "stoney_verify:staff_closed:v8"
_CLOSE_REOPENED_MARKER = "stoney_verify:ticket_reopened:v7"
_STAFF_REVIEW_PANEL_MARKER = "stoney_verify:staff_review_panel:v5"
_TRANSCRIPT_POSTED_MARKER = "stoney_verify:transcript_posted:v4"
_OPEN_CONTROLS_MARKER = "stoney_verify:open_controls:v4"

_CLOSE_PROMPT_LOCKS: Dict[int, asyncio.Lock] = {}
_STAFF_REVIEW_PANEL_LOCKS: Dict[int, asyncio.Lock] = {}
_STAFF_CLOSED_LOCKS: Dict[int, asyncio.Lock] = {}
_TRANSCRIPT_POST_LOCKS: Dict[int, asyncio.Lock] = {}
_CLOSE_ACTION_LOCKS: Dict[int, asyncio.Lock] = {}
_REOPEN_ACTION_LOCKS: Dict[int, asyncio.Lock] = {}
_DELETE_ACTION_LOCKS: Dict[int, asyncio.Lock] = {}
_OPEN_CONTROLS_LOCKS: Dict[int, asyncio.Lock] = {}

_TRANSCRIPT_VIEWS_REGISTERED = False


def _lock_for(container: Dict[int, asyncio.Lock], channel_id: int) -> asyncio.Lock:
    cid = int(channel_id)
    lock = container.get(cid)
    if lock is None:
        lock = container[cid] = asyncio.Lock()
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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
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


def _role_by_id(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    try:
        if not guild or not role_id or int(role_id) <= 0:
            return None
        role = guild.get_role(int(role_id))
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


async def _remove_unverified_role_if_present(
    member: Optional[discord.Member],
    *,
    reason: str,
) -> Tuple[bool, Optional[str]]:
    try:
        if not isinstance(member, discord.Member):
            return False, None

        unverified_role = _role_by_id(member.guild, int(UNVERIFIED_ROLE_ID or 0))
        if not unverified_role or unverified_role not in member.roles:
            return False, None

        await member.remove_roles(unverified_role, reason=reason)
        return True, None
    except discord.Forbidden:
        return False, "I can't remove the Unverified role. Check role hierarchy and Manage Roles."
    except Exception as e:
        return False, str(e)


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


async def _ticket_row(channel_id: int | str) -> Optional[Dict[str, Any]]:
    try:
        row = await get_ticket_by_any_channel_id(channel_id)
        if isinstance(row, dict):
            return row
    except Exception:
        pass
    return None


def _row_status(row: Optional[Dict[str, Any]]) -> str:
    try:
        return str((row or {}).get("status") or "").strip().lower()
    except Exception:
        return ""


def _row_is_closed(row: Optional[Dict[str, Any]]) -> bool:
    return _row_status(row) == "closed"


def _row_is_deleted(row: Optional[Dict[str, Any]]) -> bool:
    return _row_status(row) == "deleted"


def _channel_looks_closed(channel: discord.TextChannel) -> bool:
    try:
        return str(channel.name or "").lower().startswith("closed-")
    except Exception:
        return False


async def _ticket_is_closed(channel: discord.TextChannel) -> bool:
    try:
        row = await _ticket_row(channel.id)
        if row and _row_is_closed(row):
            return True
        if _channel_looks_closed(channel):
            return True
    except Exception:
        pass
    return False


async def _ticket_is_deleted(channel: discord.TextChannel) -> bool:
    try:
        row = await _ticket_row(channel.id)
        if row and _row_is_deleted(row):
            return True
    except Exception:
        pass
    return False


async def _ticket_is_open_like(channel: discord.TextChannel) -> bool:
    try:
        row = await _ticket_row(channel.id)
        if row and _row_status(row) in {"open", "claimed"} and not _channel_looks_closed(channel):
            return True
    except Exception:
        pass
    return False


async def _ticket_has_transcript(channel_id: int | str) -> bool:
    try:
        row = await _ticket_row(channel_id)
        if not row:
            return False
        return bool(
            str(row.get("transcript_url") or "").strip()
            or str(row.get("transcript_message_id") or "").strip()
            or str(row.get("transcript_channel_id") or "").strip()
        )
    except Exception:
        return False


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


def _staff_display_name(user: discord.abc.User) -> str:
    try:
        return (
            getattr(user, "display_name", None)
            or getattr(user, "name", None)
            or str(user)
        )
    except Exception:
        return "Staff"


def _message_has_marker_or_custom_ids(
    message: discord.Message,
    *,
    marker: str = "",
    custom_ids: Optional[set[str]] = None,
) -> bool:
    try:
        if marker:
            content = str(getattr(message, "content", "") or "")
            if marker in content:
                return True
    except Exception:
        pass

    try:
        embeds = getattr(message, "embeds", None) or []
        for e in embeds:
            footer_text = str(getattr(getattr(e, "footer", None), "text", "") or "")
            if marker and marker in footer_text:
                return True
    except Exception:
        pass

    try:
        wanted = set(custom_ids or set())
        if wanted:
            comps = getattr(message, "components", None) or []
            for row in comps:
                for child in (getattr(row, "children", None) or []):
                    cid = str(getattr(child, "custom_id", "") or "")
                    if cid in wanted:
                        return True
    except Exception:
        pass

    return False


async def _find_bot_control_messages(
    channel: discord.TextChannel,
    *,
    marker: str = "",
    custom_ids: Optional[set[str]] = None,
    limit: int = 80,
) -> List[discord.Message]:
    messages: List[discord.Message] = []
    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
        async for msg in channel.history(limit=limit):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            if _message_has_marker_or_custom_ids(msg, marker=marker, custom_ids=custom_ids):
                messages.append(msg)
    except Exception:
        pass
    return messages


async def _cleanup_duplicate_control_messages(
    messages: List[discord.Message],
    *,
    keep_message_id: Optional[int] = None,
    suffix: Optional[str] = None,
) -> None:
    for msg in list(messages):
        try:
            if keep_message_id is not None and int(msg.id) == int(keep_message_id):
                continue
            await _freeze_message_controls(msg, content_suffix=suffix)
        except Exception:
            continue


def _disabled_view_from_existing_message(message: Optional[discord.Message]) -> Optional[discord.ui.View]:
    if message is None:
        return None

    try:
        comps = getattr(message, "components", None) or []
        if not comps:
            return None

        view = discord.ui.View(timeout=None)

        for row in comps:
            for child in (getattr(row, "children", None) or []):
                try:
                    style = getattr(child, "style", discord.ButtonStyle.secondary)
                    label = getattr(child, "label", None)
                    emoji = getattr(child, "emoji", None)
                    url = getattr(child, "url", None)
                    custom_id = getattr(child, "custom_id", None)

                    if url:
                        item = discord.ui.Button(
                            label=label,
                            style=discord.ButtonStyle.link,
                            emoji=emoji,
                            url=url,
                            row=getattr(child, "row", None),
                            disabled=True,
                        )
                    else:
                        item = discord.ui.Button(
                            label=label,
                            style=style,
                            emoji=emoji,
                            custom_id=custom_id,
                            row=getattr(child, "row", None),
                            disabled=True,
                        )
                    view.add_item(item)
                except Exception:
                    continue

        return view
    except Exception:
        return None


async def _freeze_message_controls(
    message: Optional[discord.Message],
    *,
    content_suffix: Optional[str] = None,
) -> None:
    if message is None:
        return

    try:
        content = message.content or ""
        if content_suffix:
            if content_suffix not in content:
                content = f"{content}\n{content_suffix}" if content else content_suffix

        frozen_view = _disabled_view_from_existing_message(message)
        await message.edit(content=content, view=frozen_view)
    except Exception:
        try:
            await message.edit(view=None)
        except Exception:
            pass


async def _detect_is_ghost_ticket(channel: discord.TextChannel) -> bool:
    try:
        row = await _ticket_row(channel.id)
        if row:
            return _safe_bool(row.get("is_ghost"), False)
    except Exception:
        pass

    try:
        return "ghost" in str(channel.name or "").lower()
    except Exception:
        return False


# ============================================================
# Close prompt / open controls / staff closed panel helpers
# ============================================================

async def _find_open_controls_message(channel: discord.TextChannel, limit: int = 80) -> Optional[discord.Message]:
    messages = await _find_bot_control_messages(
        channel,
        marker=_OPEN_CONTROLS_MARKER,
        custom_ids={"sv:ticket:close", "sv:ticket:delete_open"},
        limit=limit,
    )
    return messages[0] if messages else None


async def _freeze_open_controls_message(
    channel: discord.TextChannel,
    *,
    closed_by: Optional[discord.abc.User] = None,
) -> None:
    try:
        msg = await _find_open_controls_message(channel)
        if not msg:
            return

        suffix = "🔒 Ticket closed."
        if closed_by is not None:
            try:
                suffix = f"🔒 Closed by {closed_by.mention}."
            except Exception:
                suffix = "🔒 Ticket closed."

        await _freeze_message_controls(msg, content_suffix=suffix)
    except Exception:
        pass


async def _has_staff_closed_message(channel: discord.TextChannel, limit: int = 80) -> bool:
    return (await _find_staff_closed_message(channel, limit=limit)) is not None


async def _find_staff_closed_message(channel: discord.TextChannel, limit: int = 80) -> Optional[discord.Message]:
    messages = await _find_bot_control_messages(
        channel,
        marker=_STAFF_CLOSED_MARKER,
        custom_ids={"sv:ticket:reopen", "sv:ticket:transcript", "sv:ticket:delete"},
        limit=limit,
    )
    return messages[0] if messages else None


async def _post_staff_closed_message(channel: discord.TextChannel, closed_by: discord.abc.User) -> None:
    lock = _lock_for(_STAFF_CLOSED_LOCKS, channel.id)
    async with lock:
        embed = discord.Embed(
            title="🔒 Ticket Closed",
            description=(
                f"This ticket was closed by {closed_by.mention}.\n"
                "The ticket owner is now locked from replying until staff reopens it or deletes it."
            ),
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )
        embed.set_footer(text=_STAFF_CLOSED_MARKER)
        view = StaffClosedTicketView()

        existing_messages = await _find_bot_control_messages(
            channel,
            marker=_STAFF_CLOSED_MARKER,
            custom_ids={"sv:ticket:reopen", "sv:ticket:transcript", "sv:ticket:delete"},
            limit=80,
        )

        if existing_messages:
            latest = existing_messages[0]
            try:
                await latest.edit(embed=embed, view=view, content=_STAFF_CLOSED_MARKER)
                await _cleanup_duplicate_control_messages(
                    existing_messages,
                    keep_message_id=latest.id,
                    suffix="🔒 Replaced by latest closed-ticket controls.",
                )
                return
            except Exception:
                pass

        await channel.send(content=_STAFF_CLOSED_MARKER, embed=embed, view=view)


async def _find_existing_close_prompt(channel: discord.TextChannel, limit: int = 80) -> Optional[discord.Message]:
    messages = await _find_bot_control_messages(
        channel,
        marker=_CLOSE_PROMPT_MARKER,
        custom_ids={"sv:ticket:confirm_close", "sv:ticket:cancel_close"},
        limit=limit,
    )
    return messages[0] if messages else None


async def _freeze_all_close_prompts(
    channel: discord.TextChannel,
    *,
    suffix: str,
) -> None:
    messages = await _find_bot_control_messages(
        channel,
        marker=_CLOSE_PROMPT_MARKER,
        custom_ids={"sv:ticket:confirm_close", "sv:ticket:cancel_close"},
        limit=80,
    )
    for msg in messages:
        try:
            await _freeze_message_controls(msg, content_suffix=suffix)
        except Exception:
            continue


async def prompt_ticket_close_confirmation(
    channel: discord.TextChannel,
    requested_by: Optional[discord.Member] = None,
) -> Optional[discord.Message]:
    lock = _lock_for(_CLOSE_PROMPT_LOCKS, channel.id)
    async with lock:
        if await _ticket_is_deleted(channel):
            return None

        if await _ticket_is_closed(channel):
            return await _find_staff_closed_message(channel)

        actor = requested_by.mention if requested_by else "the ticket owner"
        content = (
            f"⚠️ {actor} requested to close this ticket.\n"
            "Please confirm below.\n"
            f"{_CLOSE_PROMPT_MARKER}"
        )
        view = ConfirmCloseTicketView()

        existing_messages = await _find_bot_control_messages(
            channel,
            marker=_CLOSE_PROMPT_MARKER,
            custom_ids={"sv:ticket:confirm_close", "sv:ticket:cancel_close"},
            limit=80,
        )

        if existing_messages:
            latest = existing_messages[0]
            try:
                await latest.edit(content=content, view=view)
                await _cleanup_duplicate_control_messages(
                    existing_messages,
                    keep_message_id=latest.id,
                    suffix="ℹ️ Replaced by latest close confirmation.",
                )
                return latest
            except Exception:
                pass

        try:
            return await channel.send(content, view=view)
        except Exception as e:
            print("⚠️ Failed to post close confirmation:", e)
            return None


async def post_or_replace_open_ticket_controls(
    channel: discord.TextChannel,
) -> Optional[discord.Message]:
    lock = _lock_for(_OPEN_CONTROLS_LOCKS, channel.id)
    async with lock:
        if await _ticket_is_deleted(channel):
            return None

        if await _ticket_is_closed(channel):
            return None

        owner = await _resolve_ticket_owner(channel)

        embed = discord.Embed(
            title="🟢 Ticket Open",
            description=(
                f"{owner.mention if isinstance(owner, discord.Member) else 'The ticket owner'} can close this ticket when finished.\n"
                "Staff can also close it or delete it."
            ),
            color=discord.Color.green(),
            timestamp=now_utc(),
        )
        embed.set_footer(text=_OPEN_CONTROLS_MARKER)

        view = TicketOpenActionsView()
        existing_messages = await _find_bot_control_messages(
            channel,
            marker=_OPEN_CONTROLS_MARKER,
            custom_ids={"sv:ticket:close", "sv:ticket:delete_open"},
            limit=80,
        )

        if existing_messages:
            latest = existing_messages[0]
            try:
                await latest.edit(embed=embed, view=view, content=_OPEN_CONTROLS_MARKER)
                await _cleanup_duplicate_control_messages(
                    existing_messages,
                    keep_message_id=latest.id,
                    suffix="ℹ️ Replaced by latest open-ticket controls.",
                )
                return latest
            except Exception:
                pass

        try:
            return await channel.send(
                content=_OPEN_CONTROLS_MARKER,
                embed=embed,
                view=view,
            )
        except Exception as e:
            print("⚠️ Failed to post open ticket controls:", e)
            return None


async def _ensure_open_ticket_controls_after_reopen(
    channel: discord.TextChannel,
    *,
    attempts: int = 3,
) -> Optional[discord.Message]:
    last_msg: Optional[discord.Message] = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            if await _ticket_is_deleted(channel):
                return None
            if await _ticket_is_closed(channel):
                await asyncio.sleep(0.45 * attempt)
                continue

            last_msg = await post_or_replace_open_ticket_controls(channel)
            if last_msg is not None:
                return last_msg
        except Exception:
            pass

        await asyncio.sleep(0.45 * attempt)

    return last_msg


# ============================================================
# Transcript wrapper helpers
# ============================================================

async def build_html_transcript(
    channel: discord.TextChannel,
    limit: int = 2000,
) -> Tuple[str, Dict[str, int], Dict[int, int], Dict[int, int]]:
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
    _ = owner
    _ = owner_id

    lock = _lock_for(_TRANSCRIPT_POST_LOCKS, channel.id)

    async with lock:
        try:
            if await _ticket_has_transcript(channel.id):
                return

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

            try:
                await channel.send(
                    f"🧾 Transcript has been posted.\n{_TRANSCRIPT_POSTED_MARKER}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                pass

        except Exception as e:
            print("⚠️ send_tickettool_style_transcript wrapper failed:", repr(e))


async def auto_close_after_decision(
    channel: discord.TextChannel,
    closer: Optional[discord.Member] = None,
    decision: Optional[str] = None,
):
    try:
        if int(AUTO_DELETE_TICKET_SECONDS or 0) <= 0:
            return
    except Exception:
        return

    try:
        await channel.send(
            f"🕒 Decision made. Ticket will auto-close in **{AUTO_DELETE_TICKET_SECONDS} seconds**."
        )
        await asyncio.sleep(int(AUTO_DELETE_TICKET_SECONDS))

        try:
            fresh_channel = channel.guild.get_channel(channel.id)
            if not isinstance(fresh_channel, discord.TextChannel):
                try:
                    fetched = await channel.guild.fetch_channel(channel.id)
                    if isinstance(fetched, discord.TextChannel):
                        fresh_channel = fetched
                    else:
                        return
                except Exception:
                    return
            channel = fresh_channel
        except Exception:
            return

        is_ghost = False
        try:
            row = await _ticket_row(channel.id)
            if row:
                is_ghost = _safe_bool(row.get("is_ghost"), False)
            else:
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
    ) -> tuple[Optional[str], Optional[int], Optional[str], Optional[discord.Member], Optional[discord.TextChannel]]:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return (None, None, None, None, None)

        ticket_id: Optional[str] = None
        member_id: Optional[int] = None
        member_name: Optional[str] = None
        member_obj: Optional[discord.Member] = None

        try:
            row = await _ticket_row(channel.id)
            if row:
                ticket_id = str(row.get("id") or "").strip() or None
                member_id = _safe_int(row.get("user_id") or row.get("owner_id") or row.get("requester_id"), 0) or None
                member_name = (
                    str(row.get("username") or "").strip()
                    or str(row.get("owner_name") or "").strip()
                    or str(row.get("requester_name") or "").strip()
                    or None
                )
        except Exception:
            pass

        if member_id is not None:
            try:
                member_obj = channel.guild.get_member(member_id)
                if member_obj is None:
                    member_obj = await channel.guild.fetch_member(member_id)
            except Exception:
                member_obj = None

        if member_obj is None:
            try:
                owner = await _resolve_ticket_owner(channel)
                if owner:
                    member_obj = owner
                    member_id = int(owner.id)
                    member_name = member_name or str(owner)
            except Exception:
                pass

        return (ticket_id, member_id, member_name, member_obj, channel)

    async def _disable_panel_after_action(
        self,
        interaction: discord.Interaction,
        *,
        status_line: str,
    ) -> None:
        await _freeze_message_controls(
            interaction.message,
            content_suffix=status_line,
        )

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
        _ = button
        if not await self._ensure_staff(interaction):
            return

        ticket_id, member_id, member_name, member_obj, channel = await self._resolve_ticket_context(interaction)
        _ = ticket_id
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")
        if member_id is None:
            return await _reply_ephemeral(interaction, "❌ Could not resolve the ticket member.")

        result = await _approve_verification_service(
            guild=channel.guild,
            channel=channel,
            token="",
            staff_member=interaction.user,
            decision_text="APPROVED",
            close_after=False,
            owner=member_obj,
        )

        if result.get("already_verified"):
            await self._disable_panel_after_action(
                interaction,
                status_line="✅ Member already appears verified. Duplicate approval blocked.",
            )
            return await _reply_ephemeral(
                interaction,
                "✅ This member already appears verified. Duplicate approval was blocked.",
            )

        if not result.get("ok"):
            return await _reply_ephemeral(
                interaction,
                f"❌ {result.get('message') or 'Approval failed.'}",
            )

        try:
            target = member_obj.mention if isinstance(member_obj, discord.Member) else (f"<@{member_id}>" if member_id else f"`{member_name or 'member'}`")
            await channel.send(
                f"✅ {target} was approved by **{_staff_display_name(interaction.user)}**.\n"
                "Reason: Approved from Discord staff review panel."
            )
        except Exception:
            pass

        await self._disable_panel_after_action(
            interaction,
            status_line=f"✅ Approved by {interaction.user.mention}.",
        )
        await _reply_ephemeral(interaction, "✅ Member approved.")

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
        _ = button
        if not await self._ensure_staff(interaction):
            return

        ticket_id, member_id, member_name, member_obj, channel = await self._resolve_ticket_context(interaction)
        _ = ticket_id
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")
        if member_id is None:
            return await _reply_ephemeral(interaction, "❌ Could not resolve the ticket member.")

        result = await _deny_verification_service(
            guild=channel.guild,
            channel=channel,
            token="",
            staff_member=interaction.user,
            decision_text="DENIED",
            close_after=False,
        )

        if not result.get("ok"):
            return await _reply_ephemeral(
                interaction,
                f"❌ {result.get('message') or 'Denial failed.'}",
            )

        try:
            target = member_obj.mention if isinstance(member_obj, discord.Member) else (f"<@{member_id}>" if member_id else f"`{member_name or 'member'}`")
            await channel.send(
                f"❌ Verification denied for {target} by **{_staff_display_name(interaction.user)}**.\n"
                "Reason: Denied from Discord staff review panel."
            )
        except Exception:
            pass

        await self._disable_panel_after_action(
            interaction,
            status_line=f"❌ Denied by {interaction.user.mention}.",
        )
        await _reply_ephemeral(interaction, "❌ Member denied.")

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
        _ = button
        if not await self._ensure_staff(interaction):
            return

        ticket_id, member_id, member_name, member_obj, channel = await self._resolve_ticket_context(interaction)
        _ = ticket_id
        _ = member_id
        _ = member_name
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")
        if not isinstance(member_obj, discord.Member):
            return await _reply_ephemeral(interaction, "❌ Could not resolve the ticket member.")

        removed, remove_error = await _remove_unverified_role_if_present(
            member_obj,
            reason=f"Removed unverified role from Discord staff review panel by {interaction.user} ({interaction.user.id})",
        )
        if remove_error:
            return await _reply_ephemeral(interaction, f"❌ {remove_error}")

        if not removed:
            return await _reply_ephemeral(interaction, "ℹ️ Member did not have the Unverified role.")

        try:
            await channel.send(
                f"🧹 Removed **Unverified** from {member_obj.mention} by **{_staff_display_name(interaction.user)}**."
            )
        except Exception:
            pass

        await _freeze_message_controls(
            interaction.message,
            content_suffix=f"🧹 Unverified removed by {interaction.user.mention}.",
        )
        await _reply_ephemeral(interaction, "🧹 Unverified role removed.")

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
        _ = button
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
            timestamp=now_utc(),
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
# Open ticket actions view
# ============================================================

class TicketOpenActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="sv:ticket:close",
        row=0,
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        _ = button
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        if await _ticket_is_deleted(channel):
            return await _reply_ephemeral(interaction, "❌ Ticket is already deleted.")

        if await _ticket_is_closed(channel):
            return await _reply_ephemeral(interaction, "ℹ️ Ticket is already closed.")

        if not await _user_can_close_ticket(interaction, channel):
            return await _reply_ephemeral(
                interaction,
                "❌ Only the ticket owner or staff can close this ticket.",
            )

        if isinstance(interaction.user, discord.Member) and _is_staff_member(interaction.user):
            lock = _lock_for(_CLOSE_ACTION_LOCKS, channel.id)
            async with lock:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.defer(ephemeral=True)
                except Exception:
                    pass

                if await _ticket_is_deleted(channel):
                    return await _reply_ephemeral(interaction, "❌ Ticket is already deleted.")

                if await _ticket_is_closed(channel):
                    return await _reply_ephemeral(interaction, "ℹ️ Ticket is already closed.")

                try:
                    closed = await mark_ticket_closed(
                        channel=channel,
                        closed_by=interaction.user,
                        reason="Closed from ticket open controls",
                    )
                except Exception as e:
                    print("⚠️ mark_ticket_closed failed from open controls:", e)
                    closed = False

                if not closed:
                    return await _reply_ephemeral(interaction, "❌ Failed to close ticket.")

                try:
                    await _freeze_message_controls(
                        interaction.message,
                        content_suffix=f"🔒 Closed by {interaction.user.mention}.",
                    )
                except Exception:
                    pass

                try:
                    await _freeze_all_close_prompts(
                        channel,
                        suffix=f"🔒 Closed by {interaction.user.mention}.",
                    )
                except Exception:
                    pass

                try:
                    await interaction.followup.send(
                        f"✅ Closed {channel.mention}.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                return

        prompt = await prompt_ticket_close_confirmation(
            channel,
            requested_by=interaction.user if isinstance(interaction.user, discord.Member) else None,
        )
        if prompt is None:
            return await _reply_ephemeral(interaction, "❌ Failed to post the close confirmation.")

        await _reply_ephemeral(interaction, "⚠️ Close confirmation posted.")

    @discord.ui.button(
        label="Delete Ticket",
        style=discord.ButtonStyle.secondary,
        emoji="🗑️",
        custom_id="sv:ticket:delete_open",
        row=0,
    )
    async def delete_open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        _ = button
        if not isinstance(interaction.user, discord.Member) or not _is_staff_member(interaction.user):
            return await _reply_ephemeral(interaction, "❌ Staff only.")

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        if await _ticket_is_deleted(channel):
            return await _reply_ephemeral(interaction, "❌ Ticket is already deleted.")

        lock = _lock_for(_DELETE_ACTION_LOCKS, channel.id)
        async with lock:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

            if await _ticket_is_deleted(channel):
                return await _reply_ephemeral(interaction, "❌ Ticket is already deleted.")

            # Canonical lifecycle:
            # open -> closed -> deleted
            # This prevents open-ticket delete corruption.
            if await _ticket_is_open_like(channel):
                try:
                    closed_ok = await mark_ticket_closed(
                        channel=channel,
                        closed_by=interaction.user,
                        reason="Closed as part of staff delete from open controls",
                    )
                except Exception as e:
                    print("⚠️ open-delete close step failed:", e)
                    closed_ok = False

                if not closed_ok:
                    return await _reply_ephemeral(
                        interaction,
                        "❌ Failed to move ticket into closed state before delete.",
                    )

                try:
                    await _freeze_message_controls(
                        interaction.message,
                        content_suffix=f"🗑️ Delete started by {interaction.user.mention}.",
                    )
                except Exception:
                    pass

                try:
                    await _freeze_all_close_prompts(
                        channel,
                        suffix=f"🗑️ Delete started by {interaction.user.mention}.",
                    )
                except Exception:
                    pass

            is_ghost = await _detect_is_ghost_ticket(channel)

            try:
                result = await staff_delete_closed_ticket(
                    channel=channel,
                    staff_member=interaction.user,
                    is_ghost=is_ghost,
                    reason="Deleted by staff from open ticket controls",
                )
            except Exception as e:
                print("⚠️ staff_delete_closed_ticket failed from open delete:", e)
                return await _reply_ephemeral(interaction, f"❌ Failed to delete ticket: {e}")

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
        _ = button
        if not await self._ensure_staff(interaction):
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        lock = _lock_for(_REOPEN_ACTION_LOCKS, channel.id)
        async with lock:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

            if await _ticket_is_deleted(channel):
                return await _reply_ephemeral(interaction, "❌ Ticket is already deleted.")

            if await _ticket_is_open_like(channel):
                await _freeze_message_controls(
                    interaction.message,
                    content_suffix=f"🔓 Ticket already open. Checked by {interaction.user.mention}.",
                )
                controls_msg = await _ensure_open_ticket_controls_after_reopen(channel)
                if controls_msg is None:
                    try:
                        await channel.send(
                            _OPEN_CONTROLS_MARKER,
                            embed=discord.Embed(
                                title="🟢 Ticket Open",
                                description="Recovered open-ticket controls.",
                                color=discord.Color.green(),
                                timestamp=now_utc(),
                            ),
                            view=TicketOpenActionsView(),
                        )
                    except Exception:
                        pass
                return await _reply_ephemeral(interaction, "ℹ️ Ticket is already open.")

            owner = await _resolve_ticket_owner(channel)

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
                return await _reply_ephemeral(interaction, "❌ Failed to reopen ticket.")

            try:
                await _freeze_message_controls(
                    interaction.message,
                    content_suffix=f"🔓 Reopened by {interaction.user.mention}.",
                )
            except Exception:
                pass

            try:
                await _freeze_all_close_prompts(
                    channel,
                    suffix=f"🔓 Reopened by {interaction.user.mention}.",
                )
            except Exception:
                pass

            controls_msg = await _ensure_open_ticket_controls_after_reopen(channel)
            if controls_msg is None:
                try:
                    await channel.send(
                        f"⚠️ Ticket reopened by {interaction.user.mention}, but open controls had to be recovered manually.\n"
                        f"{_OPEN_CONTROLS_MARKER}",
                        view=TicketOpenActionsView(),
                    )
                except Exception:
                    pass

            try:
                await channel.send(
                    f"🔓 Ticket reopened by {interaction.user.mention}.\n{_CLOSE_REOPENED_MARKER}"
                )
            except Exception:
                pass

            try:
                await interaction.followup.send(
                    "✅ Ticket reopened.",
                    ephemeral=True,
                )
            except Exception:
                pass

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
        _ = button
        if not await self._ensure_staff(interaction):
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        owner = await _resolve_ticket_owner(channel)

        try:
            if await _ticket_has_transcript(channel.id):
                return await _reply_ephemeral(interaction, "ℹ️ Transcript already exists for this ticket.")

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
        _ = button
        if not await self._ensure_staff(interaction):
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        lock = _lock_for(_DELETE_ACTION_LOCKS, channel.id)
        async with lock:
            if await _ticket_is_deleted(channel):
                return await _reply_ephemeral(interaction, "❌ Ticket is already deleted.")

            is_ghost = False
            try:
                row = await _ticket_row(channel.id)
                if row:
                    is_ghost = _safe_bool(row.get("is_ghost"), False)
                else:
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
        _ = button
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        if not await _user_can_close_ticket(interaction, channel):
            return await _reply_ephemeral(
                interaction,
                "❌ Only the ticket owner or staff can close this ticket.",
            )

        lock = _lock_for(_CLOSE_ACTION_LOCKS, channel.id)
        async with lock:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True)
            except Exception:
                pass

            if await _ticket_is_deleted(channel):
                return await _reply_ephemeral(interaction, "❌ Ticket is already deleted.")

            if await _ticket_is_closed(channel):
                try:
                    await _freeze_message_controls(
                        interaction.message,
                        content_suffix="🔒 Ticket was already closed.",
                    )
                except Exception:
                    pass
                return await _reply_ephemeral(interaction, "ℹ️ Ticket is already closed.")

            try:
                closed = await mark_ticket_closed(
                    channel=channel,
                    closed_by=interaction.user if isinstance(interaction.user, (discord.Member, discord.User)) else None,
                    reason="Closed from ticket confirmation UI",
                )
            except Exception as e:
                print("⚠️ mark_ticket_closed failed:", e)
                closed = False

            if not closed:
                return await _reply_ephemeral(interaction, "❌ Failed to close ticket.")

            try:
                await _freeze_message_controls(
                    interaction.message,
                    content_suffix="🔒 Ticket close confirmed.",
                )
            except Exception:
                pass

            try:
                await _freeze_all_close_prompts(
                    channel,
                    suffix="🔒 Ticket close confirmed.",
                )
            except Exception:
                pass

            try:
                await interaction.followup.send(
                    "✅ Ticket closed.",
                    ephemeral=True,
                )
            except Exception:
                pass

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
        _ = button
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await _reply_ephemeral(interaction, "❌ Invalid channel.")

        if not await _user_can_close_ticket(interaction, channel):
            return await _reply_ephemeral(interaction, "❌ Only the ticket owner or staff can do that.")

        if await _ticket_is_closed(channel):
            return await _reply_ephemeral(interaction, "ℹ️ Ticket is already closed.")

        try:
            await _freeze_message_controls(
                interaction.message,
                content_suffix="❎ Ticket close cancelled.",
            )
        except Exception:
            pass

        await _reply_ephemeral(interaction, "✅ Cancelled.")


# ============================================================
# Verify UI helpers
# ============================================================

def build_verify_ui_view(*, token: str | None = None) -> discord.ui.View:
    _ = token
    view = discord.ui.View(timeout=None)

    view.add_item(
        discord.ui.Button(
            label="Get Secure Upload",
            style=discord.ButtonStyle.primary,
            custom_id="sv:verify:get",
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
                custom_id="sv:verify:vc",
                emoji="🎙️",
                row=0,
            )
        )

    try:
        site_url = str(globals().get("VERIFY_SITE_URL", "") or "").strip()
        if site_url:
            view.add_item(
                discord.ui.Button(
                    label="Tap to view website",
                    style=discord.ButtonStyle.link,
                    url=site_url,
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
            custom_id="sv:verify:raw",
            emoji="🔎",
            row=1,
        )
    )

    try:
        if bool(ALLOW_USER_VERIFYLINK):
            view.add_item(
                discord.ui.Button(
                    label="Generate New Link",
                    style=discord.ButtonStyle.secondary,
                    custom_id="sv:verify:regen",
                    emoji="🔁",
                    row=1,
                )
            )
    except Exception:
        pass

    return view


def _is_current_verify_ui_embed(embed: Optional[discord.Embed]) -> bool:
    if embed is None:
        return False

    try:
        title = str(getattr(embed, "title", "") or "").strip()
        footer_text = str(getattr(getattr(embed, "footer", None), "text", "") or "").strip()

        if title == VERIFY_UI_TITLE:
            return True
        if VERIFY_UI_FOOTER and VERIFY_UI_FOOTER.split(" • ")[0] in footer_text:
            return True
    except Exception:
        pass

    return False


def _is_legacy_verify_ui_embed(embed: Optional[discord.Embed]) -> bool:
    if embed is None:
        return False

    try:
        title = str(getattr(embed, "title", "") or "").strip()
        footer_text = str(getattr(getattr(embed, "footer", None), "text", "") or "").strip()

        legacy_titles = {
            "Stoney Balonney Verification",
            "Stoney Baloney Verification",
        }

        if title in legacy_titles and title != VERIFY_UI_TITLE:
            return True

        if "stoney_verify:verify_ui:" in footer_text and (VERIFY_UI_FOOTER.split(" • ")[0] not in footer_text):
            return True

        user_field = False
        privacy_field = False
        for f in (embed.fields or []):
            name = str(getattr(f, "name", "") or "").strip().lower()
            if name in {"👤 user", "user"}:
                user_field = True
            if name in {"🔒 privacy", "privacy"}:
                privacy_field = True

        if user_field and privacy_field and title != VERIFY_UI_TITLE:
            return True
    except Exception:
        pass

    return False


async def find_last_verify_ui_message(
    channel: discord.TextChannel,
    limit: int = 80,
    *,
    include_legacy: bool = False,
) -> Optional[discord.Message]:
    try:
        async for msg in channel.history(limit=limit):
            if not msg.author or not bot.user or msg.author.id != bot.user.id:
                continue
            if not msg.embeds:
                continue

            for e in msg.embeds:
                if _is_current_verify_ui_embed(e):
                    return msg

                if include_legacy and _is_legacy_verify_ui_embed(e):
                    return msg
    except Exception:
        pass
    return None


async def _delete_stale_verify_ui_messages(channel: discord.TextChannel, limit: int = 80) -> int:
    removed = 0
    try:
        me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
        async for msg in channel.history(limit=limit):
            if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            if not msg.embeds:
                continue

            should_delete = False
            for e in msg.embeds:
                if _is_legacy_verify_ui_embed(e):
                    should_delete = True
                    break

            if should_delete:
                try:
                    await msg.delete()
                    removed += 1
                except Exception:
                    continue
    except Exception:
        pass
    return removed


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

        current = await find_last_verify_ui_message(post_channel, limit=80, include_legacy=False)
        if current:
            return True

        try:
            await _delete_stale_verify_ui_messages(post_channel, limit=80)
        except Exception:
            pass

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

        if not requester_id:
            try:
                row = await _ticket_row(post_channel.id)
                requester_id = _safe_int(
                    (row or {}).get("user_id") or (row or {}).get("owner_id") or (row or {}).get("requester_id"),
                    0,
                ) or None
            except Exception:
                requester_id = None

        if not requester_id:
            print(
                f"⚠️ ensure_verify_ui_present: no requester_id resolved "
                f"for channel={post_channel.id} reason={reason}"
            )
            return False

        result = await post_or_replace_verify_ui(
            post_channel,
            requester_id=requester_id,
            reason=reason,
            site_url=VERIFY_SITE_URL,
            ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
            allow_regen=bool(ALLOW_USER_VERIFYLINK),
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

    verified_role = guild.get_role(int(VERIFIED_ROLE_ID or 0))
    resident_role = guild.get_role(int(RESIDENT_ROLE_ID or 0))

    roles_to_assign: List[discord.Role] = []
    missing_roles: List[str] = []

    if not verified_role:
        missing_roles.append(f"Verified (ID: {VERIFIED_ROLE_ID})")
    else:
        roles_to_assign.append(verified_role)

    if not resident_role:
        missing_roles.append(f"Resident (ID: {RESIDENT_ROLE_ID})")
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
    global _TRANSCRIPT_VIEWS_REGISTERED

    if _TRANSCRIPT_VIEWS_REGISTERED:
        return

    _TRANSCRIPT_VIEWS_REGISTERED = True

    try:
        bot.add_view(TicketOpenActionsView())
    except Exception as e:
        print("⚠️ Failed to register TicketOpenActionsView:", e)

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
    "TicketOpenActionsView",
    "StaffClosedTicketView",
    "ConfirmCloseTicketView",
    "prompt_ticket_close_confirmation",
    "post_or_replace_open_ticket_controls",
    "send_tickettool_style_transcript",
    "auto_close_after_decision",
    "build_html_transcript",
    "build_verify_ui_view",
    "find_last_verify_ui_message",
    "ensure_verify_ui_present",
    "check_bot_can_assign_roles",
    "post_or_replace_verification_staff_panel",
]
