from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, Optional, Tuple

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403

from ..tickets import find_ticket_owner_retry
from ..verify_ui import post_or_replace_verify_ui

from .common import (
    VC_ACCESS_TASKS,
    VC_REQUESTS,
    RUNTIME_STATS,
    _discord_channel_url,
    _staff_check,
    _staff_ping_text,
    _track_task,
    make_custom_id,
)

try:
    from ..store import (
        sb_get_token_info,
        sb_mark_decision,
        sb_set_used,
    )
except Exception:
    def sb_get_token_info(token: str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None

    def sb_mark_decision(
        token: str,
        decision: str,
        staff_id: int,
        approved_user_id: Optional[int] = None,
    ) -> None:  # type: ignore
        return None

    def sb_set_used(token: str, used: bool = True) -> None:  # type: ignore
        return None


# ============================================================
# Session / VC compatibility wiring
# ============================================================
try:
    from .. import vc_verify as _vc_verify_mod  # type: ignore
except Exception:
    _vc_verify_mod = None  # type: ignore

try:
    from .. import vc_sessions as _vc_sessions_mod  # type: ignore
except Exception:
    _vc_sessions_mod = None  # type: ignore


# ============================================================
# Constants / action names
# ============================================================
VC_STAFF_ACTIONS = {
    "vc_start",
    "vc_complete",
    "vc_cancel",
    "vc_upload",
    "vc_end",
    "vc_reissue",
    "vc_approve",
    "vc_denyclose",
    "vc_accept",
}

DEFAULT_VC_VERIFY_REQUESTS_CHANNEL_ID = 1476977094729793710


# ============================================================
# Helpers
# ============================================================
def _vc_requests_channel_id() -> int:
    """Resolve the staff alert channel id for VC verify request panels."""
    for key in ("VC_VERIFY_REQUESTS_CHANNEL_ID", "VC_VERIFY_QUEUE_CHANNEL_ID"):
        v = os.getenv(key)
        if v and str(v).strip().isdigit():
            return int(str(v).strip())

    try:
        if VC_VERIFY_QUEUE_CHANNEL_ID and int(VC_VERIFY_QUEUE_CHANNEL_ID) != 0:
            return int(VC_VERIFY_QUEUE_CHANNEL_ID)
    except Exception:
        pass

    return int(DEFAULT_VC_VERIFY_REQUESTS_CHANNEL_ID)


def _get_vc_channel(guild: discord.Guild) -> Optional[discord.VoiceChannel]:
    """Return the VC verification channel, or None if not configured/accessible."""
    try:
        if not VC_VERIFY_CHANNEL_ID:
            print("⚠️ VC_VERIFY_CHANNEL_ID is not set.")
            return None

        ch = guild.get_channel(int(VC_VERIFY_CHANNEL_ID))
        if not isinstance(ch, discord.VoiceChannel):
            print(
                f"⚠️ Channel {VC_VERIFY_CHANNEL_ID} is not a voice channel "
                f"(type: {type(ch).__name__})"
            )
            return None
        return ch
    except Exception as e:
        print(f"⚠️ Error in _get_vc_channel: {e}")
        return None


async def _resolve_text_channel(
    guild: discord.Guild,
    channel_id: int,
) -> Optional[discord.abc.Messageable]:
    """Resolve a staff post destination reliably."""
    if not channel_id:
        return None

    def _ok(ch: object) -> bool:
        try:
            if not hasattr(ch, "guild") or getattr(ch, "guild", None) != guild:
                return False
            if not hasattr(ch, "send") or not callable(getattr(ch, "send")):
                return False
            return True
        except Exception:
            return False

    try:
        ch = guild.get_channel(int(channel_id))
        if ch and _ok(ch):
            return ch  # type: ignore[return-value]
    except Exception:
        pass

    try:
        ch2 = await guild.fetch_channel(int(channel_id))
        if ch2 and _ok(ch2):
            return ch2  # type: ignore[return-value]
    except Exception:
        pass

    return None


async def _get_vc_queue_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    """VC verify staff queue channel."""
    try:
        cid = _vc_requests_channel_id()
        return await _resolve_text_channel(guild, int(cid or 0))
    except Exception:
        return None


async def _get_staff_alert_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    """Where staff-only VC request panels should go."""
    q = await _get_vc_queue_channel(guild)
    if q:
        return q

    try:
        if MODLOG_CHANNEL_ID:
            ch = await _resolve_text_channel(guild, int(MODLOG_CHANNEL_ID))
            if ch:
                return ch
    except Exception:
        pass

    try:
        if TRANSCRIPTS_CHANNEL_ID:
            ch = await _resolve_text_channel(guild, int(TRANSCRIPTS_CHANNEL_ID))
            if ch:
                return ch
    except Exception:
        pass

    return None


def _can_manage_channel(
    me: discord.Member,
    channel: discord.abc.GuildChannel,
) -> Tuple[bool, str]:
    """
    Check if the bot can manage the channel.
    Returns (True, "") or (False, reason).
    """
    try:
        if not me:
            return False, "Bot member not found in guild."
        perms = channel.permissions_for(me)
        if perms.administrator:
            return True, ""
        if not perms.manage_channels:
            return False, f"Bot lacks 'Manage Channels' in {channel.mention} (or category)."
        return True, ""
    except Exception as e:
        return False, f"Error checking permissions: {e}"


def _resolve_ticket_channel_from_token_info(
    guild: discord.Guild,
    token_info: Dict[str, Any],
) -> Optional[discord.TextChannel]:
    """
    Resolve the real ticket text channel from Supabase token_info.
    Returns None if channel doesn't exist or is not a text channel.
    """
    try:
        ch_id = int(str(token_info.get("channel_id") or "0") or 0)
        if not ch_id:
            return None
        ch = guild.get_channel(ch_id)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass
    return None


def _build_vc_staff_embed(
    *,
    guild: discord.Guild,
    requester_id: int,
    requester_mention: str,
    ticket_channel_id: int,
    token: str,
) -> discord.Embed:
    """Staff VC verification panel embed."""
    member = guild.get_member(int(requester_id))

    if member:
        user_display = f"{member.mention} — **{member.display_name}**"
    else:
        user_display = f"<@{requester_id}>"

    emb = discord.Embed(
        title="🎙️ VC Verification Requested",
        description="Staff-only panel — choose how to handle this VC request.",
        color=discord.Color.dark_green(),
        timestamp=now_utc(),
    )

    emb.add_field(
        name="User",
        value=f"{user_display}\n`{requester_id}`",
        inline=False,
    )
    emb.add_field(
        name="Ticket",
        value=f"<#{int(ticket_channel_id)}>\n`{ticket_channel_id}`",
        inline=True,
    )
    emb.add_field(
        name="VC Channel",
        value=(
            f"<#{int(VC_VERIFY_CHANNEL_ID)}>\n`{VC_VERIFY_CHANNEL_ID}`"
            if VC_VERIFY_CHANNEL_ID
            else "`Not configured`"
        ),
        inline=True,
    )
    emb.add_field(
        name="Token",
        value=f"`{token}`",
        inline=False,
    )

    vc_footer_ttl = int(
        globals().get(
            "VC_TOKEN_TTL_MINUTES",
            globals().get("VC_REQUEST_TTL_MINUTES", TOKEN_TTL_MINUTES or 20),
        ) or 20
    )
    emb.set_footer(
        text=f"Stoney Verify • VC staff panel | TTL {vc_footer_ttl}m"
    )
    return emb


async def _post_staff_vc_request_panel(
    *,
    guild: discord.Guild,
    token: str,
    requester_id: int,
    requester_mention: str,
    ticket_channel_id: int,
) -> Optional[int]:
    """
    Post the staff VC request panel.

    Priority:
      1) VC queue / requests channel
      2) MODLOG channel
      3) TRANSCRIPTS channel
      4) Ticket channel (last resort)
    """
    staff_view = discord.ui.View(timeout=None)
    staff_view.add_item(
        discord.ui.Button(
            label="✅ Accept VC Verify",
            style=discord.ButtonStyle.success,
            custom_id=make_custom_id("vc_accept", token),
        )
    )
    staff_view.add_item(
        discord.ui.Button(
            label="🔁 Ask for Upload Instead",
            style=discord.ButtonStyle.secondary,
            custom_id=make_custom_id("vc_upload", token),
        )
    )
    staff_view.add_item(
        discord.ui.Button(
            label="♻️ Reissue Token",
            style=discord.ButtonStyle.secondary,
            custom_id=make_custom_id("vc_reissue", token),
        )
    )

    emb = _build_vc_staff_embed(
        guild=guild,
        requester_id=int(requester_id),
        requester_mention=requester_mention,
        ticket_channel_id=int(ticket_channel_id),
        token=token,
    )

    ping = _staff_ping_text()
    content = ping if ping else None

    candidate_ids: list[int] = []

    try:
        candidate_ids.append(int(_vc_requests_channel_id()))
    except Exception:
        pass

    try:
        if MODLOG_CHANNEL_ID:
            candidate_ids.append(int(MODLOG_CHANNEL_ID))
    except Exception:
        pass

    try:
        if TRANSCRIPTS_CHANNEL_ID:
            candidate_ids.append(int(TRANSCRIPTS_CHANNEL_ID))
    except Exception:
        pass

    try:
        candidate_ids.append(int(ticket_channel_id))
    except Exception:
        pass

    tried: set[int] = set()

    for cid in candidate_ids:
        if not cid or cid in tried:
            continue
        tried.add(cid)

        ch = await _resolve_text_channel(guild, int(cid))
        if not ch:
            continue

        try:
            me = guild.me
            if me and hasattr(ch, "permissions_for"):
                perms = ch.permissions_for(me)  # type: ignore[attr-defined]
                if not (getattr(perms, "view_channel", True) and getattr(perms, "send_messages", True)):
                    print(
                        f"⚠️ VC staff panel: bot lacks view/send perms in "
                        f"{getattr(ch, 'id', None)}"
                    )
                    continue
        except Exception:
            pass

        try:
            msg = await ch.send(content=content, embed=emb, view=staff_view)  # type: ignore[misc]
            return int(getattr(msg, "id", 0) or 0) or None
        except Exception as e:
            print(f"⚠️ Failed to post staff VC panel to {getattr(ch, 'id', None)}: {e}")
            continue

    print("⚠️ VC staff panel: no target channel resolved or all posts failed.")
    return None


async def _cleanup_vc_permissions(
    guild: discord.Guild,
    keep_member: Optional[discord.Member] = None,
) -> None:
    """
    Remove all non-staff member overwrites from the VC channel,
    optionally keeping one member (the current user).
    """
    vc = _get_vc_channel(guild)
    if not vc:
        return

    me = guild.me
    if not me:
        return

    ok, why = _can_manage_channel(me, vc)
    if not ok:
        try:
            print(f"⚠️ VC cleanup skipped: {why}")
        except Exception:
            pass
        return

    for target, _ow in list(vc.overwrites.items()):
        if not isinstance(target, discord.Member):
            continue

        if is_staff(target) or (keep_member and target.id == keep_member.id):
            continue

        try:
            await vc.set_permissions(
                target,
                overwrite=None,
                reason="VC cleanup: remove stale access",
            )
            print(f"✅ Removed VC overwrite for {target}")
        except Exception as e:
            print(f"⚠️ Failed to remove overwrite for {target}: {e}")


async def _vc_revoke_access(
    guild: discord.Guild,
    member: discord.Member,
    token: str,
    reason: str = "revoke",
) -> None:
    vc = _get_vc_channel(guild)
    if not vc:
        return

    me = guild.me
    if not me:
        return

    ok, _ = _can_manage_channel(me, vc)
    if not ok:
        return

    try:
        await vc.set_permissions(
            member,
            overwrite=None,
            reason=f"VC verify revoke ({reason}) token={token}",
        )
        print(f"✅ Revoked VC access for {member} in {vc.name}")
    except Exception as e:
        print(f"⚠️ Failed to revoke VC access: {e}")

    try:
        t = VC_ACCESS_TASKS.get(token)
        if t and not t.done():
            t.cancel()
    except Exception:
        pass

    VC_ACCESS_TASKS.pop(token, None)


async def _vc_grant_access(
    guild: discord.Guild,
    member: discord.Member,
    token: str,
) -> Tuple[bool, str]:
    """
    Grant the member access to the VC verification channel.
    Returns (success, message).
    """
    print(f"🔍 _vc_grant_access: guild={guild.id}, member={member.id}, token={token}")

    vc = _get_vc_channel(guild)
    if not vc:
        msg = "VC verification channel not found or not a voice channel."
        print(f"❌ {msg}")
        return False, msg

    me = guild.me
    if not me:
        msg = "Bot member missing in guild."
        print(f"❌ {msg}")
        return False, msg

    ok, reason = _can_manage_channel(me, vc)
    if not ok:
        full_msg = f"Bot lacks permission to manage {vc.mention}: {reason}"
        print(f"❌ {full_msg}")
        return False, full_msg

    try:
        ow = vc.overwrites_for(member)
        ow.view_channel = True
        ow.connect = True
        ow.speak = True
        await vc.set_permissions(
            member,
            overwrite=ow,
            reason=f"VC verify grant (token={token})",
        )
        print(f"✅ Granted VC access to {member} in {vc.name}")
    except discord.Forbidden as e:
        msg = f"Forbidden while setting VC permissions: {e}"
        print(f"❌ {msg}")
        return False, msg
    except discord.HTTPException as e:
        msg = f"Discord API error while setting permissions: {e}"
        print(f"❌ {msg}")
        return False, msg
    except Exception as e:
        msg = f"Unexpected error: {e}"
        print(f"❌ {msg}")
        return False, msg

    await _cleanup_vc_permissions(guild, keep_member=member)

    access_minutes = int(globals().get("VC_VERIFY_ACCESS_MINUTES", 30) or 30)

    async def _revoke_later():
        try:
            await asyncio.sleep(max(30, access_minutes * 60))
            await _vc_revoke_access(guild, member, token, reason="auto-expire")
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"⚠️ Error in revoke_later: {e}")

    try:
        old = VC_ACCESS_TASKS.get(token)
        if old and not old.done():
            old.cancel()
        t = asyncio.create_task(_revoke_later())
        _track_task(t, label="vc_access_revoke")
        VC_ACCESS_TASKS[token] = t
    except Exception as e:
        print(f"⚠️ Failed to schedule revoke task: {e}")

    return True, "OK"


