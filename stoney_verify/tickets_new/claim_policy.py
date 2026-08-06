from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class TicketActionDecision:
    allowed: bool
    code: str
    message: str
    actor_id: int
    owner_id: int
    claimed_by_id: int
    status: str
    action: str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _status(row: Optional[Mapping[str, Any]]) -> str:
    raw = str((row or {}).get("status") or "unknown").strip().lower()
    if raw in {"active", "reopened"}:
        return "open"
    return raw if raw in {"open", "claimed", "closed", "deleted"} else "unknown"


def ticket_owner_id(row: Optional[Mapping[str, Any]]) -> int:
    for key in ("user_id", "owner_id", "requester_id"):
        value = _safe_int((row or {}).get(key), 0)
        if value > 0:
            return value
    return 0


def ticket_claimed_by_id(row: Optional[Mapping[str, Any]]) -> int:
    for key in ("assigned_to", "claimed_by"):
        value = _safe_int((row or {}).get(key), 0)
        if value > 0:
            return value
    return 0


def ticket_guild_id(row: Optional[Mapping[str, Any]]) -> int:
    for key in ("guild_id", "server_id"):
        value = _safe_int((row or {}).get(key), 0)
        if value > 0:
            return value
    return 0


def ticket_has_transcript(row: Optional[Mapping[str, Any]]) -> bool:
    if not isinstance(row, Mapping):
        return False
    for key in (
        "transcript_url",
        "transcript_message_id",
        "transcript_channel_id",
    ):
        try:
            if str(row.get(key) or "").strip():
                return True
        except Exception:
            continue
    return False


def _cached_guild_owner_id(row: Optional[Mapping[str, Any]]) -> int:
    """Resolve the real Discord guild owner for a registered ticket.

    Ticket actions run while the bot is connected to the guild, so the guild is
    expected to be present in the bot cache. Failure to resolve the guild owner
    fails closed and preserves normal claimant enforcement.
    """
    gid = ticket_guild_id(row)
    if gid <= 0:
        return 0

    try:
        from stoney_verify.globals import bot

        guild = bot.get_guild(int(gid))
        if guild is None:
            return 0

        owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
        if owner_id > 0:
            return owner_id

        return _safe_int(getattr(getattr(guild, "owner", None), "id", 0), 0)
    except Exception:
        return 0


def is_staff_member(member: Any, *, staff_role_ids: tuple[int, ...] = ()) -> bool:
    """Return whether a human is subject to claim-first ticket enforcement.

    The ticket requester is excluded by the caller before this helper runs.
    Every other human who can reach a private ticket must therefore fail closed
    as a staff/participant actor, even when a server uses a renamed or
    server-specific support role that is not present in legacy environment
    configuration. This also prevents accidentally-added participants from
    bypassing the claimant lock. Bots remain excluded from message enforcement.
    """
    if member is None:
        return False

    try:
        if bool(getattr(member, "bot", False)):
            return False
    except Exception:
        pass

    try:
        permissions = member.guild_permissions
        if bool(permissions.administrator or permissions.manage_channels or permissions.manage_guild):
            return True
    except Exception:
        pass

    configured = {int(role_id) for role_id in staff_role_ids if _safe_int(role_id, 0) > 0}
    if configured:
        try:
            if any(int(role.id) in configured for role in (member.roles or [])):
                return True
        except Exception:
            pass

    # Fail closed. A non-requester human present in a private ticket may not
    # interact until they are the recorded claimant, regardless of role naming.
    return True


