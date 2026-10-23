from __future__ import annotations

"""Action Center 4C: conservative guild schedule policy and safe dry-run.

No worker reads this policy to send messages; saved delivery is ALWAYS off.
Completion truth and verification reminders remain owned by existing systems.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .action_builder_policy import parse_action_builder

KEY = "action_reminder_schedule_v1"
VERSION = 1
DELAYS = frozenset((12, 24, 48, 72))
REPEATS = frozenset((48, 72, 168))
MAX_ATTEMPTS = 3
MAX_GUILD_PER_DAY = 10


def checked_int(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Expected an integer between {minimum} and {maximum}")
    return value


def zone_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 90:
        raise ValueError("Choose a valid IANA timezone")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError("Unknown timezone, e.g. America/New_York") from exc
    return name


@dataclass(frozen=True)
class ActionSchedule:
    revision: int = 0
    first_delay_hours: int = 24
    repeat_hours: int = 72
    max_attempts: int = 2
    quiet_start: int = 22
    quiet_end: int = 8
    timezone: str = "UTC"
    max_guild_per_day: int = 5
    enabled: bool = False

    def __post_init__(self) -> None:
        checked_int(self.revision, 0, 2_147_483_647)
        checked_int(self.max_attempts, 1, MAX_ATTEMPTS)
        checked_int(self.max_guild_per_day, 1, MAX_GUILD_PER_DAY)
        checked_int(self.quiet_start, 0, 23)
        checked_int(self.quiet_end, 0, 23)
        if self.first_delay_hours not in DELAYS or self.repeat_hours not in REPEATS:
            raise ValueError("Choose a supported schedule preset")
        zone_name(self.timezone)
        if self.enabled is not False:
            raise ValueError("Schedule is planning-only; cannot activate delivery")

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": VERSION, "revision": self.revision,
            "first_delay_hours": self.first_delay_hours,
            "repeat_hours": self.repeat_hours,
            "max_attempts": self.max_attempts,
            "max_guild_per_day": self.max_guild_per_day,
            "quiet_start": self.quiet_start, "quiet_end": self.quiet_end,
            "timezone": self.timezone, "enabled": False, "delivery": "preview_only",
        }


def parse_schedule(config: Mapping[str, Any]) -> ActionSchedule:
    raw = config.get(KEY)
    if raw is None:
        return ActionSchedule()
    if not isinstance(raw, Mapping) or type(raw.get("version")) is not int or raw["version"] != VERSION:
        raise ValueError("Unsupported schedule schema")
    if raw.get("enabled") is not False or raw.get("delivery") != "preview_only":
        raise ValueError("Live scheduling is not enabled by 4C")
    keys = ("revision", "first_delay_hours", "repeat_hours", "max_attempts",
            "quiet_start", "quiet_end", "max_guild_per_day")
    if any(type(raw.get(key)) is not int for key in keys):
        raise ValueError("Malformed schedule number")
    return ActionSchedule(**{key: raw[key] for key in keys}, timezone=zone_name(raw.get("timezone")))


def edit_schedule(old: ActionSchedule, *, first_delay_hours: int, repeat_hours: int,
                  max_attempts: int, quiet_start: int, quiet_end: int,
                  timezone: str, max_guild_per_day: int) -> ActionSchedule:
    return replace(
        old, revision=old.revision + 1,
        first_delay_hours=first_delay_hours, repeat_hours=repeat_hours,
        max_attempts=max_attempts, quiet_start=quiet_start, quiet_end=quiet_end,
        timezone=zone_name(timezone), max_guild_per_day=max_guild_per_day,
    )


async def load_schedule(guild_id: int, *, refresh: bool = True) -> tuple[Mapping[str, Any], ActionSchedule]:
    from .guild_config import get_guild_config
    if int(guild_id) <= 0:
        raise ValueError("Guild required")
    cfg = await get_guild_config(guild_id, refresh=refresh)
    if not isinstance(cfg, Mapping) or str(cfg.get("source") or "").startswith("unavailable:"):
        raise RuntimeError("Guild config unavailable")
    parse_action_builder(cfg)
    return cfg, parse_schedule(cfg)


async def save_schedule(guild_id: int, *, expected_config: Mapping[str, Any],
                        schedule: ActionSchedule) -> tuple[bool, Mapping[str, Any]]:
    from .guild_config import compare_and_swap_guild_config_key
    if int(guild_id) <= 0:
        raise ValueError("Guild required")
    parse_action_builder(expected_config)
    current = parse_schedule(expected_config)
    if schedule.revision != current.revision + 1:
        raise ValueError("Stale schedule revision")
    return await compare_and_swap_guild_config_key(
        guild_id, KEY, expected=expected_config.get(KEY),
        value=schedule.to_payload(), source="action_center_schedule_draft",
    )


def in_quiet_hours(local_hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    return (start <= local_hour < end) if start < end else (local_hour >= start or local_hour < end)


@dataclass(frozen=True)
class ScheduleDecision:
    status: str
    next_at: datetime | None = None
    reason: str = ""


def preview_candidate(
    policy: ActionSchedule, *, condition: str, owner_status: str,
    now: datetime, joined_at: datetime | None,
    condition_started_at: datetime | None = None,
    last_claimed_at: datetime | None = None, attempts: int = 0,
    guild_claims_today: int = 0, included: bool = True,
) -> ScheduleDecision:
    """Informational only. No claim or send; unknown owner states never pass."""
    if condition not in ("pending_verification", "member_setup_revision"):
        return ScheduleDecision("blocked", reason="unknown condition")
    if owner_status == "unavailable":
        return ScheduleDecision("blocked", reason="source unavailable")
    if owner_status == "complete":
        return ScheduleDecision("complete", reason="canonical owner marked complete")
    if owner_status != "pending":
        return ScheduleDecision("blocked", reason="unknown owner status")
    if not included:
        return ScheduleDecision("excluded", reason="not included in this guild draft")
    if now.tzinfo is None or joined_at is None or joined_at.tzinfo is None:
        return ScheduleDecision("blocked", reason="missing timezone-aware join date")
    if condition == "member_setup_revision" and (
        condition_started_at is None or condition_started_at.tzinfo is None
    ):
        return ScheduleDecision("blocked", reason="published revision timestamp unavailable")
    if last_claimed_at is not None and last_claimed_at.tzinfo is None:
        return ScheduleDecision("blocked", reason="invalid claim timestamp")
    if attempts < 0 or guild_claims_today < 0:
        return ScheduleDecision("blocked", reason="invalid claim counters")
    if attempts >= policy.max_attempts:
        return ScheduleDecision("exhausted", reason="attempt limit")
    if guild_claims_today >= policy.max_guild_per_day:
        return ScheduleDecision("capped", reason="guild daily cap")
    instant = now.astimezone(timezone.utc)
    start = joined_at.astimezone(timezone.utc)
    if condition == "member_setup_revision":
        # An old member must not be nudged because a revision was published
        # today. Wait until the later of actual joining and publishing.
        start = max(start, condition_started_at.astimezone(timezone.utc))
    due = start + timedelta(hours=policy.first_delay_hours)
    if last_claimed_at is not None:
        due = max(due, last_claimed_at.astimezone(timezone.utc) + timedelta(hours=policy.repeat_hours))
    if instant < due:
        return ScheduleDecision("waiting", next_at=due, reason="delay/cooldown")
    zone = ZoneInfo(policy.timezone)
    if in_quiet_hours(instant.astimezone(zone).hour, policy.quiet_start, policy.quiet_end):
        # Step over actual UTC hours, not imaginary DST-local times. If the
        # quiet boundary is fractional this can be up to 59m conservative.
        for hour in range(1, 27):
            future = instant + timedelta(hours=hour)
            if not in_quiet_hours(future.astimezone(zone).hour, policy.quiet_start, policy.quiet_end):
                return ScheduleDecision("quiet", next_at=future, reason="server quiet hours")
        return ScheduleDecision("blocked", reason="quiet window cannot resolve")
    return ScheduleDecision("preview_due", next_at=instant, reason="dry-run only; no sending authorized")
