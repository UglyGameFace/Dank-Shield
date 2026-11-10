from __future__ import annotations

"""Versioned, independently auditable Voice Safety agreement.

Membership is the opt-in, decided by the server owner: being a member of the
server authorizes Voice Safety monitoring in the monitored voice channel, and
leaving the server ends it. No per-session click and no stored agreement
record are required for admission. The only thing that blocks admission for a
member who is still in the server is a standing explicit decline, recorded
under ``voice_safety_decline_v1`` by the Decline & Exit flow.

Agreement records remain as the audit trail: the Verify click
(``basic_verify_v1``), a notice agreement (``voice_safety_notice_v1``), and
the one-time grandfathering for members who were already verified when the
disclosed Verify panel arrived (``membership_grandfather_v1``). A leave
writes a withdrawal record, but a leave withdrawal is not a decline —
rejoining opts the member back in under the membership rule. Historic
``server_membership_v2`` records never authorize anything on their own and
are only ever converted to the grandfather source.
"""

from datetime import datetime, timezone
from typing import Any

VOICE_SAFETY_AGREEMENT_KEY = "voice_safety_agreement_v1"
VOICE_SAFETY_DISCLOSURE_MARKER = "Dank Shield Voice Safety Agreement v1"
VOICE_SAFETY_MEMBERSHIP_SOURCE = "server_membership_v2"
VOICE_SAFETY_GRANDFATHER_SOURCE = "membership_grandfather_v1"
VOICE_SAFETY_DECLINE_SOURCE = "voice_safety_decline_v1"
_ALLOWED_AGREEMENT_SOURCES = frozenset(
    {"basic_verify_v1", "voice_safety_notice_v1", VOICE_SAFETY_GRANDFATHER_SOURCE, VOICE_SAFETY_DECLINE_SOURCE}
)


async def read_voice_safety_agreement(guild_id: int, user_id: int) -> bool:
    """Report whether a stored explicit agreement record exists.

    This is the audit-trail read used by the decline grace flow; it is NOT
    the admission gate — membership authorizes admission. Failures read as
    no record."""
    from .profile_card_service import get_profile_guild_settings

    try:
        row = await get_profile_guild_settings(int(guild_id), int(user_id), refresh=True)
        value = (row.get("settings") or {}).get(VOICE_SAFETY_AGREEMENT_KEY)
        return bool(
            isinstance(value, dict)
            and value.get("accepted") is True
            and value.get("version") == 1
            and value.get("source") in _ALLOWED_AGREEMENT_SOURCES
            and isinstance(value.get("acted_at"), str)
            and bool(value.get("acted_at"))
        )
    except Exception:
        return False


async def save_voice_safety_agreement(
    guild_id: int, user_id: int, enabled: bool, *, source: str = "voice_safety_notice_v1"
) -> bool:
    """Write + reread, raising if the intended choice cannot be confirmed."""
    from .profile_card_service import upsert_profile_guild_namespace

    if source not in _ALLOWED_AGREEMENT_SOURCES:
        raise ValueError("Unknown Voice Safety acknowledgement source")
    value: dict[str, Any] = {
        "accepted": bool(enabled), "version": 1, "source": source,
        "acted_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await upsert_profile_guild_namespace(
        int(guild_id), int(user_id), VOICE_SAFETY_AGREEMENT_KEY, value
    )
    stored = (result.get("settings") or {}).get(VOICE_SAFETY_AGREEMENT_KEY)
    if not isinstance(stored, dict) or stored != value:
        raise RuntimeError("Voice Safety consent update could not be confirmed")
    return bool(enabled)


async def _read_agreement_record(guild_id: int, user_id: int) -> dict[str, Any] | None:
    """Raw stored agreement record, or None; storage errors read as no record."""
    from .profile_card_service import get_profile_guild_settings

    try:
        row = await get_profile_guild_settings(int(guild_id), int(user_id), refresh=True)
        value = (row.get("settings") or {}).get(VOICE_SAFETY_AGREEMENT_KEY)
        return value if isinstance(value, dict) else None
    except Exception:
        return None


