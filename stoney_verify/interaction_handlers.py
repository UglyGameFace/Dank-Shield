from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import discord

from .globals import *  # noqa: F401,F403

from .tickets import (
    find_ticket_owner_retry,
    is_verification_ticket_channel,
    parse_mod_id,
    _parse_webhook_id_from_url,  # type: ignore
)

from .commands_ext.common import (
    VC_REQUESTS,
    VC_REQUEST_COOLDOWNS,
    RUNTIME_STATS,
    TICKET_LAST_ACTIVITY,
    VC_ACCESS_TASKS,
    ACTIVE_DECISION_PANEL_MSG_ID,
    RECENT_SUBMISSION_TOKENS,
    RECENT_SUBMISSION_MSG_IDS,
    KICK_TIMER_TASKS,
    KICK_TIMER_STARTS,
    KICK_TIMER_STARTED_BY,
    SITE_URL,
    ALLOW_USER_VERIFYLINK,
    VC_VERIFY_ACCESS_MINUTES,
    _get_lock,
    _discord_channel_url,
    _staff_check,
    _staff_ping_text,
    build_verify_link,
    extract_token_from_message,
    make_custom_id,
    mark_ticket_activity,
    parse_custom_id,
    token_is_expired,
)

try:
    from .store import (
        sb_get_token_info,
        sb_mark_decision,
        sb_set_submitted,
        sb_set_submitted_at,
        sb_set_used,
    )
except Exception:
    def sb_get_token_info(token: str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None

    def sb_mark_decision(  # type: ignore
        token: str,
        decision: str,
        staff_id: int,
        approved_user_id: Optional[int] = None,
    ) -> None:
        return None

    def sb_set_submitted(token: str) -> None:  # type: ignore
        return None

    def sb_set_submitted_at(token: str, submitted_at=None) -> None:  # type: ignore
        return None

    def sb_set_used(token: str, used: bool = True) -> None:  # type: ignore
        return None


try:
    from .verify_ui import (
        maybe_handle_verify_ui_interaction,
        post_or_replace_verify_ui,
    )
except Exception:
    async def maybe_handle_verify_ui_interaction(interaction: discord.Interaction, site_url: str) -> bool:  # type: ignore
        return False

    async def post_or_replace_verify_ui(*args, **kwargs) -> Optional[str]:  # type: ignore
        return None


try:
    from .transcripts import (
        auto_close_after_decision,
        check_bot_can_assign_roles,
    )
except Exception:
    async def auto_close_after_decision(*args, **kwargs) -> None:  # type: ignore
        return None

    async def check_bot_can_assign_roles(*args, **kwargs) -> Tuple[bool, str, List[discord.Role]]:  # type: ignore
        return (False, "transcripts.py missing", [])


try:
    from .commands_ext.kick_timers import (
        _cancel_kick_timer,
        kick_timer_persist_delete,
    )
except Exception:
    def _cancel_kick_timer(channel_id: int) -> bool:
        try:
            task = KICK_TIMER_TASKS.get(int(channel_id))
            if task and not task.done():
                task.cancel()
                return True
        except Exception:
            pass
        return False

    async def kick_timer_persist_delete(channel_id: int) -> None:
        return None


try:
    from .commands_ext.vc_flow import (
        VC_STAFF_ACTIONS,
        _build_vc_staff_embed,
        _can_manage_channel,
        _cleanup_stale_vc_request,
        _find_active_vc_token_for_channel,
        _get_staff_alert_channel,
        _get_vc_channel,
        _post_staff_vc_request_panel,
        _resolve_ticket_channel_from_token_info,
        _resolve_vc_ticket_and_owner,
        _resolve_text_channel,
        _vc_disable_panels_everywhere,
        _vc_grant_access,
        _vc_lock_channel_for_session,
        _vc_requests_channel_id,
        _vc_revoke_access,
        _vc_unlock_channel_for_next_session,
    )
except Exception:
    VC_STAFF_ACTIONS = {
        "vc_accept",
        "vc_start",
        "vc_complete",
        "vc_cancel",
        "vc_upload",
        "vc_reissue",
        "vc_end",
        "vc_approve",
        "vc_denyclose",
    }

    DEFAULT_VC_VERIFY_REQUESTS_CHANNEL_ID = 1476977094729793710

    def _vc_requests_channel_id() -> int:
        try:
            return int(DEFAULT_VC_VERIFY_REQUESTS_CHANNEL_ID)
        except Exception:
            return 0

    def _get_vc_channel(guild: discord.Guild) -> Optional[discord.VoiceChannel]:
        try:
            if not VC_VERIFY_CHANNEL_ID:
                return None
            ch = guild.get_channel(int(VC_VERIFY_CHANNEL_ID))
            if isinstance(ch, discord.VoiceChannel):
                return ch
        except Exception:
            pass
        return None

    async def _resolve_text_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.Messageable]:
        try:
            ch = guild.get_channel(int(channel_id))
            if ch and hasattr(ch, "send") and callable(getattr(ch, "send")):
                return ch  # type: ignore[return-value]
        except Exception:
            pass
        try:
            ch = await guild.fetch_channel(int(channel_id))
            if ch and hasattr(ch, "send") and callable(getattr(ch, "send")):
                return ch  # type: ignore[return-value]
        except Exception:
            pass
        return None

    async def _get_staff_alert_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
        return None

    def _can_manage_channel(me: discord.Member, channel: discord.abc.GuildChannel) -> Tuple[bool, str]:
        try:
            perms = channel.permissions_for(me)
            if perms.administrator or perms.manage_channels:
                return True, ""
            return False, f"Bot lacks Manage Channels in {channel.mention}"
        except Exception as e:
            return False, str(e)

    def _resolve_ticket_channel_from_token_info(
        guild: discord.Guild,
        token_info: Dict[str, Any],
    ) -> Optional[discord.TextChannel]:
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
        emb = discord.Embed(
            title="🎙️ VC Verification Requested",
            description="Staff-only panel — choose how to handle this VC request.",
            color=discord.Color.dark_green(),
            timestamp=now_utc(),
        )
        emb.add_field(name="User", value=f"{requester_mention}\n`{requester_id}`", inline=False)
        emb.add_field(name="Ticket", value=f"<#{int(ticket_channel_id)}>\n`{ticket_channel_id}`", inline=True)
        emb.add_field(
            name="VC Channel",
            value=(f"<#{int(VC_VERIFY_CHANNEL_ID)}>\n`{VC_VERIFY_CHANNEL_ID}`" if VC_VERIFY_CHANNEL_ID else "`Not configured`"),
            inline=True,
        )
        emb.add_field(name="Token", value=f"`{token}`", inline=False)
        emb.set_footer(text="Stoney Verify • VC staff panel")
        return emb

    async def _post_staff_vc_request_panel(
        *,
        guild: discord.Guild,
        token: str,
        requester_id: int,
        requester_mention: str,
        ticket_channel_id: int,
    ) -> Optional[int]:
        return None

    async def _cleanup_stale_vc_request(guild: discord.Guild, token: str, reason: str) -> bool:
        try:
            VC_REQUESTS.pop(str(token), None)
        except Exception:
            pass
        return True

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

    async def _vc_disable_panels_everywhere(guild: discord.Guild, token: str, status_text: str) -> None:
        return None

    async def _vc_grant_access(guild: discord.Guild, member: discord.Member, token: str) -> Tuple[bool, str]:
        return False, "vc_flow import missing"

    async def _vc_revoke_access(guild: discord.Guild, member: discord.Member, token: str, reason: str = "revoke") -> None:
        return None

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
        return False, "vc_flow import missing"

    async def _vc_unlock_channel_for_next_session(guild: discord.Guild, token: str) -> None:
        return None