def evaluate_ticket_action(
    row: Optional[Mapping[str, Any]],
    *,
    actor_id: Any,
    action: str,
    system_action: bool = False,
    allow_requester_cancel: bool = False,
    guild_owner_id: Any = 0,
) -> TicketActionDecision:
    """Return the authoritative claim-first decision for one ticket action.

    Claimant ownership still controls every normal staff mutation. The actual
    Discord guild owner may use the separately confirmed ``owner_emergency_*``
    namespace for a narrow, audited lifecycle override. Normal close, transfer,
    unclaim, delete, reopen, notes, macros, and verification actions do not gain
    an owner/admin bypass. Safe emergency delete additionally requires a closed
    ticket with preserved transcript metadata.
    """
    clean_action = str(action or "action").strip().lower().replace(" ", "_")
    aid = _safe_int(actor_id, 0)
    owner_id = ticket_owner_id(row)
    claimed_by_id = ticket_claimed_by_id(row)
    status = _status(row)
    resolved_guild_owner_id = _safe_int(guild_owner_id, 0) or _cached_guild_owner_id(row)

    def decision(allowed: bool, code: str, message: str) -> TicketActionDecision:
        return TicketActionDecision(
            allowed=allowed,
            code=code,
            message=message,
            actor_id=aid,
            owner_id=owner_id,
            claimed_by_id=claimed_by_id,
            status=status,
            action=clean_action,
        )

    if not isinstance(row, Mapping):
        return decision(False, "ticket_not_found", "This ticket is not registered. Nothing was changed.")

    if system_action:
        return decision(True, "system_action", "Authorized internal ticket operation.")

    if aid <= 0:
        return decision(False, "actor_required", "A real Discord member is required for this ticket action.")

    if status == "deleted":
        return decision(False, "ticket_deleted", "This ticket is deleted and cannot be changed.")

    if clean_action.startswith("owner_emergency_"):
        if resolved_guild_owner_id <= 0 or aid != resolved_guild_owner_id:
            return decision(
                False,
                "guild_owner_required",
                "Only the actual Discord server owner can use Emergency Override.",
            )

        if clean_action == "owner_emergency_transfer":
            if status not in {"open", "claimed"}:
                return decision(False, "owner_emergency_transfer_not_open", "Only an open or claimed ticket can be force-transferred.")
            return decision(True, "owner_emergency_transfer_allowed", "Confirmed server-owner emergency transfer is allowed.")

        if clean_action == "owner_emergency_unclaim":
            if status not in {"open", "claimed"}:
                return decision(False, "owner_emergency_unclaim_not_open", "Only an open or claimed ticket can be force-unclaimed.")
            return decision(True, "owner_emergency_unclaim_allowed", "Confirmed server-owner emergency unclaim is allowed.")

        if clean_action == "owner_emergency_close":
            if status not in {"open", "claimed"}:
                return decision(False, "owner_emergency_close_not_open", "Only an open or claimed ticket can be emergency-closed.")
            return decision(True, "owner_emergency_close_allowed", "Confirmed server-owner emergency close is allowed.")

        if clean_action == "owner_emergency_delete_prepare":
            if status != "closed":
                return decision(False, "owner_emergency_delete_requires_closed", "Close the ticket before starting a safe emergency delete.")
            return decision(True, "owner_emergency_delete_prepare_allowed", "Safe emergency delete may prepare and verify a transcript.")

        if clean_action == "owner_emergency_delete":
            if status != "closed":
                return decision(False, "owner_emergency_delete_requires_closed", "Only a closed ticket can be emergency-deleted.")
            if not ticket_has_transcript(row):
                return decision(False, "owner_emergency_delete_requires_transcript", "A preserved transcript is required before emergency deletion.")
            return decision(True, "owner_emergency_delete_allowed", "Confirmed safe emergency delete is allowed.")

        return decision(False, "unknown_owner_emergency_action", "That Emergency Override action is not supported.")

    if clean_action == "claim":
        if status not in {"open", "claimed"}:
            return decision(False, "ticket_not_open", "Only an open ticket can be claimed.")
        if owner_id > 0 and aid == owner_id:
            return decision(False, "requester_cannot_claim", "The ticket requester cannot claim their own ticket.")
        if claimed_by_id <= 0:
            return decision(True, "claim_allowed", "Ticket may be claimed.")
        if claimed_by_id == aid:
            return decision(True, "already_claimed_by_actor", "You already claimed this ticket.")
        return decision(
            False,
            "claimed_by_other",
            f"This ticket is already claimed by <@{claimed_by_id}>. It must be transferred first.",
        )

    if clean_action in {"close", "cancel"} and allow_requester_cancel and aid == owner_id:
        if status == "open" and claimed_by_id <= 0:
            return decision(True, "requester_cancel_allowed", "Requester may cancel their unclaimed ticket.")
        if claimed_by_id > 0:
            return decision(
                False,
                "requester_cancel_after_claim",
                "This ticket is already claimed. Ask the assigned staff member to close it.",
            )
        return decision(False, "requester_cancel_not_open", "Only an open unclaimed ticket can be cancelled.")

    if aid == owner_id:
        return decision(
            False,
            "requester_action_forbidden",
            "The ticket requester can provide information, but cannot use this staff action.",
        )

    if claimed_by_id <= 0:
        return decision(
            False,
            "claim_required",
            "Claim this ticket first. Claim is the only staff action allowed while it is unclaimed.",
        )

    if aid != claimed_by_id:
        return decision(
            False,
            "claimant_required",
            f"Only the current claimant <@{claimed_by_id}> can do that. Transfer the ticket first.",
        )

    if clean_action == "delete" and status != "closed":
        return decision(False, "close_before_delete", "Close the ticket first, then use Delete as a separate action.")

    if clean_action == "reopen" and status != "closed":
        return decision(False, "reopen_requires_closed", "Only a closed ticket can be reopened.")

    if status == "closed" and clean_action not in {
        "delete",
        "reopen",
        "view_info",
        "view_notes",
        "transcript",
    }:
        return decision(False, "ticket_closed", "This ticket is closed. Reopen it before using that action.")

    if status not in {"open", "claimed", "closed"}:
        return decision(False, "invalid_status", "The ticket lifecycle state is invalid. Nothing was changed.")

    return decision(True, "claimant_allowed", "Authorized current claimant action.")


__all__ = [
    "TicketActionDecision",
    "evaluate_ticket_action",
    "is_staff_member",
    "ticket_claimed_by_id",
    "ticket_guild_id",
    "ticket_has_transcript",
    "ticket_owner_id",
]
