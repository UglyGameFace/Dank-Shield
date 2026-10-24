from __future__ import annotations

"""Bounded read-only saved-policy checks for the existing owner directory.

A saved "enabled" policy is NOT proof a worker is running, a target is due,
a private PostgreSQL ledger is accessible, or a message has been delivered.
The original feature modules remain the only owners of delivery and settings.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from .action_notification_owners import NOTIFICATION_OWNERS

CHECK_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class NotificationPolicyStatus:
    key: str
    state: str
    detail: str


async def _verification_policy(guild_id: int) -> NotificationPolicyStatus:
    from .guild_config import get_guild_config
    from .verification_reminders import parse_policy

    cfg = await get_guild_config(guild_id, refresh=True)
    if not isinstance(cfg, Mapping) or str(cfg.get("source") or "").startswith(
        ("unavailable:", "unconfigured:", "env_fallback")
    ):
        raise RuntimeError("Verification configuration unavailable")
    policy = parse_policy(cfg)
    return NotificationPolicyStatus(
        "verification",
        "saved_enabled" if policy.enabled else "saved_disabled",
        "Saved Simple Verify reminder policy only; delivery depends on current "
        "verification prerequisites and private PostgreSQL health.",
    )


async def _quiet_policy(guild_id: int) -> NotificationPolicyStatus:
    from .community_quiet_notice_service import get_quiet_notice

    cfg = await get_quiet_notice(guild_id)
    if cfg is None:
        return NotificationPolicyStatus(
            "quiet", "not_configured", "No saved quiet-server notice for this guild."
        )
    if type(cfg.enabled) is not bool or int(cfg.guild_id) != guild_id:
        raise RuntimeError("Malformed quiet notice configuration")
    return NotificationPolicyStatus(
        "quiet", "saved_enabled" if cfg.enabled else "saved_disabled",
        "Saved quiet-server notice policy only; channel access and runtime health "
        "are checked by Community Tools.",
    )


async def _world420_policy(guild_id: int) -> NotificationPolicyStatus:
    from .somewhere_420_schedules import get_schedule

    cfg = await get_schedule("guild", guild_id)
    if cfg is None:
        return NotificationPolicyStatus(
            "world420", "not_configured",
            "No guild-wide 4:20 schedule saved. Personal member reminders are separate.",
        )
    if not isinstance(cfg, Mapping) or type(cfg.get("enabled")) is not bool:
        raise RuntimeError("Malformed guild-wide 4:20 schedule")
    return NotificationPolicyStatus(
        "world420", "saved_enabled" if cfg["enabled"] else "saved_disabled",
        "Guild-wide saved schedule only. Personal subscriptions are not inspected "
        "and actual send health is not verified.",
    )


_READERS = {
    "verification": _verification_policy,
    "quiet": _quiet_policy,
    "world420": _world420_policy,
}


async def inspect_saved_notification_status(guild_id: int) -> tuple[NotificationPolicyStatus, ...]:
    """Inspect only one guild. No member scans, claims, DB writes or sends."""
    if type(guild_id) is not int or guild_id <= 0:
        raise ValueError("A real guild ID is required")

    async def read(key: str):
        try:
            return await asyncio.wait_for(_READERS[key](guild_id), timeout=CHECK_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never leak backend exception text, credentials or hidden member data.
            return NotificationPolicyStatus(
                key, "unavailable", "Saved configuration could not be verified."
            )

    pending = tuple(_READERS)
    checked = await asyncio.gather(*(read(key) for key in pending))
    by_key = {state.key: state for state in checked}

    return tuple(
        by_key.get(owner.key) or NotificationPolicyStatus(
            owner.key, "owner_managed",
            "Not inspected here. Open the existing feature for its own current "
            "settings, session, notices or delivery results.",
        )
        for owner in NOTIFICATION_OWNERS
    )