try:
    from .identity_proof_service import record_verified_identity_for_user
except Exception:
    def record_verified_identity_for_user(  # type: ignore
        *,
        guild_id: Any,
        user_id: Any,
        identity_fingerprint: str,
        source: str,
        created_by: Optional[str] = None,
        fingerprint_version: str = "v1",
        confidence: int = 100,
        notes: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {}


_INTERACTION_HANDLERS_REGISTERED = False


def _role_by_id(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    try:
        if not guild or not role_id or int(role_id) <= 0:
            return None
        role = guild.get_role(int(role_id))
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _extract_identity_fingerprint(token_info: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(token_info, dict):
        return None

    keys = (
        "identity_fingerprint",
        "verification_fingerprint",
        "verified_identity_fingerprint",
        "proof_fingerprint",
        "document_fingerprint",
        "face_fingerprint",
        "person_fingerprint",
        "id_hash",
        "identity_hash",
    )
    for key in keys:
        try:
            value = _safe_str(token_info.get(key))
            if value:
                return value
        except Exception:
            continue
    return None


def _extract_identity_source(token_info: Optional[Dict[str, Any]], *, default: str) -> str:
    if not isinstance(token_info, dict):
        return default

    candidates = (
        _safe_str(token_info.get("identity_source")),
        _safe_str(token_info.get("verification_source")),
        _safe_str(token_info.get("proof_source")),
        _safe_str(token_info.get("source")),
        default,
    )

    allowed = {
        "manual_review",
        "id_verification",
        "voice_verification",
        "document_verification",
        "selfie_match",
        "external_account_link",
        "trusted_admin_override",
    }

    for candidate in candidates:
        text = candidate.lower().strip()
        if text in allowed:
            return text

    text = _safe_str(token_info.get("verification_source") or token_info.get("source")).lower()
    if "voice" in text or "vc" in text:
        return "voice_verification"
    if "document" in text or "id" in text:
        return "document_verification"
    if "selfie" in text or "face" in text:
        return "selfie_match"
    return default


async def _persist_identity_proof_on_approval(
    *,
    guild: discord.Guild,
    owner: Optional[discord.Member],
    token: str,
    token_info: Optional[Dict[str, Any]],
    staff_member: discord.Member,
    channel: discord.TextChannel,
    approval_mode: str,
) -> Tuple[bool, Optional[str]]:
    try:
        if not isinstance(owner, discord.Member):
            return False, None

        fingerprint = _extract_identity_fingerprint(token_info)
        if not fingerprint:
            return False, None

        source = _extract_identity_source(
            token_info,
            default=("voice_verification" if approval_mode == "vc" else "manual_review"),
        )

        evidence = {
            "token": token,
            "channel_id": str(channel.id),
            "guild_id": str(guild.id),
            "approved_by": str(staff_member.id),
            "approved_by_name": getattr(staff_member, "display_name", None) or getattr(staff_member, "name", None),
            "approval_mode": approval_mode,
            "decision": "APPROVED",
            "token_info_keys": sorted([str(k) for k in token_info.keys()]) if isinstance(token_info, dict) else [],
        }

        row = record_verified_identity_for_user(
            guild_id=str(guild.id),
            user_id=str(owner.id),
            identity_fingerprint=fingerprint,
            source=source,
            created_by=str(staff_member.id),
            fingerprint_version=_safe_str((token_info or {}).get("fingerprint_version")) or "v1",
            confidence=100,
            notes=f"Verification approved via {approval_mode} by {staff_member} ({staff_member.id})",
            evidence=evidence,
        )

        proof_id = _safe_str((row or {}).get("id")) or None
        return True, proof_id
    except Exception as e:
        print("⚠️ Failed persisting identity proof on approval:", repr(e))
        return False, str(e)


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


async def handle_possible_submission(message: discord.Message) -> None:
    if not isinstance(message.channel, discord.TextChannel):
        return
    if not is_verification_ticket_channel(message.channel):
        return
    if not message.guild:
        return

    me = message.guild.me
    if not me and bot.user:
        try:
            me = message.guild.get_member(bot.user.id) or await message.guild.fetch_member(bot.user.id)
        except Exception:
            me = None
    if not me:
        return

    perms = message.channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages):
        return

    if not getattr(message, "webhook_id", None):
        return

    token = extract_token_from_message(message)

    if not token:
        try:
            await asyncio.sleep(1.2)
            fresh = await message.channel.fetch_message(message.id)
            token = extract_token_from_message(fresh)
        except Exception:
            token = None

    if not token:
        return

    token_info = sb_get_token_info(token)
    if not token_info:
        return

    if token_is_expired(token_info):
        return

    ti_channel = str(token_info.get("channel_id") or "")
    if not ti_channel or ti_channel != str(message.channel.id):
        return

    ti_guild = str(token_info.get("guild_id") or "")
    if ti_guild and ti_guild != str(message.guild.id):
        return

    expected_wh_id = None
    webhook_url = token_info.get("webhook_url")
    if webhook_url:
        expected_wh_id = _parse_webhook_id_from_url(str(webhook_url))
        if expected_wh_id and int(getattr(message, "webhook_id", 0) or 0) != int(expected_wh_id):
            return
    else:
        print(f"⚠️ Token {token} has no webhook_url stored – proceeding without webhook check.")

    if token_info.get("used", False):
        return

    if token_info.get("submitted", False):
        return

    sb_set_submitted(token)
    try:
        sb_set_submitted_at(token, now_utc())
    except Exception:
        pass

    RUNTIME_STATS["submissions_seen"] += 1
    mark_ticket_activity(message.channel.id)

    try:
        now = now_utc()
        prune_after = max(10, SUBMISSION_DEDUPE_SECONDS * 3)

        for t, ts in list(RECENT_SUBMISSION_TOKENS.items()):
            if (now - ts).total_seconds() > prune_after:
                RECENT_SUBMISSION_TOKENS.pop(t, None)

        for mid, ts in list(RECENT_SUBMISSION_MSG_IDS.items()):
            if (now - ts).total_seconds() > prune_after:
                RECENT_SUBMISSION_MSG_IDS.pop(mid, None)

        if message.id in RECENT_SUBMISSION_MSG_IDS:
            return

        last = RECENT_SUBMISSION_TOKENS.get(token)
        if last and (now - last).total_seconds() < SUBMISSION_DEDUPE_SECONDS:
            return

        RECENT_SUBMISSION_TOKENS[token] = now
        RECENT_SUBMISSION_MSG_IDS[message.id] = now
    except Exception:
        pass

    try:
        _cancel_kick_timer(message.channel.id)
    except Exception:
        pass
    try:
        KICK_TIMER_TASKS.pop(message.channel.id, None)
        KICK_TIMER_STARTS.pop(message.channel.id, None)
        KICK_TIMER_STARTED_BY.pop(message.channel.id, None)
    except Exception:
        pass
    try:
        await kick_timer_persist_delete(int(message.channel.id))
    except Exception:
        pass

    if perms.read_message_history:
        try:
            async for m in message.channel.history(limit=50):
                if not m.author or not bot.user or m.author.id != bot.user.id:
                    continue
                if m.content and "🧾 **Staff Decision Panel**" in m.content:
                    try:
                        await m.delete(reason="Cleanup old staff decision panel (new submission)")
                    except Exception:
                        pass
        except Exception:
            pass

    owner = await find_ticket_owner_retry(message.channel)

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="✅ Approve",
        style=discord.ButtonStyle.success,
        custom_id=make_custom_id("approve", token),
    ))
    view.add_item(discord.ui.Button(
        label="⛔ Deny & Close",
        style=discord.ButtonStyle.danger,
        custom_id=make_custom_id("denyclose", token),
    ))
    view.add_item(discord.ui.Button(
        label="🔁 Request Resubmission",
        style=discord.ButtonStyle.secondary,
        custom_id=make_custom_id("resubmit", token),
    ))

    panel_msg = await message.channel.send(
        f"🧾 **Staff Decision Panel** for {(owner.mention if owner else 'this ticket')}:",
        view=view,
    )
    ACTIVE_DECISION_PANEL_MSG_ID[token] = int(panel_msg.id)
    RUNTIME_STATS["panels_posted"] += 1