async def _vc_disable_panels_everywhere(
    guild: discord.Guild,
    token: str,
    status_text: str,
) -> None:
    """
    When one staff member handles a VC request, disable the old buttons
    everywhere we posted them.
    """
    try:
        req = VC_REQUESTS.get(token) or {}
        msg_ids = req.get("staff_msg_ids") or []
        if not isinstance(msg_ids, list):
            msg_ids = []
    except Exception:
        msg_ids = []

    if not msg_ids:
        return

    staff_ch = await _get_staff_alert_channel(guild)
    if not staff_ch:
        return

    for mid in list(msg_ids):
        try:
            m = await staff_ch.fetch_message(int(mid))  # type: ignore[attr-defined]
            try:
                await m.edit(content=(m.content or ""), view=None)
            except Exception:
                pass

            try:
                if status_text:
                    await staff_ch.send(f"ℹ️ VC request `{token}`: {status_text}")  # type: ignore[misc]
            except Exception:
                pass
        except Exception:
            continue


async def _cleanup_stale_vc_request(
    guild: discord.Guild,
    token: str,
    reason: str,
) -> bool:
    """
    If a VC request's ticket channel no longer exists, delete the request entry
    and disable the panel message (if any). Returns True if cleanup was performed.
    """
    try:
        req = VC_REQUESTS.get(token)
        if not req:
            return False

        ticket_ch_id = req.get("ticket_channel_id")
        if not ticket_ch_id:
            VC_REQUESTS.pop(token, None)
            return True

        ticket_ch = guild.get_channel(ticket_ch_id)
        if isinstance(ticket_ch, discord.TextChannel):
            return False

        VC_REQUESTS.pop(token, None)

        msg_ids = req.get("staff_msg_ids", [])
        staff_ch = await _get_staff_alert_channel(guild)
        if staff_ch and msg_ids:
            for mid in msg_ids:
                try:
                    msg = await staff_ch.fetch_message(mid)  # type: ignore[attr-defined]
                    await msg.edit(
                        content=(
                            "🚫 **Request cancelled** – ticket channel no longer exists.\n"
                            f"Reason: {reason}"
                        ),
                        view=None,
                    )
                except Exception:
                    pass
        return True
    except Exception as e:
        print(f"⚠️ Error in _cleanup_stale_vc_request: {e}")
        return False


