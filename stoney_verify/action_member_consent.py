from __future__ import annotations

"""Private, per-member per-guild consent for FUTURE Member Setup reminder DMs.

This namespace is independent of verification reminders, profile visibility and
4:20 opt-outs. Default is OFF. No worker/sender can use these settings yet.
Read failures must never become implicit consent.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

KEY = "action_member_reminders_v1"
VERSION = 1


@dataclass(frozen=True)
class MemberReminderConsent:
    member_setup_dm: bool = False
    updated_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "member_setup_dm": self.member_setup_dm,
            "updated_at": self.updated_at,
        }


def parse_consent(settings: Mapping[str, Any]) -> MemberReminderConsent:
    raw = settings.get(KEY)
    if raw is None:
        return MemberReminderConsent()
    if not isinstance(raw, Mapping) or type(raw.get("version")) is not int or raw.get("version") != VERSION:
        raise ValueError("Unknown reminder preference version; no delivery allowed")
    if type(raw.get("member_setup_dm")) is not bool:
        raise ValueError("Invalid reminder preference flag; no delivery allowed")
    updated = raw.get("updated_at")
    if not isinstance(updated, str) or not updated:
        raise ValueError("Reminder consent timestamp missing")
    try:
        stamp = datetime.fromisoformat(updated.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Reminder consent timestamp invalid") from exc
    if stamp.tzinfo is None:
        raise ValueError("Reminder consent timestamp has no timezone")
    return MemberReminderConsent(raw["member_setup_dm"], updated)


def _require_ids(guild_id: int, user_id: int) -> tuple[int, int]:
    if type(guild_id) is not int or type(user_id) is not int or guild_id <= 0 or user_id <= 0:
        raise ValueError("Valid server and member IDs required")
    return guild_id, user_id


async def read_member_consent(guild_id: int, user_id: int) -> MemberReminderConsent:
    """Fresh privacy read. Storage outage/unknown schema raises; no fallback."""
    from .profile_card_service import get_profile_guild_settings
    gid, uid = _require_ids(guild_id, user_id)
    record = await get_profile_guild_settings(gid, uid, refresh=True)
    settings = record.get("settings")
    if not isinstance(settings, Mapping):
        raise RuntimeError("Member preference storage returned no verified settings")
    return parse_consent(settings)


async def save_member_consent(guild_id: int, user_id: int, *, enabled: bool) -> MemberReminderConsent:
    """Only the caller's explicitly requested choice, persisted and reread."""
    from .profile_card_service import upsert_profile_guild_namespace
    gid, uid = _require_ids(guild_id, user_id)
    if type(enabled) is not bool:
        raise ValueError("Reminder preference must be on or off")
    # Check the existing version before overwriting to avoid deleting future
    # migration state. Network reads never silently turn into no preference.
    await read_member_consent(gid, uid)
    expected = MemberReminderConsent(
        member_setup_dm=enabled,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    row = await upsert_profile_guild_namespace(gid, uid, KEY, expected.to_payload())
    saved = (row.get("settings") or {}).get(KEY)
    if not isinstance(saved, Mapping) or dict(saved) != expected.to_payload():
        raise RuntimeError("Reminder preference save was not confirmed")
    confirmed = await read_member_consent(gid, uid)
    if confirmed != expected:
        raise RuntimeError("Reminder preference changed or cannot be verified")
    return confirmed