async def _defer_ephemeral(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass


async def _handle_mod_quick_action(
    interaction: discord.Interaction,
    action: str,
    user_id: int,
    extra: str,
) -> bool:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return True

    if not is_staff(interaction.user):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Staff only.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Staff only.", ephemeral=True)
        except Exception:
            pass
        return True

    await _defer_ephemeral(interaction)

    guild = interaction.guild
    target = guild.get_member(int(user_id))
    if not target:
        await interaction.followup.send("❌ User not found (may have left).", ephemeral=True)
        return True

    me = guild.me
    if not me:
        await interaction.followup.send("❌ Bot member missing.", ephemeral=True)
        return True

    try:
        if me.top_role <= target.top_role and not me.guild_permissions.administrator:
            await interaction.followup.send("❌ I can’t act on that member (role hierarchy).", ephemeral=True)
            return True
    except Exception:
        pass

    try:
        if action == "ban":
            if not me.guild_permissions.ban_members:
                await interaction.followup.send("❌ Missing **Ban Members** permission.", ephemeral=True)
                return True
            await guild.ban(
                target,
                reason=f"QuickAction ban by {interaction.user} ({interaction.user.id})",
                delete_message_days=0,
            )
            RUNTIME_STATS["mod_actions"] += 1
            await interaction.followup.send(f"🔨 Banned {target.mention}.", ephemeral=True)
            return True

        if action == "kick":
            if not me.guild_permissions.kick_members:
                await interaction.followup.send("❌ Missing **Kick Members** permission.", ephemeral=True)
                return True
            await guild.kick(
                target,
                reason=f"QuickAction kick by {interaction.user} ({interaction.user.id})",
            )
            RUNTIME_STATS["mod_actions"] += 1
            await interaction.followup.send(f"👢 Kicked {target.mention}.", ephemeral=True)
            return True

        if action == "timeout":
            if not me.guild_permissions.moderate_members:
                await interaction.followup.send("❌ Missing **Moderate Members** permission.", ephemeral=True)
                return True
            mins = MOD_TIMEOUT_MINUTES
            try:
                if extra.startswith("m="):
                    mins = int(extra.replace("m=", "").strip())
            except Exception:
                mins = MOD_TIMEOUT_MINUTES
            until = now_utc() + timedelta(minutes=max(1, mins))
            await target.timeout(
                until,
                reason=f"QuickAction timeout by {interaction.user} ({interaction.user.id})",
            )
            RUNTIME_STATS["mod_actions"] += 1
            await interaction.followup.send(
                f"⏳ Timed out {target.mention} for {mins} minutes.",
                ephemeral=True,
            )
            return True

        await interaction.followup.send("❌ Unknown mod action.", ephemeral=True)
        return True
    except discord.Forbidden:
        await interaction.followup.send("❌ Forbidden (permissions/hierarchy).", ephemeral=True)
        return True
    except discord.HTTPException as e:
        await interaction.followup.send(f"❌ Discord API error: {e}", ephemeral=True)
        return True
    except Exception as e:
        await interaction.followup.send(f"❌ Unexpected error: {e}", ephemeral=True)
        return True


async def _handle_verify_ui_action(
    interaction: discord.Interaction,
    *,
    action: str,
    token: str,
    guild: discord.Guild,
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
) -> bool:
    token_info = sb_get_token_info(token)
    if not token_info:
        await interaction.followup.send("❌ Invalid or expired token.", ephemeral=True)
        return True

    if action != "sv:verify:reissue" and token_is_expired(token_info):
        await interaction.followup.send("❌ This token expired. Generate a new link.", ephemeral=True)
        return True

    if str(token_info.get("channel_id") or "") != str(channel.id):
        await interaction.followup.send("❌ That token doesn’t belong to this ticket.", ephemeral=True)
        return True

    ti_guild = str(token_info.get("guild_id") or "")
    if ti_guild and ti_guild != str(guild.id):
        await interaction.followup.send("❌ That token doesn’t belong to this server.", ephemeral=True)
        return True

    expected_uid = token_info.get("requester_id") or token_info.get("user_id")
    is_owner = False
    try:
        if expected_uid and int(str(expected_uid)) == int(interaction.user.id):
            is_owner = True
    except Exception:
        is_owner = False

    if not is_owner and owner:
        try:
            is_owner = int(owner.id) == int(interaction.user.id)
        except Exception:
            is_owner = False

    if action == "sv:verify:reissue":
        is_staff_user = isinstance(interaction.user, discord.Member) and is_staff(interaction.user)

        if not is_staff_user and not is_owner:
            await interaction.followup.send("❌ Only the **ticket owner** can reissue a token.", ephemeral=True)
            return True

        if not is_staff_user and not token_is_expired(token_info):
            await interaction.followup.send(
                "⛔ You already have an **active** upload link. You can reissue a new token **after it expires**.",
                ephemeral=True,
            )
            return True

        new_token = await post_or_replace_verify_ui(
            channel,
            requester_id=int(owner.id) if owner else (int(expected_uid) if expected_uid else None),
            reason=f"reissue:{interaction.user.id}",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=TOKEN_TTL_MINUTES,
            allow_regen=ALLOW_USER_VERIFYLINK,
        )
        if new_token:
            RUNTIME_STATS["ui_reissued"] += 1

        await interaction.followup.send(
            "✅ Reissued verify link." if new_token else "❌ Failed to reissue.",
            ephemeral=True,
        )
        return True

    if action in ("sv:verify:get", "sv:verify:raw", "sv:verify:vc") and not is_owner:
        await interaction.followup.send("❌ Only the **ticket owner** can use that.", ephemeral=True)
        return True

    if action == "sv:verify:regen":
        if not ALLOW_USER_VERIFYLINK:
            if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
                await interaction.followup.send("❌ Regen is staff-only.", ephemeral=True)
                return True
        else:
            if not is_owner:
                await interaction.followup.send(
                    "❌ Only the **ticket owner** can generate a new link.",
                    ephemeral=True,
                )
                return True

        new_token = await post_or_replace_verify_ui(
            channel,
            requester_id=int(owner.id) if owner else (int(expected_uid) if expected_uid else None),
            reason="regen_button",
            site_url=VERIFY_SITE_URL,
            ttl_minutes=TOKEN_TTL_MINUTES,
            allow_regen=ALLOW_USER_VERIFYLINK,
        )
        await interaction.followup.send(
            "✅ New link posted." if new_token else "❌ Failed to generate link.",
            ephemeral=True,
        )
        return True

    if token_info.get("used", False):
        await interaction.followup.send(
            "❌ That token has already been used. Generate a new link for a fresh token.",
            ephemeral=True,
        )
        return True

    link = build_verify_link(token)

    if action == "sv:verify:raw":
        RUNTIME_STATS["raw_link_clicks"] += 1
        await interaction.followup.send(
            "🔗 **Raw link (tap to reveal):**\n" + f"||<{link}>||",
            ephemeral=True,
        )
        return True

    if action == "sv:verify:get":
        RUNTIME_STATS["open_link_clicks"] += 1
        view = discord.ui.View(timeout=120)
        view.add_item(discord.ui.Button(
            label="Open Secure Upload",
            style=discord.ButtonStyle.link,
            url=link,
        ))
        await interaction.followup.send(
            "🔒 Here’s your secure upload link:",
            view=view,
            ephemeral=True,
        )
        return True

    if action == "sv:verify:vc":
        if not VC_VERIFY_CHANNEL_ID:
            await interaction.followup.send("❌ VC verification is not configured.", ephemeral=True)
            return True

        try:
            last = VC_REQUEST_COOLDOWNS.get(int(interaction.user.id))
            if last and (now_utc() - last).total_seconds() < VC_REQUEST_COOLDOWN_SECONDS:
                left = int(VC_REQUEST_COOLDOWN_SECONDS - (now_utc() - last).total_seconds())
                await interaction.followup.send(
                    f"⏳ Please wait **{left}s** before requesting VC verify again.",
                    ephemeral=True,
                )
                return True
            VC_REQUEST_COOLDOWNS[int(interaction.user.id)] = now_utc()
        except Exception:
            pass

        vc = _get_vc_channel(guild)
        if not vc:
            await interaction.followup.send(
                "❌ VC verification channel isn’t configured correctly.",
                ephemeral=True,
            )
            return True

        existing = VC_REQUESTS.get(token) or {}
        if existing.get("status") == "PENDING":
            await interaction.followup.send(
                "✅ VC request is already queued. Staff will respond soon.",
                ephemeral=True,
            )
            return True

        VC_REQUESTS[token] = {
            "status": "PENDING",
            "requested_at": now_utc().isoformat(),
            "requested_by": int(interaction.user.id),
            "ticket_channel_id": int(channel.id),
            "guild_id": int(guild.id),
            "staff_msg_ids": [],
        }
        RUNTIME_STATS["vc_requests"] += 1

        requester_mention = (owner.mention if owner else f"<@{int(interaction.user.id)}>")
        staff_mid = await _post_staff_vc_request_panel(
            guild=guild,
            token=token,
            requester_id=int(interaction.user.id),
            requester_mention=requester_mention,
            ticket_channel_id=int(channel.id),
        )
        if staff_mid:
            try:
                VC_REQUESTS[token]["staff_msg_ids"] = [int(staff_mid)]
            except Exception:
                pass

        try:
            if owner:
                await channel.send(
                    f"🎙️ {owner.mention} **VC verification request sent.**\n"
                    "Staff will respond here when they’re ready. Please wait."
                )
            else:
                await channel.send(
                    "🎙️ **VC verification request sent.**\n"
                    "Staff will respond here when they’re ready. Please wait."
                )
        except Exception:
            pass

        await interaction.followup.send(
            "✅ VC verification request queued. Staff will accept it when available.",
            ephemeral=True,
        )
        return True

    return False


async def _handle_vc_staff_action(
    interaction: discord.Interaction,
    *,
    action: str,
    token: str,
    guild: discord.Guild,
    token_info_q: Dict[str, Any],
) -> bool:
    if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await interaction.followup.send("❌ Staff only.", ephemeral=True)
        return True

    expired = token_is_expired(token_info_q)

    if action in ("vc_start", "vc_complete", "vc_cancel"):
        if expired and action != "vc_cancel":
            await _cleanup_stale_vc_request(guild, token, reason="expired before session control")
            await interaction.followup.send("❌ This VC request token is expired.", ephemeral=True)
            return True

        try:
            ticket_ch = _resolve_ticket_channel_from_token_info(guild, token_info_q)
        except Exception:
            ticket_ch = None

        rid = 0
        try:
            rid = int(str(token_info_q.get("requester_id") or token_info_q.get("user_id") or "0") or 0)
        except Exception:
            rid = 0

        member = None
        if rid:
            try:
                member = guild.get_member(rid) or await guild.fetch_member(rid)
            except Exception:
                member = None

        try:
            from . import vc_sessions  # type: ignore
        except Exception:
            vc_sessions = None  # type: ignore

        if action == "vc_start":
            try:
                if token in VC_REQUESTS and isinstance(VC_REQUESTS.get(token), dict):
                    VC_REQUESTS[token]["status"] = "STARTED"
                    VC_REQUESTS[token]["started_by"] = str(interaction.user.id)
                    VC_REQUESTS[token]["started_at"] = now_utc().isoformat()
            except Exception:
                pass

            try:
                if vc_sessions and hasattr(vc_sessions, "start_session"):
                    await vc_sessions.start_session(
                        guild_id=guild.id,
                        token=token,
                        ticket_channel_id=int(getattr(ticket_ch, "id", 0) or 0),
                        vc_channel_id=int(VC_VERIFY_CHANNEL_ID or 0),
                        user_id=rid,
                        staff_id=int(interaction.user.id),
                    )
            except Exception:
                pass

            if isinstance(ticket_ch, discord.TextChannel):
                try:
                    vc_id = int(VC_VERIFY_CHANNEL_ID or 0)
                    view = None
                    if vc_id:
                        try:
                            view = discord.ui.View(timeout=1800)
                            view.add_item(discord.ui.Button(
                                label="🎙️ Join ID-Verify VC",
                                style=discord.ButtonStyle.link,
                                url=_discord_channel_url(guild.id, vc_id),
                            ))
                        except Exception:
                            view = None

                    await ticket_ch.send(
                        f"🎙️ **VC session started.**\n"
                        f"{('<@%s>' % rid) if rid else ''} please join <#{vc_id}> when ready.\n"
                        "A staff member will meet you there.",
                        view=view,
                    )
                except Exception:
                    pass

            await interaction.followup.send("▶️ VC session started (user notified).", ephemeral=True)
            return True

        if member:
            try:
                await _vc_revoke_access(
                    guild,
                    member,
                    token,
                    reason=("vc_complete" if action == "vc_complete" else "vc_cancel"),
                )
            except Exception:
                pass

        decision = "APPROVED" if action == "vc_complete" else "CANCELED"
        try:
            sb_mark_decision(token, decision, int(interaction.user.id), approved_user_id=rid if decision == "APPROVED" else None)
        except Exception:
            pass

        try:
            if vc_sessions and hasattr(vc_sessions, "end_session"):
                await vc_sessions.end_session(
                    guild_id=guild.id,
                    token=token,
                    status=("COMPLETED" if action == "vc_complete" else "CANCELED"),
                    staff_id=int(interaction.user.id),
                )
        except Exception:
            pass

        try:
            await _cleanup_stale_vc_request(guild, token, reason=f"session {decision.lower()}")
        except Exception:
            pass

        await interaction.followup.send(
            ("🏁 VC session completed." if action == "vc_complete" else "❌ VC session canceled (access revoked)."),
            ephemeral=True,
        )
        return True

    if action == "vc_reissue":
        try:
            ticket_ch = _resolve_ticket_channel_from_token_info(guild, token_info_q)
        except Exception:
            ticket_ch = None

        if not ticket_ch:
            await _cleanup_stale_vc_request(guild, token, reason="ticket channel not found during reissue")
            await interaction.followup.send(
                "❌ I couldn't resolve the ticket channel for this VC request.\n"
                "The ticket may have been deleted. This request has been cleaned up.",
                ephemeral=True,
            )
            return True

        rid = 0
        try:
            rid = int(str(token_info_q.get("requester_id") or token_info_q.get("user_id") or "0") or 0)
        except Exception:
            rid = 0

        try:
            vc_ttl = int(globals().get("VC_REQUEST_TTL_MINUTES", 0) or 0)
        except Exception:
            vc_ttl = 0
        if vc_ttl <= 0:
            vc_ttl = max(20, int(TOKEN_TTL_MINUTES or 20))

        try:
            from .verify_ui import _issue_token_url  # type: ignore
            new_token, _ = await _issue_token_url(
                site_url=VERIFY_SITE_URL,
                guild=guild,
                channel=ticket_ch,
                requester_id=int(rid or 0),
                ttl_minutes=vc_ttl,
            )
            print(f"✅ Created new token {new_token} with TTL {vc_ttl}m")
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to reissue VC token: {e}", ephemeral=True)
            return True

        try:
            VC_REQUESTS[new_token] = {
                "status": "PENDING",
                "requested_at": now_utc().isoformat(),
                "requested_by": int(rid or 0),
                "ticket_channel_id": int(ticket_ch.id),
                "guild_id": int(guild.id),
                "reissued_from": token,
                "reissued_by": int(interaction.user.id),
            }
        except Exception:
            pass

        try:
            if interaction.message:
                staff_view = discord.ui.View(timeout=None)
                staff_view.add_item(discord.ui.Button(
                    label="✅ Accept VC Verify",
                    style=discord.ButtonStyle.success,
                    custom_id=make_custom_id("vc_accept", new_token),
                ))
                staff_view.add_item(discord.ui.Button(
                    label="🔁 Ask for Upload Instead",
                    style=discord.ButtonStyle.secondary,
                    custom_id=make_custom_id("vc_upload", new_token),
                ))
                staff_view.add_item(discord.ui.Button(
                    label="♻️ Reissue Token",
                    style=discord.ButtonStyle.secondary,
                    custom_id=make_custom_id("vc_reissue", new_token),
                ))

                owner2 = None
                try:
                    owner2 = await find_ticket_owner_retry(ticket_ch)
                except Exception:
                    owner2 = None

                requester_mention = f"<@{int(rid)}>" if rid else (owner2.mention if owner2 else f"<@{interaction.user.id}>")
                emb = _build_vc_staff_embed(
                    guild=guild,
                    requester_id=int(rid or 0),
                    requester_mention=requester_mention,
                    ticket_channel_id=int(ticket_ch.id),
                    token=new_token,
                )
                emb.set_footer(text=f"Stoney Verify • VC staff panel | TTL {vc_ttl}m")

                await interaction.message.edit(embed=emb, view=staff_view)
        except Exception:
            pass

        old_entry = VC_REQUESTS.get(token)
        if old_entry and old_entry.get("ticket_panel_msg_id"):
            ticket_panel_msg_id = old_entry["ticket_panel_msg_id"]
            if ticket_ch:
                try:
                    ticket_msg = await ticket_ch.fetch_message(ticket_panel_msg_id)
                    new_view = discord.ui.View(timeout=None)
                    new_view.add_item(discord.ui.Button(
                        label="✅ Approve (VC)",
                        style=discord.ButtonStyle.success,
                        custom_id=make_custom_id("vc_approve", new_token),
                    ))
                    new_view.add_item(discord.ui.Button(
                        label="⛔ Deny & Close (VC)",
                        style=discord.ButtonStyle.danger,
                        custom_id=make_custom_id("vc_denyclose", new_token),
                    ))
                    new_view.add_item(discord.ui.Button(
                        label="🧹 End VC Session",
                        style=discord.ButtonStyle.secondary,
                        custom_id=make_custom_id("vc_end", new_token),
                    ))
                    await ticket_msg.edit(view=new_view)

                    if new_token not in VC_REQUESTS:
                        VC_REQUESTS[new_token] = {}
                    VC_REQUESTS[new_token]["ticket_panel_msg_id"] = ticket_panel_msg_id
                except Exception as e:
                    print(f"⚠️ Failed to update ticket panel during reissue: {e}")

        await interaction.followup.send(
            f"✅ Reissued VC token.\nOld: `{token}`\nNew: `{new_token}`\nTicket: {ticket_ch.mention}",
            ephemeral=True,
        )
        return True

    if expired and action != "vc_reissue":
        try:
            raw = token_info_q.get("expires_at")
            exp = _parse_iso_datetime(str(raw or ""))
            now = now_utc()
            dbg = f"expires_at={raw!r} parsed={exp.isoformat() if exp else None} now={now.isoformat()}"
        except Exception:
            dbg = ""
        msg = "❌ This VC token expired."
        if dbg:
            msg += f"\n`{dbg}`"
        msg += "\nUse ♻️ **Reissue Token** or run `/vc_reissue` to generate a fresh VC token."
        await interaction.followup.send(msg, ephemeral=True)
        return True

    ticket_ch = _resolve_ticket_channel_from_token_info(guild, token_info_q)
    if not ticket_ch:
        await _cleanup_stale_vc_request(guild, token, reason="ticket channel not found")
        await interaction.followup.send(
            "❌ Could not resolve the ticket channel for this VC request.\n"
            "The ticket may have been deleted. This request has been cleaned up.",
            ephemeral=True,
        )
        return True

    channel = ticket_ch
    owner = await find_ticket_owner_retry(channel)

    if action == "vc_upload":
        RUNTIME_STATS["vc_upload_requested"] += 1
        try:
            if owner:
                await _vc_revoke_access(guild, owner, token, reason="upload-requested")
        except Exception:
            pass

        try:
            await post_or_replace_verify_ui(
                channel,
                requester_id=int(owner.id) if owner else None,
                reason=f"vc_upload_requested:{interaction.user.id}",
                site_url=VERIFY_SITE_URL,
                ttl_minutes=TOKEN_TTL_MINUTES,
                allow_regen=ALLOW_USER_VERIFYLINK,
            )
        except Exception:
            pass

        try:
            await _vc_disable_panels_everywhere(
                guild,
                token,
                status_text=f"Upload requested by {interaction.user.mention}",
            )
        except Exception:
            pass

        try:
            if interaction.message:
                await interaction.message.edit(
                    content="✅ VC request handled: staff requested upload instead.",
                    view=None,
                )
        except Exception:
            pass

        if owner:
            await channel.send(
                f"🔁 {owner.mention} Staff requested **secure upload** instead. "
                "Use the **Get Secure Upload** button above."
            )
        else:
            await channel.send(
                "🔁 Staff requested **secure upload** instead. "
                "Use the **Get Secure Upload** button above."
            )

        try:
            req = VC_REQUESTS.get(token) or {}
            VC_REQUESTS[token] = {
                **req,
                "status": "UPLOAD_REQUESTED",
                "handled_by": int(interaction.user.id),
                "handled_at": now_utc().isoformat(),
            }
        except Exception:
            pass

        await interaction.followup.send(
            "✅ Requested upload instead (ticket stays open).",
            ephemeral=True,
        )
        return True

    if action == "vc_accept":
        token_info = sb_get_token_info(token)
        if token_info and token_info.get("used", False):
            await interaction.followup.send("❌ This token has already been used.", ephemeral=True)
            return True

        req = VC_REQUESTS.get(token) or {}
        if req.get("status") == "ACCEPTED" and int(req.get("accepted_by", 0) or 0) != int(interaction.user.id):
            await interaction.followup.send(
                "❌ Another staff member already accepted this VC request.",
                ephemeral=True,
            )
            return True

        if not owner:
            await interaction.followup.send(
                "❌ Could not detect ticket owner for VC verification.",
                ephemeral=True,
            )
            return True

        VC_REQUESTS[token] = {
            **req,
            "status": "ACCEPTED",
            "accepted_by": int(interaction.user.id),
            "accepted_at": now_utc().isoformat(),
        }
        RUNTIME_STATS["vc_accepted"] += 1

        vc_ch = _get_vc_channel(guild)
        if not vc_ch:
            await interaction.followup.send("❌ VC verification channel not found.", ephemeral=True)
            return True

        me = guild.me
        if not me:
            await interaction.followup.send("❌ Bot member missing.", ephemeral=True)
            return True

        ok, perm_msg = _can_manage_channel(me, vc_ch)
        if not ok:
            await interaction.followup.send(
                f"❌ Bot lacks required permissions: {perm_msg}",
                ephemeral=True,
            )
            return True

        ok, msg = await _vc_grant_access(guild, owner, token)
        if not ok:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)
            return True

        try:
            await _vc_disable_panels_everywhere(
                guild,
                token,
                status_text=f"Accepted by {interaction.user.mention}",
            )
        except Exception:
            pass

        try:
            if interaction.message:
                await interaction.message.edit(
                    content=f"✅ Claimed by {interaction.user.mention}.",
                    view=None,
                )
        except Exception:
            pass

        staff_controls = discord.ui.View(timeout=None)
        staff_controls.add_item(discord.ui.Button(
            label="✅ Approve (VC)",
            style=discord.ButtonStyle.success,
            custom_id=make_custom_id("vc_approve", token),
        ))
        staff_controls.add_item(discord.ui.Button(
            label="⛔ Deny & Close (VC)",
            style=discord.ButtonStyle.danger,
            custom_id=make_custom_id("vc_denyclose", token),
        ))
        staff_controls.add_item(discord.ui.Button(
            label="🧹 End VC Session",
            style=discord.ButtonStyle.secondary,
            custom_id=make_custom_id("vc_end", token),
        ))

        ticket_channel: Optional[discord.TextChannel] = None
        try:
            tcid = int((req.get("ticket_channel_id") or 0))
            ch = guild.get_channel(tcid) if guild and tcid else None
            if not isinstance(ch, discord.TextChannel) and guild and tcid:
                try:
                    ch = await guild.fetch_channel(tcid)
                except Exception:
                    ch = None
            if isinstance(ch, discord.TextChannel):
                ticket_channel = ch
        except Exception:
            ticket_channel = None

        try:
            if ticket_channel:
                view = None
                try:
                    view = discord.ui.View(timeout=1800)
                    view.add_item(discord.ui.Button(
                        label="🎙️ Join ID-Verify VC",
                        style=discord.ButtonStyle.link,
                        url=_discord_channel_url(guild.id, vc_ch.id),
                    ))
                except Exception:
                    view = None

                access_min = 30
                try:
                    access_min = int(globals().get("VC_VERIFY_ACCESS_MINUTES", 30) or 30)
                except Exception:
                    access_min = 30

                oid = int(getattr(owner, "id", 0) or 0) if owner else 0
                user_mention = owner.mention if owner else (f"<@{oid}>" if oid else "Unknown user")
                content_msg = (
                    f"✅ **VC Verify accepted** by {interaction.user.mention}\n\n"
                    f"{user_mention} tap below to join <#{vc_ch.id}> now.\n"
                    f"⏳ Temporary access expires in ~{access_min} minutes."
                )

                edited = False
                try:
                    me_id = int(getattr(getattr(bot, "user", None), "id", 0) or 0)
                    async for msg in ticket_channel.history(limit=50):
                        if int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                            continue
                        t = (msg.content or "")
                        if (
                            "VC verification request sent" in t
                            or "VC request sent" in t
                            or "Staff has been notified" in t
                            or "VC Verify accepted" in t
                        ):
                            await msg.edit(content=content_msg, view=view)
                            edited = True
                            break
                except Exception:
                    edited = False

                if not edited:
                    await ticket_channel.send(content_msg, view=view)
        except Exception:
            pass

        try:
            if getattr(interaction, "message", None):
                await interaction.message.edit(
                    content=f"✅ VC verify accepted by {interaction.user.mention} for <@{owner.id}>.",
                    embed=None,
                    view=staff_controls,
                )
                msg_obj = interaction.message
            else:
                msg_obj = await interaction.channel.send(
                    content=f"✅ VC verify accepted by {interaction.user.mention} for <@{owner.id}>.",
                    view=staff_controls,
                )

            VC_REQUESTS[token]["staff_panel_msg_id"] = int(getattr(msg_obj, "id", 0) or 0)
        except Exception:
            pass

        await interaction.followup.send(
            "✅ Accepted VC verify and granted temporary access.",
            ephemeral=True,
        )
        return True

    if action == "vc_end":
        if owner:
            await _vc_revoke_access(guild, owner, token, reason="ended-by-staff")
        RUNTIME_STATS["vc_ended"] += 1
        await interaction.followup.send("🧹 VC session ended (access revoked).", ephemeral=True)
        return True

    async with _get_lock(token):
        token_info = sb_get_token_info(token)
        if not token_info:
            await interaction.followup.send("❌ Invalid token.", ephemeral=True)
            return True

        if token_info.get("used", False):
            await interaction.followup.send("❌ This token has already been used.", ephemeral=True)
            return True

        if not owner:
            sb_mark_decision(token, "APPROVED (VC) (owner not detected)", int(interaction.user.id))
            await interaction.followup.send(
                "✅ Saved decision but owner wasn’t detected.",
                ephemeral=True,
            )
            return True

        try:
            await _vc_revoke_access(guild, owner, token, reason="decision-made")
        except Exception:
            pass

        if action == "vc_denyclose":
            sb_mark_decision(token, "DENIED (VC)", int(interaction.user.id))
            try:
                sb_set_used(token, True)
            except Exception:
                pass
            RUNTIME_STATS["vc_denied"] += 1
            await interaction.followup.send("⛔ **Denied (VC)** (saved).", ephemeral=True)
            try:
                await _vc_unlock_channel_for_next_session(guild, token)
            except Exception:
                pass
            await auto_close_after_decision(channel, closer=interaction.user, decision="DENIED (VC)")
            return True

        can_assign, error_msg, roles_to_assign = await check_bot_can_assign_roles(guild)
        if not can_assign:
            sb_mark_decision(token, "APPROVED (VC) (roles failed)", int(interaction.user.id))
            try:
                sb_set_used(token, True)
            except Exception:
                pass
            await interaction.followup.send(
                f"❌ **Cannot assign roles:** {error_msg}\nDecision saved, roles not granted.",
                ephemeral=True,
            )
            await auto_close_after_decision(
                channel,
                closer=interaction.user,
                decision="APPROVED (VC) (roles failed)",
            )
            return True

        try:
            await owner.add_roles(
                *roles_to_assign,
                reason=f"Stoney Verify approved via VC by {interaction.user} ({interaction.user.id})",
            )
            _, remove_error = await _remove_unverified_role_if_present(
                owner,
                reason=f"Stoney Verify VC approval cleanup by {interaction.user} ({interaction.user.id})",
            )
            if remove_error:
                sb_mark_decision(
                    token,
                    "APPROVED (VC) (unverified cleanup failed)",
                    int(interaction.user.id),
                )
                await interaction.followup.send(
                    f"❌ Roles were added, but removing **Unverified** failed: {remove_error}",
                    ephemeral=True,
                )
                return True
        except discord.Forbidden:
            sb_mark_decision(token, "APPROVED (VC) (role add failed)", int(interaction.user.id))
            await interaction.followup.send(
                "❌ I can’t add roles (check Manage Roles + hierarchy).",
                ephemeral=True,
            )
            return True
        except Exception as e:
            await interaction.followup.send(f"❌ Unexpected error: {e}", ephemeral=True)
            return True

        proof_saved, proof_meta = await _persist_identity_proof_on_approval(
            guild=guild,
            owner=owner,
            token=token,
            token_info=token_info,
            staff_member=interaction.user,
            channel=channel,
            approval_mode="vc",
        )

        sb_mark_decision(token, "APPROVED (VC)", int(interaction.user.id), approved_user_id=int(owner.id))
        try:
            sb_set_used(token, True)
        except Exception:
            pass
        RUNTIME_STATS["vc_approved"] += 1

        msg = f"✅ **Approved (VC)!** Granted {', '.join([r.mention for r in roles_to_assign])} to {owner.mention}."
        if proof_saved and proof_meta:
            msg += f"\n🧬 Stored hard identity proof (`{proof_meta}`) for future confirmed-duplicate checks."
        elif proof_saved:
            msg += "\n🧬 Stored hard identity proof for future confirmed-duplicate checks."

        await interaction.followup.send(msg, ephemeral=True)
        try:
            await _vc_unlock_channel_for_next_session(guild, token)
        except Exception:
            pass
        await auto_close_after_decision(channel, closer=interaction.user, decision="APPROVED (VC)")
        return True


