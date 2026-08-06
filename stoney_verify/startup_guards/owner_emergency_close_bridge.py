from __future__ import annotations

"""Route confirmed owner emergency close through the full ticket lifecycle.

The canonical close service intentionally authorizes ordinary ``close`` as a
claimant action. The Emergency Override service has already authorized the
separate ``owner_emergency_close`` action, so this adapter invokes the lifecycle
as an explicitly scoped internal close and restores the real owner attribution
in the visible controls and audit feed.
"""

from typing import Any

from ..tickets_new.owner_emergency_override import is_actual_guild_owner

_PATCHED = False
_PREFIX = "Owner emergency override:"


def _name(actor: Any) -> str:
    try:
        return str(actor)
    except Exception:
        return "Discord guild owner"


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from ..tickets_new import service
        from ..tickets_new.explicit_system_action_guard import (
            explicit_ticket_system_action,
        )
    except Exception as exc:
        print(f"⚠️ owner_emergency_close_bridge import failed: {exc!r}")
        return False

    original = getattr(service, "mark_ticket_closed", None)
    if not callable(original):
        return False
    if bool(getattr(original, "_owner_emergency_close_bridge", False)):
        _PATCHED = True
        return True

    async def wrapped_mark_ticket_closed(*args: Any, **kwargs: Any) -> bool:
        channel = kwargs.get("channel")
        actor = kwargs.get("closed_by")
        reason = str(kwargs.get("reason") or "").strip()
        guild = getattr(channel, "guild", None)

        is_confirmed_owner_override = bool(
            channel is not None
            and actor is not None
            and reason.startswith(_PREFIX)
            and is_actual_guild_owner(guild, actor)
        )
        if not is_confirmed_owner_override:
            return bool(await original(*args, **kwargs))

        system_kwargs = dict(kwargs)
        system_kwargs["closed_by"] = None
        async with explicit_ticket_system_action("confirmed-owner-emergency-close"):
            closed = bool(await original(*args, **system_kwargs))

        if not closed:
            return False

        try:
            await service._freeze_open_ticket_controls_safe(channel, closed_by=actor)
        except Exception:
            pass
        try:
            await service._post_staff_closed_message_safe(channel, closed_by=actor)
        except Exception:
            pass

        try:
            row = await service._ticket_row_for_channel_id(channel.id)
            from ..tickets_new.event_service import log_ticket_closed

            await log_ticket_closed(
                guild_id=channel.guild.id,
                actor_user_id=actor.id,
                actor_name=_name(actor),
                channel_id=channel.id,
                reason=reason.removeprefix(_PREFIX).strip(),
                ticket_row=row,
                source="tickets_new_owner_emergency_close",
                metadata={
                    "owner_emergency_override": True,
                    "allow_duplicate_event": True,
                },
            )
        except Exception:
            pass

        return True

    setattr(wrapped_mark_ticket_closed, "_owner_emergency_close_bridge", True)
    setattr(service, "mark_ticket_closed", wrapped_mark_ticket_closed)
    _PATCHED = True
    print("✅ owner_emergency_close_bridge: confirmed owner close lifecycle active")
    return True


apply()

__all__ = ["apply"]
