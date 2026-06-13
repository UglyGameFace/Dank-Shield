from __future__ import annotations

"""Post a clean staff-name claim note after voice verification approval.

The canonical claim guard owns the database mutation. This tiny follow-up guard
only improves what staff/members see in the ticket channel by including the staff
member's server display name.
"""

from typing import Any, Dict, Optional

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ voice_ticket_claim_notice_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ voice_ticket_claim_notice_guard: {message}")
    except Exception:
        pass


def _display_name(member: discord.Member) -> str:
    try:
        return str(getattr(member, "display_name", None) or getattr(member, "name", None) or member)[:160]
    except Exception:
        return "staff"


async def _send_claim_note(voice_mod: Any, guild: discord.Guild, token: str, staff_member: discord.Member) -> None:
    try:
        ctx = await voice_mod.resolve_vc_context(guild=guild, token=str(token or "").strip(), allow_expired=False)
    except Exception:
        return
    try:
        channel = ctx.get("channel") if isinstance(ctx, dict) else None
        if not isinstance(channel, discord.TextChannel):
            return
        display = _display_name(staff_member)
        await channel.send(
            f"🎯 Ticket claimed by {staff_member.mention} (`{display}`) during voice verification approval.",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except Exception:
        pass


def apply() -> bool:
    try:
        from ..verification_new import voice_verify as voice_mod
    except Exception as exc:
        _warn(f"could not import voice_verify: {exc!r}")
        return False

    if getattr(voice_mod, "_VOICE_TICKET_CLAIM_NOTICE_GUARD_APPLIED", False):
        return True

    original = getattr(voice_mod, "accept_vc_request", None)
    if not callable(original):
        _warn("voice approval function is unavailable")
        return False

    async def wrapped(
        *,
        guild: discord.Guild,
        token: str,
        staff_member: discord.Member,
        queue_message: Optional[discord.Message] = None,
    ) -> Dict[str, Any]:
        result = await original(guild=guild, token=token, staff_member=staff_member, queue_message=queue_message)
        try:
            if isinstance(result, dict) and result.get("ok"):
                await _send_claim_note(voice_mod, guild, token, staff_member)
        except Exception:
            pass
        return result

    try:
        setattr(wrapped, "_voice_ticket_claim_notice_wrapped", True)
        voice_mod.accept_vc_request = wrapped
        voice_mod._VOICE_TICKET_CLAIM_NOTICE_GUARD_APPLIED = True
        _log("patched voice verification ticket claim notice")
        return True
    except Exception as exc:
        _warn(f"patch failed: {exc!r}")
        return False


apply()

__all__ = ["apply"]