async def _handle_standard_staff_decision(
    interaction: discord.Interaction,
    *,
    action: str,
    token: str,
    guild: discord.Guild,
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
) -> bool:
    if action not in ("approve", "denyclose", "resubmit"):
        return False

    if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
        await interaction.followup.send("❌ Staff only.", ephemeral=True)
        return True

    try:
        bound_id = ACTIVE_DECISION_PANEL_MSG_ID.get(token)
        if bound_id and interaction.message and int(interaction.message.id) != int(bound_id):
            await interaction.followup.send(
                "❌ That decision panel is stale. Use the latest panel.",
                ephemeral=True,
            )
            return True
    except Exception:
        pass

    token_info = sb_get_token_info(token)
    if not token_info:
        await interaction.followup.send("❌ Invalid or expired token.", ephemeral=True)
        return True

    if token_is_expired(token_info):
        await interaction.followup.send("❌ This token expired. Generate a new link.", ephemeral=True)
        return True

    if str(token_info.get("channel_id") or "") != str(channel.id):
        await interaction.followup.send(
            "❌ That decision token doesn’t belong to this ticket.",
            ephemeral=True,
        )
        return True

    ti_guild = str(token_info.get("guild_id") or "")
    if ti_guild and ti_guild != str(guild.id):
        await interaction.followup.send(
            "❌ That decision token doesn’t belong to this server.",
            ephemeral=True,
        )
        return True

    if not owner:
        try:
            expected_uid = token_info.get("requester_id") or token_info.get("user_id")
            if expected_uid:
                owner = guild.get_member(int(str(expected_uid)))
        except Exception:
            owner = None

    async with _get_lock(token):
        token_info = sb_get_token_info(token)
        if not token_info:
            await interaction.followup.send("❌ Invalid token.", ephemeral=True)
            return True

        if token_is_expired(token_info):
            await interaction.followup.send("❌ This token expired. Generate a new link.", ephemeral=True)
            return True

        if token_info.get("used", False):
            await interaction.followup.send("❌ This token has already been used.", ephemeral=True)
            return True

        if action == "denyclose":
            sb_mark_decision(token, "DENIED", int(interaction.user.id))
            try:
                sb_set_used(token, True)
            except Exception:
                pass
            RUNTIME_STATS["denied"] += 1
            await interaction.followup.send(
                "⛔ **Denied** (saved). Ticket will close automatically if auto-close is enabled.",
                ephemeral=True,
            )
            await auto_close_after_decision(channel, closer=interaction.user, decision="DENIED")
            return True

        if action == "resubmit":
            sb_mark_decision(token, "RESUBMIT REQUESTED", int(interaction.user.id))
            RUNTIME_STATS["resubmit"] += 1

            try:
                await post_or_replace_verify_ui(
                    channel,
                    requester_id=int(owner.id) if owner else None,
                    reason=f"resubmit_requested:{interaction.user.id}",
                    site_url=VERIFY_SITE_URL,
                    ttl_minutes=TOKEN_TTL_MINUTES,
                    allow_regen=ALLOW_USER_VERIFYLINK,
                )
                if owner:
                    await channel.send(
                        f"🔁 {owner.mention} Please **resubmit** your ID using the new secure upload button above."
                    )
                else:
                    await channel.send(
                        "🔁 Please **resubmit** your ID using the new secure upload button above."
                    )
            except Exception as e:
                print("⚠️ resubmit flow failed:", e)

            await interaction.followup.send(
                "✅ Resubmission requested. A new link was posted and the ticket stays open.",
                ephemeral=True,
            )
            return True

        can_assign, error_msg, roles_to_assign = await check_bot_can_assign_roles(guild)
        if not can_assign:
            sb_mark_decision(token, "APPROVED (roles failed)", int(interaction.user.id))
            try:
                sb_set_used(token, True)
            except Exception:
                pass
            await interaction.followup.send(
                f"❌ **Cannot assign roles:** {error_msg}\n\n*Decision was saved but roles were not granted.*",
                ephemeral=True,
            )
            await auto_close_after_decision(
                channel,
                closer=interaction.user,
                decision="APPROVED (roles failed)",
            )
            return True

        if not owner:
            sb_mark_decision(token, "APPROVED (owner not detected)", int(interaction.user.id))
            try:
                sb_set_used(token, True)
            except Exception:
                pass
            await interaction.followup.send(
                "✅ Approved (saved) but I couldn't detect the ticket owner to grant roles.",
                ephemeral=True,
            )
            await auto_close_after_decision(
                channel,
                closer=interaction.user,
                decision="APPROVED (owner not detected)",
            )
            return True

        try:
            await owner.add_roles(
                *roles_to_assign,
                reason=f"Stoney Verify approved by {interaction.user} ({interaction.user.id})",
            )
            _, remove_error = await _remove_unverified_role_if_present(
                owner,
                reason=f"Stoney Verify approval cleanup by {interaction.user} ({interaction.user.id})",
            )
            if remove_error:
                sb_mark_decision(
                    token,
                    "APPROVED (unverified cleanup failed)",
                    int(interaction.user.id),
                )
                await interaction.followup.send(
                    f"❌ Roles were added, but removing **Unverified** failed: {remove_error}",
                    ephemeral=True,
                )
                return True
        except discord.Forbidden:
            sb_mark_decision(token, "APPROVED (role add failed)", int(interaction.user.id))
            await interaction.followup.send(
                "❌ I can't add roles. Fix my role position + permissions (**Manage Roles**) and try again.",
                ephemeral=True,
            )
            return True
        except Exception as e:
            await interaction.followup.send(f"❌ Unexpected error: {e}", ephemeral=True)
            return True

        proof_saved, proof_meta = await _persist_identity_proof_on_approval(
            guild=guild,
            owner=owner,
            token=token,
            token_info=token_info,
            staff_member=interaction.user,
            channel=channel,
            approval_mode="standard",
        )

        sb_mark_decision(token, "APPROVED", int(interaction.user.id), approved_user_id=int(owner.id))
        try:
            sb_set_used(token, True)
        except Exception:
            pass
        RUNTIME_STATS["approved"] += 1

        msg = f"✅ **Approved!** Granted {', '.join([r.mention for r in roles_to_assign])} to {owner.mention}."
        if proof_saved and proof_meta:
            msg += f"\n🧬 Stored hard identity proof (`{proof_meta}`) for future confirmed-duplicate checks."
        elif proof_saved:
            msg += "\n🧬 Stored hard identity proof for future confirmed-duplicate checks."

        await interaction.followup.send(msg, ephemeral=True)
        await auto_close_after_decision(channel, closer=interaction.user, decision="APPROVED")
        return True