def _find_active_vc_token_for_channel(channel_id: int) -> Optional[str]:
    try:
        cid = int(channel_id)
    except Exception:
        return None

    for tok, req in list((VC_REQUESTS or {}).items()):
        try:
            if int(req.get("ticket_channel_id") or 0) != cid:
                continue

            status = str(req.get("status") or "").upper()
            if status in {
                "PENDING",
                "ACCEPTED",
                "STAFF_ACCEPTED",
                "READY",
                "IN_VC",
                "STARTED",
                "TAKEN_OVER",
                "RESTARTED",
            }:
                return str(tok)
        except Exception:
            continue

    return None


async def _resolve_vc_ticket_and_owner(
    guild: discord.Guild,
    token: str,
) -> Tuple[Optional[discord.TextChannel], Optional[discord.Member], Optional[Dict[str, Any]]]:
    tok = str(token or "").strip()
    if not tok:
        return None, None, None

    token_info = sb_get_token_info(tok)
    if not token_info:
        return None, None, None

    ticket_ch = _resolve_ticket_channel_from_token_info(guild, token_info)
    owner = None

    try:
        rid = int(str(token_info.get("requester_id") or token_info.get("user_id") or "0") or 0)
        if rid:
            owner = guild.get_member(rid) or await guild.fetch_member(rid)
    except Exception:
        owner = None

    if owner is None and isinstance(ticket_ch, discord.TextChannel):
        try:
            owner = await find_ticket_owner_retry(ticket_ch)
        except Exception:
            owner = None

    return ticket_ch if isinstance(ticket_ch, discord.TextChannel) else None, owner, token_info


