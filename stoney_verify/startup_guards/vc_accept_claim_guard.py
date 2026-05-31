from __future__ import annotations

"""Require canonical ticket claiming before VC verification accept.

VC Accept must not be a side door around the ticket system. When staff accepts a
VC verification request, the ticket should become claimed by that staff member
first, using tickets_new.service.assign_ticket so DB state, activity logs, and
claimed-by display all stay consistent.
"""

from typing import Any, Dict, Optional

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ vc_accept_claim_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ vc_accept_claim_guard: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _claimed_by_id(row: Optional[Dict[str, Any]]) -> int:
    if not isinstance(row, dict):
        return 0
    for key in ("assigned_to", "claimed_by"):
        value = _safe_int(row.get(key), 0)
        if value > 0:
            return value
    return 0


async def _safe_ticket_notice(channel: Optional[discord.TextChannel], content: str) -> None:
    try:
        if isinstance(channel, discord.TextChannel):
            await channel.send(content, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    except Exception:
        pass


async def _claim_ticket_before_accept(
    *,
    voice_mod: Any,
    guild: discord.Guild,
    token: str,
    staff_member: discord.Member,
) -> Dict[str, Any]:
    try:
        from ..tickets_new.repository import get_ticket_by_any_channel_id
        from ..tickets_new.service import assign_ticket
    except Exception as e:
        return voice_mod._result(False, f"Ticket claim system unavailable: {type(e).__name__}: {e}")

    ctx = await voice_mod.resolve_vc_context(
        guild=guild,
        token=str(token or "").strip(),
        allow_expired=False,
    )
    if not ctx.get("ok"):
        return ctx

    ticket_channel = ctx.get("channel")
    owner = ctx.get("owner")

    if not isinstance(ticket_channel, discord.TextChannel):
        return voice_mod._result(False, "Could not resolve the verification ticket channel.")

    row = None
    try:
        row = await get_ticket_by_any_channel_id(ticket_channel.id)
    except Exception as e:
        _warn(f"ticket row lookup failed channel={ticket_channel.id}: {type(e).__name__}: {e}")
        row = None

    if not isinstance(row, dict):
        return voice_mod._result(
            False,
            "VC request was not accepted because this ticket has no DB row to claim. Run ticket sync/health check, then try again.",
            channel=ticket_channel,
            owner=owner,
        )

    claimed_by = _claimed_by_id(row)
    staff_id = int(staff_member.id)

    if claimed_by > 0 and claimed_by != staff_id:
        return voice_mod._result(
            False,
            f"This ticket is already claimed by <@{claimed_by}>. Transfer or unclaim it before accepting the VC request.",
            channel=ticket_channel,
            owner=owner,
        )

    if claimed_by == staff_id:
        return voice_mod._result(True, "Ticket already claimed by this staff member.", channel=ticket_channel, owner=owner)

    ok = False
    try:
        ok = bool(await assign_ticket(channel_id=ticket_channel.id, staff_member=staff_member))
    except Exception as e:
        _warn(f"assign_ticket crashed channel={ticket_channel.id} staff={staff_id}: {type(e).__name__}: {e}")
        ok = False

    if not ok:
        return voice_mod._result(
            False,
            "VC request was not accepted because the ticket could not be claimed first.",
            channel=ticket_channel,
            owner=owner,
        )

    await _safe_ticket_notice(ticket_channel, f"🎯 Ticket claimed by {staff_member.mention} via VC Accept.")
    return voice_mod._result(True, "Ticket claimed for VC accept.", channel=ticket_channel, owner=owner)


def apply() -> bool:
    try:
        from ..verification_new import voice_verify as voice_mod
    except Exception as e:
        _warn(f"could not import verification_new.voice_verify: {e!r}")
        return False

    if getattr(voice_mod, "_VC_ACCEPT_CLAIM_GUARD_APPLIED", False):
        return True

    original = getattr(voice_mod, "accept_vc_request", None)
    if not callable(original):
        _warn("accept_vc_request is not callable")
        return False

    async def guarded_accept_vc_request(
        *,
        guild: discord.Guild,
        token: str,
        staff_member: discord.Member,
        queue_message: Optional[discord.Message] = None,
    ) -> Dict[str, Any]:
        claim_result = await _claim_ticket_before_accept(
            voice_mod=voice_mod,
            guild=guild,
            token=token,
            staff_member=staff_member,
        )
        if not claim_result.get("ok"):
            return claim_result

        return await original(
            guild=guild,
            token=token,
            staff_member=staff_member,
            queue_message=queue_message,
        )

    try:
        setattr(guarded_accept_vc_request, "_vc_accept_claim_guard_wrapped", True)
        setattr(voice_mod, "accept_vc_request", guarded_accept_vc_request)
        setattr(voice_mod, "_VC_ACCEPT_CLAIM_GUARD_APPLIED", True)
        _log("patched VC Accept to require canonical ticket claim first")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