def _known_component_action(action: str) -> bool:
    return action in {
        "approve",
        "denyclose",
        "resubmit",
        "vc_accept",
        "vc_upload",
        "vc_end",
        "vc_approve",
        "vc_denyclose",
        "vc_reissue",
        "vc_start",
        "vc_complete",
        "vc_cancel",
        "sv:verify:get",
        "sv:verify:raw",
        "sv:verify:regen",
        "sv:verify:vc",
        "sv:verify:reissue",
    }


async def handle_component_interaction(interaction: discord.Interaction) -> None:
    if interaction.type != discord.InteractionType.component:
        return

    data = interaction.data or {}
    custom_id = (data.get("custom_id", "") or "").strip()

    try:
        if isinstance(interaction.channel, discord.TextChannel):
            mark_ticket_activity(interaction.channel.id)
    except Exception:
        pass

    try:
        handled = await maybe_handle_verify_ui_interaction(interaction, site_url=SITE_URL)
        if handled:
            return
    except Exception:
        pass

    try:
        m_action, m_uid, m_extra = parse_mod_id(custom_id)
    except Exception:
        m_action, m_uid, m_extra = (None, None, "")

    if m_action and m_uid and interaction.guild and isinstance(interaction.user, discord.Member):
        handled = await _handle_mod_quick_action(interaction, m_action, int(m_uid), m_extra)
        if handled:
            return

    action, token = parse_custom_id(custom_id)
    if not action:
        return

    await _defer_ephemeral(interaction)

    guild = interaction.guild
    if not guild:
        await interaction.followup.send("❌ Invalid context (no guild).", ephemeral=True)
        return

    if action in VC_STAFF_ACTIONS:
        if not isinstance(interaction.user, discord.Member) or not is_staff(interaction.user):
            await interaction.followup.send("❌ Staff only.", ephemeral=True)
            return

        if not token:
            await interaction.followup.send("❌ Missing decision token.", ephemeral=True)
            return

        token_info_q = sb_get_token_info(token)
        if not token_info_q:
            await interaction.followup.send("❌ Invalid or expired token.", ephemeral=True)
            return

        handled = await _handle_vc_staff_action(
            interaction,
            action=action,
            token=token,
            guild=guild,
            token_info_q=token_info_q,
        )
        if handled:
            return

    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send("❌ Invalid context.", ephemeral=True)
        return

    if action not in VC_STAFF_ACTIONS and not is_verification_ticket_channel(channel):
        await interaction.followup.send("❌ Not a verification ticket channel.", ephemeral=True)
        return

    try:
        if interaction.message and bot.user and interaction.message.author and interaction.message.author.id != bot.user.id:
            await interaction.followup.send("❌ Invalid interaction source.", ephemeral=True)
            return
    except Exception:
        pass

    owner = await find_ticket_owner_retry(channel)

    if action in ("sv:verify:get", "sv:verify:raw", "sv:verify:regen", "sv:verify:vc", "sv:verify:reissue"):
        if not token:
            await interaction.followup.send("❌ Invalid button (missing token).", ephemeral=True)
            return

        handled = await _handle_verify_ui_action(
            interaction,
            action=action,
            token=token,
            guild=guild,
            channel=channel,
            owner=owner,
        )
        if handled:
            return

    if action in ("approve", "denyclose", "resubmit"):
        handled = await _handle_standard_staff_decision(
            interaction,
            action=action,
            token=token or "",
            guild=guild,
            channel=channel,
            owner=owner,
        )
        if handled:
            return

    if not _known_component_action(action):
        try:
            if interaction.response.is_done():
                return
        except Exception:
            return
        await interaction.followup.send("❌ Invalid button.", ephemeral=True)
        return

    return


def register_interaction_handlers(bot_instance: Any) -> None:
    global _INTERACTION_HANDLERS_REGISTERED

    if _INTERACTION_HANDLERS_REGISTERED:
        try:
            print("ℹ️ interaction_handlers already registered; skipping duplicate registration.")
        except Exception:
            pass
        return

    @bot_instance.event
    async def on_interaction(interaction: discord.Interaction):
        await handle_component_interaction(interaction)

    _INTERACTION_HANDLERS_REGISTERED = True

    try:
        print("✅ interaction_handlers: registered component interaction handler")
    except Exception:
        pass


__all__ = [
    "handle_possible_submission",
    "handle_component_interaction",
    "register_interaction_handlers",
    "_remove_unverified_role_if_present",
]