async def _vc_lock_channel_for_session(
    guild: discord.Guild,
    owner: Optional[discord.Member],
    staff_member: Optional[discord.Member],
    token: str,
) -> Tuple[bool, str]:
    if not isinstance(owner, discord.Member):
        return False, "Ticket owner could not be resolved."
    if not isinstance(staff_member, discord.Member):
        return False, "Assigned staff member could not be resolved."

    try:
        if _vc_sessions_mod and hasattr(_vc_sessions_mod, "ensure_session"):
            _vc_sessions_mod.ensure_session(
                token=str(token),
                guild_id=int(guild.id),
                ticket_channel_id=int((VC_REQUESTS.get(token) or {}).get("ticket_channel_id") or 0),
                requester_id=int(owner.id),
                owner_id=int(owner.id),
                vc_channel_id=int(VC_VERIFY_CHANNEL_ID or 0),
                queue_channel_id=int(globals().get("VC_VERIFY_QUEUE_CHANNEL_ID", 0) or 0),
                access_minutes=int(globals().get("VC_VERIFY_ACCESS_MINUTES", 30) or 30),
                meta={
                    "assigned_staff_id": int(staff_member.id),
                    "assigned_staff_name": str(staff_member.display_name),
                    "staff_confirmed": True,
                },
            )

        if _vc_sessions_mod and hasattr(_vc_sessions_mod, "set_staff_accepted"):
            _vc_sessions_mod.set_staff_accepted(
                token=str(token),
                staff_id=int(staff_member.id),
                staff_name=str(staff_member.display_name),
            )
    except Exception:
        pass

    try:
        VC_REQUESTS.setdefault(token, {})
        VC_REQUESTS[token]["accepted_staff_id"] = int(staff_member.id)
        VC_REQUESTS[token]["assigned_staff_id"] = int(staff_member.id)
        VC_REQUESTS[token]["status"] = "ACCEPTED"
        VC_REQUESTS[token]["vc_channel_id"] = int(VC_VERIFY_CHANNEL_ID or 0)
    except Exception:
        pass

    if _vc_verify_mod and hasattr(_vc_verify_mod, "vc_unlock_session_participants"):
        ok, msg = await _vc_verify_mod.vc_unlock_session_participants(
            guild=guild,
            token=str(token),
            owner=owner,
            staff_member=staff_member,
        )
        if not ok:
            return False, msg
    else:
        ok, msg = await _vc_grant_access(guild, owner, token)
        if not ok:
            return False, msg
        ok2, msg2 = await _vc_grant_access(guild, staff_member, token)
        if not ok2:
            return False, msg2

    vc = None
    try:
        if _vc_verify_mod and hasattr(_vc_verify_mod, "_resolve_vc_channel"):
            vc = await _vc_verify_mod._resolve_vc_channel(guild)
    except Exception:
        vc = None

    if isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
        try:
            me = guild.me
            can_manage = False
            try:
                perms_result = _can_manage_channel(me, vc) if me else (False, "")
                can_manage = bool(perms_result[0])
            except Exception:
                can_manage = False

            if me and can_manage:
                for target, _ow in list(vc.overwrites.items()):
                    if not isinstance(target, discord.Member):
                        continue
                    if target.id in {int(owner.id), int(staff_member.id)} or is_staff(target):
                        continue
                    try:
                        await vc.set_permissions(
                            target,
                            overwrite=None,
                            reason=f"VC session lock cleanup token={token}",
                        )
                    except Exception:
                        pass

                for m in list(getattr(vc, "members", []) or []):
                    if int(m.id) in {int(owner.id), int(staff_member.id)}:
                        continue
                    try:
                        await m.move_to(
                            None,
                            reason=f"VC session private lock token={token}",
                        )
                    except Exception:
                        pass
        except Exception:
            pass

    return True, "VC locked to ticket owner + assigned staff."


