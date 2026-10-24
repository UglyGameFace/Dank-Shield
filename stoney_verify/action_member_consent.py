from __future__ import annotations

"""Private, per-member per-guild consent for FUTURE Member Setup reminder DMs.

This namespace is independent of verification reminders, profile visibility and
4:20 opt-outs. Default is OFF. Automatic Member Setup delivery can use the explicit opt-in after fresh owner
checks and durable PostgreSQL reservation. Read failures never imply consent.
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


@dataclass(frozen=True)
class MemberSetupOptInPage:
    """Private candidate identifiers, NOT eligible-to-send or a due queue."""
    user_ids: tuple[int, ...] = ()
    next_after_user_id: int | None = None

    def __post_init__(self) -> None:
        if len(self.user_ids) > 10 or len(set(self.user_ids)) != len(self.user_ids):
            raise ValueError("Opt-in candidate page limit exceeded")
        if any(type(uid) is not int or uid <= 0 for uid in self.user_ids):
            raise ValueError("Invalid opt-in candidate")
        if self.next_after_user_id is not None and (
            not self.user_ids or self.next_after_user_id != self.user_ids[-1]
        ):
            raise ValueError("Invalid opt-in pagination cursor")


async def read_member_setup_opt_in_page(
    guild_id: int, *, after_user_id: int = 0, limit: int = 5,
) -> MemberSetupOptInPage:
    """Only valid explicitly opted-in profiles, using the existing JSONB store.

    Returns a bounded source feed for the opt-in-only Member Setup worker.
    This read does NOT fetch a member, inspect eligibility, claim or send.
    The sender independently rechecks consent and owner revision before and
    after durable PostgreSQL reservation.
    """
    from .profile_card_service import list_profile_guild_settings_namespace_page

    if type(guild_id) is not int or guild_id <= 0:
        raise ValueError("Valid guild required")
    if type(after_user_id) is not int or after_user_id < 0:
        raise ValueError("Invalid opt-in cursor")
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Candidate page size must be 1–10")
    rows = await list_profile_guild_settings_namespace_page(
        guild_id,
        namespace=KEY,
        enabled_key="member_setup_dm",
        after_user_id=after_user_id,
        limit=limit,
    )
    users: list[int] = []
    for row in rows:
        try:
            consent = parse_consent(row["settings"])
            uid = int(row["user_id"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RuntimeError("Malformed saved opt-in candidate page") from None
        if not consent.member_setup_dm or uid <= 0:
            raise RuntimeError("Opt-in candidate not confirmed")
        users.append(uid)
    return MemberSetupOptInPage(
        tuple(users),
        # A full page might have more; a shorter page definitely ends the scan.
        next_after_user_id=users[-1] if len(users) == limit else None,
    )


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
