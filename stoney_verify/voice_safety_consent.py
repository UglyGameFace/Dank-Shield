from __future__ import annotations

"""Versioned, independently auditable Voice Safety agreement.

Voice Safety is a membership rule in guilds that enable it: being a member of
the server constitutes agreement with the disclosed Voice Safety policy, and
leaving the server is the opt-out. Explicit button agreements (the verify
panel, the member notice) remain supported and are never overwritten by the
membership sync; they keep their original source and timestamp.
"""

from datetime import datetime, timezone
from typing import Any

VOICE_SAFETY_AGREEMENT_KEY = "voice_safety_agreement_v1"
VOICE_SAFETY_DISCLOSURE_MARKER = "Dank Shield Voice Safety Agreement v1"
VOICE_SAFETY_MEMBERSHIP_SOURCE = "server_membership_v2"
_ALLOWED_AGREEMENT_SOURCES = frozenset(
    {"basic_verify_v1", "voice_safety_notice_v1", VOICE_SAFETY_MEMBERSHIP_SOURCE}
)


async def read_voice_safety_agreement(guild_id: int, user_id: int) -> bool:
    """Storage errors fail closed, never fall back to stale cache."""
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


async def record_membership_agreement(guild_id: int, user_id: int) -> bool:
    """Affirmative agreement for a current member, keyed to server membership.

    Idempotent: members with an existing affirmative record (explicit button
    or earlier membership sync) are left untouched so their original source
    and timestamp are preserved. A stale declined record for a member who is
    still in the server is re-affirmed, because agreement is required to stay.
    """
    if await read_voice_safety_agreement(guild_id, user_id):
        return True
    return await save_voice_safety_agreement(
        guild_id, user_id, True, source=VOICE_SAFETY_MEMBERSHIP_SOURCE
    )


async def withdraw_agreement_on_leave(guild_id: int, user_id: int) -> bool:
    """Opt-out by leaving: withdraw agreement when a member leaves the guild.

    Only writes when an affirmative record exists, so routine leaves for
    never-agreed members do not create storage noise. Returns True when a
    withdrawal was recorded.
    """
    if not await read_voice_safety_agreement(guild_id, user_id):
        return False
    await save_voice_safety_agreement(
        guild_id, user_id, False, source=VOICE_SAFETY_MEMBERSHIP_SOURCE
    )
    return True


async def sync_guild_membership_agreement(guild: Any, *, members: Any = None) -> dict[str, int]:
    """Grandfather current members into agreement (one-time backfill).

    Iterates the supplied authoritative member snapshot when available (cached
    members otherwise), skipping bots, and records affirmative
    membership agreement for anyone lacking one. Explicit button agreements
    are preserved. Returns counts; never raises -- per-member failures are
    counted so a retry can pick them up.
    """
    recorded = skipped = failed = 0
    member_list = list(members if members is not None else (getattr(guild, "members", None) or []))
    for member in member_list:
        try:
            if getattr(member, "bot", False):
                skipped += 1
                continue
            if await read_voice_safety_agreement(guild.id, member.id):
                skipped += 1
                continue
            await save_voice_safety_agreement(
                guild.id, member.id, True, source=VOICE_SAFETY_MEMBERSHIP_SOURCE
            )
            recorded += 1
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
