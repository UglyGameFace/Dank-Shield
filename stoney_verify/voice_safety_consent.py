from __future__ import annotations

"""Versioned, independently auditable Voice Safety agreement.

Voice audio processing requires an affirmative, disclosed, voice-specific action.
Historic membership-v2 records may exist but can never authorize the receiver.
Leaving the server withdraws any explicit voice agreement.
"""

from datetime import datetime, timezone
from typing import Any

VOICE_SAFETY_AGREEMENT_KEY = "voice_safety_agreement_v1"
VOICE_SAFETY_DISCLOSURE_MARKER = "Dank Shield Voice Safety Agreement v1"
VOICE_SAFETY_MEMBERSHIP_SOURCE = "server_membership_v2"
_ALLOWED_AGREEMENT_SOURCES = frozenset(
    {"basic_verify_v1", "voice_safety_notice_v1"}
)


async def read_voice_safety_agreement(guild_id: int, user_id: int) -> bool:
    """Only explicit member actions authorize voice processing; failures deny."""
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
    """Legacy API; membership must never manufacture voice-specific consent."""
    del guild_id, user_id
    return False


async def withdraw_agreement_on_leave(guild_id: int, user_id: int) -> bool:
    """Opt-out by leaving: withdraw agreement when a member leaves the guild.

    Only writes when an affirmative record exists, so routine leaves for
    never-agreed members do not create storage noise. Returns True when a
    withdrawal was recorded.
    """
    if not await read_voice_safety_agreement(guild_id, user_id):
        return False
    await save_voice_safety_agreement(
        guild_id, user_id, False, source="voice_safety_notice_v1"
    )
    return True


async def sync_guild_membership_agreement(guild: Any, *, members: Any = None) -> dict[str, int]:
    """Legacy compatibility; never auto-grant listening consent to members."""
    member_list = members if members is not None else (getattr(guild, "members", None) or ())
    return {"recorded": 0, "skipped": sum(1 for _ in member_list), "failed": 0}


def verification_message_discloses_voice_safety(message: Any) -> bool:
    """No legacy Verify panel silently creates voice data-processing permission."""
    if message is None:
        return False
    for embed in getattr(message, "embeds", ()) or ():
        for field in getattr(embed, "fields", ()) or ():
            if VOICE_SAFETY_DISCLOSURE_MARKER in str(getattr(field, "name", "")):
                return True
    return False
