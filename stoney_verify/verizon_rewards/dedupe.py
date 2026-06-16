from __future__ import annotations

from datetime import datetime
from typing import Optional

from .models import VerizonReward, VerizonRewardConfig, parse_dt, utc_now

PRIORITY_RANK = {"normal": 0, "watch": 1, "high": 2, "urgent": 3}


def priority_rank(value: object) -> int:
    return PRIORITY_RANK.get(str(value or "").strip().lower(), 0)


def should_alert(
    *,
    existing: Optional[VerizonReward],
    incoming: VerizonReward,
    config: Optional[VerizonRewardConfig] = None,
    now: Optional[datetime] = None,
) -> tuple[bool, str]:
    """Return whether this reward should be posted to Discord.

    Rules:
    - new reward: post
    - same unchanged fingerprint/status/timer/priority: suppress
    - status changes: post
    - priority increases: post
    - availability moves into the largest configured reminder window: post
    """
    if existing is None:
        return True, "new_reward"

    if existing.status != incoming.status:
        return True, "status_changed"

    if priority_rank(incoming.priority) > priority_rank(existing.priority):
        return True, "priority_increased"

    old_available = parse_dt(existing.available_at)
    new_available = parse_dt(incoming.available_at)
    if old_available != new_available and _timer_entered_alert_window(new_available, config=config, now=now):
        return True, "timer_entered_alert_window"

    same_fingerprint = existing.fingerprint_hash == incoming.fingerprint_hash
    same_timer = old_available == new_available
    same_priority = priority_rank(existing.priority) == priority_rank(incoming.priority)

    if same_fingerprint and same_timer and same_priority:
        return False, "unchanged_duplicate"

    return False, "stored_minor_change"


def _timer_entered_alert_window(
    available_at: Optional[datetime],
    *,
    config: Optional[VerizonRewardConfig],
    now: Optional[datetime],
) -> bool:
    when = parse_dt(available_at)
    if when is None:
        return False

    reference = parse_dt(now) or utc_now()
    delta_seconds = (when - reference).total_seconds()
    if delta_seconds < -60:
        return False

    offsets = tuple(config.reminder_offsets_minutes if config else (30, 10, 1))
    max_window = max(offsets or (30,))
    return delta_seconds <= max_window * 60
