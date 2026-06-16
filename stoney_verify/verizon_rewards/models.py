from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


DEFAULT_REMINDER_OFFSETS_MINUTES: tuple[int, ...] = (30, 10, 1)

DEFAULT_PRIORITY_KEYWORDS: tuple[str, ...] = (
    "gift card",
    "daily drop",
    "epic wins",
    "presale",
    "ticket",
    "tickets",
    "fifa",
    "sweepstakes",
    "merch",
    "local pass",
)


@dataclass(slots=True)
class VerizonRewardConfig:
    guild_id: int
    alert_channel_id: int = 0
    enabled: bool = False
    reminders_enabled: bool = True
    reminder_offsets_minutes: tuple[int, ...] = DEFAULT_REMINDER_OFFSETS_MINUTES
    priority_keywords: tuple[str, ...] = DEFAULT_PRIORITY_KEYWORDS
    quiet_hours_start: Optional[str] = None
    quiet_hours_end: Optional[str] = None
    staff_only_commands: bool = True
    updated_by: str = ""

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | None, *, guild_id: int) -> "VerizonRewardConfig":
        if not row:
            return cls(guild_id=int(guild_id))

        def _int(key: str, default: int = 0) -> int:
            try:
                return int(str(row.get(key, default) or default).strip())
            except Exception:
                return default

        def _bool(key: str, default: bool = False) -> bool:
            value = row.get(key, default)
            if isinstance(value, bool):
                return value
            raw = str(value or "").strip().lower()
            if raw in {"1", "true", "yes", "y", "on"}:
                return True
            if raw in {"0", "false", "no", "n", "off"}:
                return False
            return default

        def _tuple_ints(key: str, default: tuple[int, ...]) -> tuple[int, ...]:
            value = row.get(key)
            if isinstance(value, (list, tuple)):
                out: list[int] = []
                for item in value:
                    try:
                        number = int(str(item).strip())
                    except Exception:
                        continue
                    if number >= 0 and number not in out:
                        out.append(number)
                return tuple(out) if out else default
            raw = str(value or "").strip()
            if raw:
                out = []
                for part in raw.split(","):
                    try:
                        number = int(part.strip())
                    except Exception:
                        continue
                    if number >= 0 and number not in out:
                        out.append(number)
                return tuple(out) if out else default
            return default

        def _tuple_strings(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
            value = row.get(key)
            if isinstance(value, (list, tuple)):
                items = [str(x or "").strip() for x in value]
            else:
                items = [part.strip() for part in str(value or "").split(",")]
            seen: set[str] = set()
            out: list[str] = []
            for item in items:
                clean = item.lower()
                if clean and clean not in seen:
                    seen.add(clean)
                    out.append(clean)
            return tuple(out) if out else default

        return cls(
            guild_id=_int("guild_id", int(guild_id)),
            alert_channel_id=_int("alert_channel_id", 0),
            enabled=_bool("enabled", False),
            reminders_enabled=_bool("reminders_enabled", True),
            reminder_offsets_minutes=_tuple_ints("reminder_offsets_minutes", DEFAULT_REMINDER_OFFSETS_MINUTES),
            priority_keywords=_tuple_strings("priority_keywords", DEFAULT_PRIORITY_KEYWORDS),
            quiet_hours_start=str(row.get("quiet_hours_start") or "").strip() or None,
            quiet_hours_end=str(row.get("quiet_hours_end") or "").strip() or None,
            staff_only_commands=_bool("staff_only_commands", True),
            updated_by=str(row.get("updated_by") or "").strip(),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "guild_id": str(int(self.guild_id)),
            "alert_channel_id": str(int(self.alert_channel_id or 0)) if self.alert_channel_id else None,
            "enabled": bool(self.enabled),
            "reminders_enabled": bool(self.reminders_enabled),
            "reminder_offsets_minutes": list(self.reminder_offsets_minutes),
            "priority_keywords": list(self.priority_keywords),
            "quiet_hours_start": self.quiet_hours_start,
            "quiet_hours_end": self.quiet_hours_end,
            "staff_only_commands": bool(self.staff_only_commands),
            "updated_by": self.updated_by or None,
            "updated_at": utc_now().isoformat(),
        }


@dataclass(slots=True)
class VerizonReward:
    reward_id: str
    guild_id: int
    title: str
    type: str = "Unknown"
    status: str = "unknown"
    source: str = "manual"
    first_seen_at: datetime = field(default_factory=lambda: utc_now())
    last_seen_at: datetime = field(default_factory=lambda: utc_now())
    available_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    priority: str = "normal"
    raw_text: str = ""
    fingerprint_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "VerizonReward":
        gid = int(str(row.get("guild_id") or 0))
        return cls(
            reward_id=str(row.get("reward_id") or row.get("id") or ""),
            guild_id=gid,
            title=str(row.get("title") or ""),
            type=str(row.get("type") or "Unknown"),
            status=str(row.get("status") or "unknown"),
            source=str(row.get("source") or "manual"),
            first_seen_at=parse_dt(row.get("first_seen_at")) or utc_now(),
            last_seen_at=parse_dt(row.get("last_seen_at")) or utc_now(),
            available_at=parse_dt(row.get("available_at")),
            expires_at=parse_dt(row.get("expires_at")),
            priority=str(row.get("priority") or "normal"),
            raw_text=str(row.get("raw_text") or ""),
            fingerprint_hash=str(row.get("fingerprint_hash") or ""),
            metadata=dict(row.get("metadata") or row.get("meta") or {}),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "reward_id": self.reward_id,
            "guild_id": str(int(self.guild_id)),
            "title": self.title,
            "type": self.type,
            "status": self.status,
            "source": self.source,
            "first_seen_at": dt_iso(self.first_seen_at),
            "last_seen_at": dt_iso(self.last_seen_at),
            "available_at": dt_iso(self.available_at),
            "expires_at": dt_iso(self.expires_at),
            "priority": self.priority,
            "raw_text": self.raw_text,
            "fingerprint_hash": self.fingerprint_hash,
            "metadata": dict(self.metadata or {}),
            "updated_at": utc_now().isoformat(),
        }


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        text = str(value).strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def dt_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return parse_dt(value).isoformat() if parse_dt(value) else None