async def _vc_unlock_channel_for_next_session(
    guild: discord.Guild,
    token: str,
) -> None:
    try:
        if _vc_verify_mod and hasattr(_vc_verify_mod, "vc_relock_session"):
            await _vc_verify_mod.vc_relock_session(
                guild=guild,
                token=str(token),
                reason="ready for next person",
            )
    except Exception:
        pass

    try:
        if _vc_sessions_mod and hasattr(_vc_sessions_mod, "clear_unlock"):
            _vc_sessions_mod.clear_unlock(token=str(token), action_name="ready_next")
    except Exception:
        pass

    try:
        req = VC_REQUESTS.get(token) or {}
        req["status"] = "COMPLETED"
        req.pop("accepted_staff_id", None)
        req.pop("assigned_staff_id", None)
        VC_REQUESTS[token] = req
    except Exception:
        pass


# ============================================================
# Slash command handlers
# ============================================================
async def _vc_reissue_command(
    interaction: discord.Interaction,
    token: Optional[str] = None,
    ticket: Optional[discord.TextChannel] = None,
):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if not guild:
        return await interaction.followup.send("❌ Invalid context (no guild).", ephemeral=True)

    resolved_token: str = (token or "").strip()
    resolved_ticket_id: int = int(getattr(ticket, "id", 0) or 0)

    if not resolved_token:
        ch = interaction.channel
        if isinstance(ch, discord.TextChannel):
            try:
                me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
                async for msg in ch.history(limit=50):
                    if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                        continue
                    if not msg.embeds:
                        continue

                    e = msg.embeds[0]
                    ft = str(getattr(getattr(e, "footer", None), "text", "") or "")
                    if "VC staff panel" not in ft:
                        continue

                    tok = ""
                    tid = 0
                    try:
                        for f in (e.fields or []):
                            n = (f.name or "").strip().lower()
                            v = (f.value or "").strip()
                            if n == "token":
                                tok = v.split()[0].strip("`").strip()
                            if n == "ticket":
                                mm = re.search(r"\b(\d{15,22})\b", v)
                                if mm:
                                    tid = int(mm.group(1))
                    except Exception:
                        pass

                    if tok:
                        resolved_token = tok
                    if tid:
                        resolved_ticket_id = tid
                    if resolved_token:
                        break
            except Exception:
                pass

    if not resolved_token:
        return await interaction.followup.send(
            "❌ Missing token.\nUse `/vc_reissue token:<token>` or run it inside the VC queue channel so I can read the latest panel.",
            ephemeral=True,
        )

    old_info = sb_get_token_info(resolved_token)
    if not old_info:
        return await interaction.followup.send("❌ Token not found in storage.", ephemeral=True)

    if not resolved_ticket_id:
        try:
            resolved_ticket_id = int(str(old_info.get("channel_id") or "0") or 0)
        except Exception:
            resolved_ticket_id = 0

    ticket_ch: Optional[discord.TextChannel] = None
    if resolved_ticket_id:
        ch2 = guild.get_channel(int(resolved_ticket_id))
        if isinstance(ch2, discord.TextChannel):
            ticket_ch = ch2

    if not ticket_ch:
        await _cleanup_stale_vc_request(
            guild,
            resolved_token,
            reason="channel not found during reissue",
        )
        return await interaction.followup.send(
            "❌ I couldn’t resolve the ticket channel for that token.\n"
            "The ticket channel may have been deleted. The request has been cleaned up.",
            ephemeral=True,
        )

    try:
        vc_ttl = int(globals().get("VC_REQUEST_TTL_MINUTES", 0) or 0)
    except Exception:
        vc_ttl = 0
    if vc_ttl <= 0:
        vc_ttl = max(20, int(TOKEN_TTL_MINUTES or 20))

    try:
        rid = int(str(old_info.get("requester_id") or old_info.get("user_id") or "0") or 0)
    except Exception:
        rid = 0

    try:
        await post_or_replace_verify_ui(
            ticket_ch,
            requester_id=rid or None,
            reason=f"vc_reissue_by_staff:{interaction.user.id}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=TOKEN_TTL_MINUTES,
            allow_regen=ALLOW_USER_VERIFYLINK,
        )
    except Exception:
        pass

    try:
        from ..verify_ui import _issue_token_url  # type: ignore
        new_token, _ = await _issue_token_url(
            site_url=VERIFY_SITE_URL,
            guild=guild,
            channel=ticket_ch,
            requester_id=int(rid or interaction.user.id),
            ttl_minutes=vc_ttl,
        )
        print(f"✅ Created new token {new_token} with TTL {vc_ttl}m")
    except Exception as e:
        return await interaction.followup.send(
            f"❌ Failed to create new token: {e}",
            ephemeral=True,
        )

    try:
        VC_REQUESTS[new_token] = {
            "status": "PENDING",
            "requested_at": now_utc().isoformat(),
            "requested_by": int(rid or interaction.user.id),
            "ticket_channel_id": int(ticket_ch.id),
            "guild_id": int(guild.id),
            "reissued_from": resolved_token,
            "reissued_by": int(interaction.user.id),
        }
    except Exception:
        pass

    qch = await _get_vc_queue_channel(guild)
    if not qch and isinstance(interaction.channel, discord.TextChannel):
        qch = interaction.channel

    updated_panel_mid: Optional[int] = None

    try:
        if isinstance(qch, discord.TextChannel):
            me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
            async for msg in qch.history(limit=50):
                if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                    continue
                if not msg.embeds:
                    continue

                e = msg.embeds[0]
                ft = str(getattr(getattr(e, "footer", None), "text", "") or "")
                if "VC staff panel" not in ft:
                    continue

                old_tok = ""
                try:
                    for f in (e.fields or []):
                        if (f.name or "").strip().lower() == "token":
                            old_tok = str(f.value or "").strip().strip("`")
                            break
                except Exception:
                    old_tok = ""

                if old_tok and old_tok != resolved_token:
                    continue

                requester_mention = f"<@{int(rid or 0)}>" if rid else f"<@{int(interaction.user.id)}>"
                emb = _build_vc_staff_embed(
                    guild=guild,
                    requester_id=int(rid or 0),
                    requester_mention=requester_mention,
                    ticket_channel_id=int(ticket_ch.id),
                    token=new_token,
                )

                staff_view = discord.ui.View(timeout=None)
                staff_view.add_item(
                    discord.ui.Button(
                        label="✅ Accept VC Verify",
                        style=discord.ButtonStyle.success,
                        custom_id=make_custom_id("vc_accept", new_token),
                    )
                )
                staff_view.add_item(
                    discord.ui.Button(
                        label="🔁 Ask for Upload Instead",
                        style=discord.ButtonStyle.secondary,
                        custom_id=make_custom_id("vc_upload", new_token),
                    )
                )
                staff_view.add_item(
                    discord.ui.Button(
                        label="♻️ Reissue Token",
                        style=discord.ButtonStyle.secondary,
                        custom_id=make_custom_id("vc_reissue", new_token),
                    )
                )

                try:
                    await msg.edit(embed=emb, view=staff_view)
                    updated_panel_mid = int(msg.id)
                except Exception:
                    updated_panel_mid = None
                break
    except Exception:
        updated_panel_mid = None

    if updated_panel_mid is None:
        try:
            requester_mention = f"<@{int(rid or 0)}>" if rid else f"<@{int(interaction.user.id)}>"
            updated_panel_mid = await _post_staff_vc_request_panel(
                guild=guild,
                token=new_token,
                requester_id=int(rid or interaction.user.id),
                requester_mention=requester_mention,
                ticket_channel_id=int(ticket_ch.id),
            )
        except Exception:
            updated_panel_mid = None

    try:
        await _vc_disable_panels_everywhere(
            guild,
            resolved_token,
            status_text=f"Reissued → `{new_token}` by {interaction.user.mention}",
        )
    except Exception:
        pass

    try:
        if updated_panel_mid:
            VC_REQUESTS.setdefault(new_token, {}).setdefault("staff_msg_ids", [])
            VC_REQUESTS[new_token]["staff_msg_ids"] = [int(updated_panel_mid)]
    except Exception:
        pass

    old_entry = VC_REQUESTS.get(resolved_token)
    if old_entry and old_entry.get("ticket_panel_msg_id"):
        ticket_panel_msg_id = old_entry["ticket_panel_msg_id"]
        if ticket_ch:
            try:
                ticket_msg = await ticket_ch.fetch_message(ticket_panel_msg_id)
                new_view = discord.ui.View(timeout=None)
                new_view.add_item(
                    discord.ui.Button(
                        label="✅ Approve (VC)",
                        style=discord.ButtonStyle.success,
                        custom_id=make_custom_id("vc_approve", new_token),
                    )
                )
                new_view.add_item(
                    discord.ui.Button(
                        label="⛔ Deny & Close (VC)",
                        style=discord.ButtonStyle.danger,
                        custom_id=make_custom_id("vc_denyclose", new_token),
                    )
                )
                new_view.add_item(
                    discord.ui.Button(
                        label="🧹 End VC Session",
                        style=discord.ButtonStyle.secondary,
                        custom_id=make_custom_id("vc_end", new_token),
                    )
                )
                await ticket_msg.edit(view=new_view)

                if new_token not in VC_REQUESTS:
                    VC_REQUESTS[new_token] = {}
                VC_REQUESTS[new_token]["ticket_panel_msg_id"] = ticket_panel_msg_id
            except Exception as e:
                print(f"⚠️ Failed to update ticket panel in /vc_reissue: {e}")

    return await interaction.followup.send(
        f"✅ Reissued VC token.\nOld: `{resolved_token}`\nNew: `{new_token}`\nTicket: {ticket_ch.mention}",
        ephemeral=True,
    )