async def has_voice_safety_decline(guild_id: int, user_id: int) -> bool:
    """True only when a standing explicit decline blocks admission.

    A decline is a stored record with accepted=False under the decline source
    (``voice_safety_decline_v1``), written by the Decline & Exit flow. A leave
    withdrawal uses the notice source and is NOT a decline: rejoining opts the
    member back in under the membership rule. Storage errors read as no
    decline — membership authorizes admission, so an unreadable store cannot
    silently re-impose a click-to-opt-in gate.
    """
    record = await _read_agreement_record(guild_id, user_id)
    return bool(
        isinstance(record, dict)
        and record.get("accepted") is False
        and record.get("source") == VOICE_SAFETY_DECLINE_SOURCE
    )


def _member_has_role(member: Any, role_id: Any) -> bool:
    """True when the member holds the given role id (config stores it as text)."""
    try:
        wanted = int(role_id)
    except (TypeError, ValueError):
        return False
    for role in getattr(member, "roles", None) or []:
        try:
            if int(getattr(role, "id", 0)) == wanted:
                return True
        except (TypeError, ValueError):
            continue
    return False


async def record_grandfather_agreement(guild_id: int, user_id: int) -> bool:
    """One-time grandfathering for an already-verified member.

    Returns True when a voice agreement is in force afterwards. An existing
    explicit record (Verify click or member notice) is preserved untouched.
    A declined record is a decision and is never overwritten. A legacy
    membership-v2 acceptance is converted to the grandfather source, so the
    audit trail shows how the agreement arose. A member with no record gets a
    fresh grandfather record.
    """
    if await read_voice_safety_agreement(guild_id, user_id):
        return True
    existing = await _read_agreement_record(guild_id, user_id)
    if isinstance(existing, dict) and existing.get("accepted") is True:
        if existing.get("source") == VOICE_SAFETY_MEMBERSHIP_SOURCE:
            return await save_voice_safety_agreement(
                guild_id, user_id, True, source=VOICE_SAFETY_GRANDFATHER_SOURCE
            )
        return False
    if isinstance(existing, dict) and existing.get("accepted") is False:
        return False
    return await save_voice_safety_agreement(
        guild_id, user_id, True, source=VOICE_SAFETY_GRANDFATHER_SOURCE
    )


async def withdraw_agreement_on_leave(guild_id: int, user_id: int) -> bool:
    """Opt-out by leaving: withdraw agreement when a member leaves the guild.

    Only writes when an affirmative record exists, so routine leaves for
    never-agreed members do not create storage noise. Returns True when a
    withdrawal was recorded. A leave withdrawal uses the notice source, not
    the decline source, so it never blocks re-admission: rejoining opts the
    member back in under the membership rule.
    """
    if not await read_voice_safety_agreement(guild_id, user_id):
        return False
    await save_voice_safety_agreement(
        guild_id, user_id, False, source="voice_safety_notice_v1"
    )
    return True


async def sync_guild_membership_agreement(
    guild: Any, *, members: Any = None, verified_role_id: Any = None
) -> dict[str, int]:
    """One-time grandfathering for already-verified members only.

    ``verified_role_id`` is the guild's configured Verified role; members
    without it are skipped, as are bots, members with an existing admissible
    agreement, and members who declined. Without a verified role id the
    backfill cannot establish who verified, so it records nothing (fail
    closed). Per-member failures are counted so a retry can pick them up.
    """
    recorded = skipped = failed = 0
    member_list = list(members if members is not None else (getattr(guild, "members", None) or ()))
    for member in member_list:
        try:
            if getattr(member, "bot", False):
                skipped += 1
                continue
            if not _member_has_role(member, verified_role_id):
                skipped += 1
                continue
            if await read_voice_safety_agreement(guild.id, member.id):
                skipped += 1
                continue
            if await record_grandfather_agreement(guild.id, member.id):
                recorded += 1
            else:
                skipped += 1
        except Exception:
            failed += 1
    return {"recorded": recorded, "skipped": skipped, "failed": failed}


def verification_message_discloses_voice_safety(message: Any) -> bool:
    """No legacy Verify panel silently creates voice data-processing permission."""
    if message is None:
        return False
    for embed in getattr(message, "embeds", ()) or ():
        for field in getattr(embed, "fields", ()) or ():
            if VOICE_SAFETY_DISCLOSURE_MARKER in str(getattr(field, "name", "")):
                return True
    return False
