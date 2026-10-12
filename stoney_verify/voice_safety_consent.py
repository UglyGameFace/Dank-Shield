from __future__ import annotations

"""Versioned, independently auditable Voice Safety agreement.

Never infer consent from a role, age, server membership, or other feature's
preferences. Only an explicit currently-disclosed acknowledgement qualifies.
"""

from typing import Any

VOICE_SAFETY_AGREEMENT_KEY = "voice_safety_agreement_v1"
VOICE_SAFETY_DISCLOSURE_MARKER = "Dank Shield Voice Safety Agreement v1"


async def read_voice_safety_agreement(guild_id: int, user_id: int) -> bool:
    """Storage errors fail closed, never fall back to stale cache."""
    from .profile_card_service import get_profile_guild_settings

    try:
        row = await get_profile_guild_settings(int(guild_id), int(user_id), refresh=True)
        value = (row.get("settings") or {}).get(VOICE_SAFETY_AGREEMENT_KEY)
        return isinstance(value, dict) and value.get("accepted") is True and value.get("version") == 1
    except Exception:
        return False


async def save_voice_safety_agreement(guild_id: int, user_id: int, enabled: bool) -> bool:
    """Write + reread, raising if the intended choice cannot be confirmed."""
    from .profile_card_service import upsert_profile_guild_namespace

    value: dict[str, Any] = {"accepted": bool(enabled), "version": 1}
    result = await upsert_profile_guild_namespace(
        int(guild_id), int(user_id), VOICE_SAFETY_AGREEMENT_KEY, value
    )
    stored = (result.get("settings") or {}).get(VOICE_SAFETY_AGREEMENT_KEY)
    if not isinstance(stored, dict) or stored != value:
        raise RuntimeError("Voice Safety consent update could not be confirmed")
    return bool(enabled)


def verification_message_discloses_voice_safety(message: Any) -> bool:
    """No legacy Verify panel silently creates voice data-processing permission."""
    if message is None:
        return False
    for embed in getattr(message, "embeds", ()) or ():
        for field in getattr(embed, "fields", ()) or ():
            if VOICE_SAFETY_DISCLOSURE_MARKER in str(getattr(field, "name", "")):
                return True
    return False