async def _vc_status_command(
    interaction: discord.Interaction,
    token: Optional[str] = None,
):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

    tok = (token or "").strip()
    if not tok and isinstance(interaction.channel, discord.TextChannel):
        tok = _find_active_vc_token_for_channel(interaction.channel.id) or ""

    if not tok:
        return await interaction.response.send_message(
            "❌ No active VC token found for this ticket.",
            ephemeral=True,
        )

    ticket_ch, owner, token_info = await _resolve_vc_ticket_and_owner(guild, tok)
    if token_info is None:
        return await interaction.response.send_message("❌ Token not found.", ephemeral=True)

    req = VC_REQUESTS.get(tok) or {}
    accepted_by = int(req.get("accepted_staff_id") or req.get("accepted_by") or 0)

    msg = [f"🎙️ VC token: `{tok}`", f"Status: `{req.get('status') or 'UNKNOWN'}`"]
    if ticket_ch:
        msg.append(f"Ticket: {ticket_ch.mention}")
    if owner:
        msg.append(f"Owner: {owner.mention} (`{owner.id}`)")
    if accepted_by:
        msg.append(f"Accepted by: <@{accepted_by}> (`{accepted_by}`)")

    await interaction.response.send_message("\n".join(msg), ephemeral=True)


async def _vc_takeover_command(
    interaction: discord.Interaction,
    token: Optional[str] = None,
    ticket: Optional[discord.TextChannel] = None,
):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    tok = (token or "").strip()
    ticket_ch = ticket if isinstance(ticket, discord.TextChannel) else (
        interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
    )

    if not tok and ticket_ch is not None:
        tok = _find_active_vc_token_for_channel(ticket_ch.id) or ""

    if not tok:
        return await interaction.followup.send(
            "❌ No active VC token found. Pass a token or run this in the ticket channel.",
            ephemeral=True,
        )

    ticket_ch2, owner, token_info = await _resolve_vc_ticket_and_owner(guild, tok)
    if token_info is None:
        return await interaction.followup.send("❌ Token not found.", ephemeral=True)

    ticket_ch = ticket_ch2 or ticket_ch
    if not isinstance(ticket_ch, discord.TextChannel):
        return await interaction.followup.send(
            "❌ Could not resolve the ticket channel.",
            ephemeral=True,
        )

    req = VC_REQUESTS.setdefault(tok, {})
    prev_staff = int(req.get("accepted_staff_id") or req.get("accepted_by") or 0)
    req["accepted_staff_id"] = int(interaction.user.id)
    req["accepted_by"] = int(interaction.user.id)
    req["status"] = "ACCEPTED"
    req["takeover_at"] = now_utc().isoformat()
    req["takeover_by"] = int(interaction.user.id)

    try:
        if _vc_sessions_mod and hasattr(_vc_sessions_mod, "takeover_session"):
            _vc_sessions_mod.takeover_session(
                token=str(tok),
                new_staff_id=int(interaction.user.id),
                new_staff_name=str(getattr(interaction.user, "display_name", interaction.user)),
                reason="manual takeover by staff",
            )
    except Exception:
        pass

    ok, msg = await _vc_lock_channel_for_session(
        guild,
        owner,
        interaction.user if isinstance(interaction.user, discord.Member) else None,
        tok,
    )
    if not ok:
        return await interaction.followup.send(
            f"❌ Failed to lock VC channel: {msg}",
            ephemeral=True,
        )

    try:
        await ticket_ch.send(
            f"🔁 **VC verify takeover:** {interaction.user.mention} has taken over this VC session"
            + (
                f" from <@{prev_staff}>."
                if prev_staff and prev_staff != int(interaction.user.id)
                else "."
            )
        )
    except Exception:
        pass

    await interaction.followup.send(
        f"✅ VC session now belongs to you. Only you and the ticket owner should have VC access for `{tok}`.",
        ephemeral=True,
    )


async def _vc_unlock_command(
    interaction: discord.Interaction,
    token: Optional[str] = None,
):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    tok = (token or "").strip()
    if not tok and isinstance(interaction.channel, discord.TextChannel):
        tok = _find_active_vc_token_for_channel(interaction.channel.id) or ""

    if not tok:
        return await interaction.followup.send("❌ No active VC token found.", ephemeral=True)

    try:
        ticket_ch, owner, _ = await _resolve_vc_ticket_and_owner(guild, tok)
    except Exception:
        ticket_ch, owner = None, None

    try:
        if isinstance(owner, discord.Member):
            await _vc_revoke_access(guild, owner, tok, reason="manual-unlock")
    except Exception:
        pass

    await _vc_unlock_channel_for_next_session(guild, tok)

    try:
        if isinstance(ticket_ch, discord.TextChannel):
            await ticket_ch.send(
                f"🧹 VC verify channel reset by {interaction.user.mention}. Ready for the next person."
            )
    except Exception:
        pass

    await interaction.followup.send(
        f"✅ VC channel reset for token `{tok}`.",
        ephemeral=True,
    )


async def _vc_cleanup_command(interaction: discord.Interaction):
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    try:
        await _cleanup_vc_permissions(guild, keep_member=None)
        await interaction.followup.send("✅ VC verify channel cleanup complete.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ VC cleanup failed: {e}", ephemeral=True)


# ============================================================
# Explicit registration
# ============================================================
_REGISTERED = False


def register_vc_flow_commands(_bot: Any = None, tree: Any = None) -> None:
    global _REGISTERED

    if _REGISTERED:
        return

    command_tree = tree or getattr(_bot, "tree", None) or bot.tree

    @command_tree.command(
        name="vc_reissue",
        description="(Staff) Reissue a VC request token (use in the VC queue channel or ticket).",
    )
    @app_commands.describe(
        token="(Optional) The token shown in the VC queue panel. If omitted, I will try to read it from the latest VC panel in this channel.",
        ticket="(Optional) The ticket channel. If omitted, I will resolve it from the token/panel.",
    )
    async def vc_reissue_slash(
        interaction: discord.Interaction,
        token: Optional[str] = None,
        ticket: Optional[discord.TextChannel] = None,
    ):
        return await _vc_reissue_command(interaction, token=token, ticket=ticket)

    @command_tree.command(
        name="vc_status",
        description="(Staff) Show active VC verify session details for this ticket or token.",
    )
    @app_commands.describe(token="Optional VC token (leave empty to resolve from the current ticket)")
    async def vc_status_slash(
        interaction: discord.Interaction,
        token: Optional[str] = None,
    ):
        return await _vc_status_command(interaction, token=token)

    @command_tree.command(
        name="vc_takeover",
        description="(Staff) Take over a stuck VC verify ticket and lock the VC to you + the ticket owner.",
    )
    @app_commands.describe(token="Optional VC token", ticket="Optional ticket channel")
    async def vc_takeover_slash(
        interaction: discord.Interaction,
        token: Optional[str] = None,
        ticket: Optional[discord.TextChannel] = None,
    ):
        return await _vc_takeover_command(interaction, token=token, ticket=ticket)

    @command_tree.command(
        name="vc_unlock",
        description="(Staff) Force-unlock/reset the VC verify channel so it is ready for the next person.",
    )
    @app_commands.describe(token="Optional VC token")
    async def vc_unlock_slash(
        interaction: discord.Interaction,
        token: Optional[str] = None,
    ):
        return await _vc_unlock_command(interaction, token=token)

    @command_tree.command(
        name="vc_cleanup",
        description="(Staff) Remove stale non-staff permission overwrites from the VC verify channel.",
    )
    async def vc_cleanup_slash(interaction: discord.Interaction):
        return await _vc_cleanup_command(interaction)

    _REGISTERED = True
    print("✅ commands_ext.vc_flow: registered VC flow commands")


def register_extra_commands(tree) -> None:
    try:
        register_vc_flow_commands(bot, tree)
    except Exception:
        pass


__all__ = [
    "VC_STAFF_ACTIONS",
    "DEFAULT_VC_VERIFY_REQUESTS_CHANNEL_ID",
    "_vc_requests_channel_id",
    "_get_vc_channel",
    "_resolve_text_channel",
    "_get_vc_queue_channel",
    "_get_staff_alert_channel",
    "_can_manage_channel",
    "_resolve_ticket_channel_from_token_info",
    "_build_vc_staff_embed",
    "_post_staff_vc_request_panel",
    "_cleanup_vc_permissions",
    "_vc_grant_access",
    "_vc_revoke_access",
    "_vc_disable_panels_everywhere",
    "_cleanup_stale_vc_request",
    "_find_active_vc_token_for_channel",
    "_resolve_vc_ticket_and_owner",
    "_vc_lock_channel_for_session",
    "_vc_unlock_channel_for_next_session",
    "register_vc_flow_commands",
    "register_extra_commands",
]