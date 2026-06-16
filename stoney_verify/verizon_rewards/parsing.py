from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from .models import DEFAULT_PRIORITY_KEYWORDS, VerizonReward, utc_now

EASTERN = ZoneInfo("America/New_York")

TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Daily Drop", ("daily drop", "drop", "drops")),
    ("Epic Wins", ("epic wins", "epic win", "sweepstakes", "win big", "giveaway")),
    ("Presale", ("presale", "pre-sale", "early access")),
    ("Ticket Access", ("ticket", "tickets", "local pass", "local passes", "select seats", "featured venue")),
    ("Gift Card", ("gift card", "amazon", "fandango", "visa card", "egift", "e-gift")),
    ("Merch", ("merch", "merchandise", "shirt", "hoodie", "hat", "jersey")),
)

STATUS_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sold_out", ("sold out", "claimed out", "all claimed", "fully claimed", "no longer available")),
    ("expired", ("expired", "ended", "closed")),
    ("available", ("available now", "claim now", "open now", "now available", "available to claim")),
    ("coming_soon", ("coming soon", "drops", "drop starts", "available at", "opens at", "countdown", "starts in")),
)

TIME_RE = re.compile(r"\b(?P<hour>1[0-2]|0?[1-9])(?::(?P<minute>[0-5]\d))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)\b", re.I)
ISOISH_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?\b")
COUNTDOWN_RE = re.compile(
    r"(?:(?P<days>\d+)\s*d(?:ays?)?)?\s*(?:(?P<hours>\d+)\s*h(?:ours?)?)?\s*(?:(?P<minutes>\d+)\s*m(?:in(?:utes?)?)?)?\s*(?:(?P<seconds>\d+)\s*s(?:ec(?:onds?)?)?)?",
    re.I,
)
HHMMSS_RE = re.compile(r"\b(?P<hours>\d{1,2}):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)\b")
WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def normalize_text(value: object) -> str:
    text = str(value or "").lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9$:%@#.+\-/\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalized_title(value: object) -> str:
    text = normalize_text(value)
    text = re.sub(r"\b(verizon|shine|myaccess|my access|reward|rewards)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fingerprint_hash(title: str, status: str, source: str) -> str:
    payload = "|".join((normalized_title(title), normalize_text(status), normalize_text(source)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reward_id_for(guild_id: int, title: str, source: str) -> str:
    payload = f"{int(guild_id)}|{normalized_title(title)}|{normalize_text(source)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def classify_type(text: str) -> str:
    normalized = normalize_text(text)
    for label, keywords in TYPE_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return label
    return "Unknown"


def classify_status(text: str) -> str:
    normalized = normalize_text(text)
    for status, keywords in STATUS_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return status
    return "unknown"


def priority_for(text: str, keywords: Iterable[str] = DEFAULT_PRIORITY_KEYWORDS) -> str:
    normalized = normalize_text(text)
    hot = 0
    for keyword in keywords:
        if keyword and normalize_text(keyword) in normalized:
            hot += 1
    if any(word in normalized for word in ("$100", "$50", "free tickets", "limited", "fifa", "ticketmaster")):
        hot += 1
    if hot >= 2:
        return "high"
    if hot == 1:
        return "watch"
    return "normal"


def parse_datetime_hint(text: str, *, now: Optional[datetime] = None) -> Optional[datetime]:
    reference = now or utc_now()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    local_now = reference.astimezone(EASTERN)
    raw = str(text or "")

    iso = ISOISH_RE.search(raw)
    if iso:
        candidate = iso.group(0)
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=EASTERN)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass

    hh = HHMMSS_RE.search(raw)
    lower = raw.lower()
    if hh and any(token in lower for token in ("countdown", "starts in", "opens in", "available in", "drop in")):
        seconds = int(hh.group("hours")) * 3600 + int(hh.group("minutes")) * 60 + int(hh.group("seconds"))
        return (reference + timedelta(seconds=seconds)).astimezone(timezone.utc)

    relative = _parse_relative_duration(raw)
    if relative is not None and any(token in lower for token in ("countdown", "starts in", "opens in", "available in", "drop in", "in ")):
        return (reference + relative).astimezone(timezone.utc)

    target_date = local_now.date()
    for i, name in enumerate(WEEKDAY_NAMES):
        if re.search(rf"\b{name}\b", lower):
            current = local_now.weekday()
            delta = (i - current) % 7
            target_date = (local_now + timedelta(days=delta)).date()
            break

    if "tomorrow" in lower:
        target_date = (local_now + timedelta(days=1)).date()
    elif "today" in lower:
        target_date = local_now.date()

    if "noon" in lower:
        hour, minute = 12, 0
    elif "midnight" in lower:
        hour, minute = 0, 0
    else:
        tm = TIME_RE.search(raw)
        if not tm:
            return None
        hour = int(tm.group("hour"))
        minute = int(tm.group("minute") or 0)
        ampm = tm.group("ampm").lower()
        if ampm.startswith("p") and hour != 12:
            hour += 12
        if ampm.startswith("a") and hour == 12:
            hour = 0

    local = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        hour,
        minute,
        tzinfo=EASTERN,
    )
    if local < local_now - timedelta(minutes=2) and not any(day in lower for day in WEEKDAY_NAMES) and "today" not in lower:
        local += timedelta(days=1)
    return local.astimezone(timezone.utc)


def _parse_relative_duration(text: str) -> Optional[timedelta]:
    lower = text.lower()
    compact = HHMMSS_RE.search(lower)
    if compact:
        return timedelta(
            hours=int(compact.group("hours")),
            minutes=int(compact.group("minutes")),
            seconds=int(compact.group("seconds")),
        )

    days = hours = minutes = seconds = 0
    matched = False
    for pattern, attr in (
        (r"(\d+)\s*d(?:ays?)?", "days"),
        (r"(\d+)\s*h(?:ours?)?", "hours"),
        (r"(\d+)\s*m(?:in(?:utes?)?)?", "minutes"),
        (r"(\d+)\s*s(?:ec(?:onds?)?)?", "seconds"),
    ):
        m = re.search(pattern, lower)
        if not m:
            continue
        matched = True
        value = int(m.group(1))
        if attr == "days":
            days = value
        elif attr == "hours":
            hours = value
        elif attr == "minutes":
            minutes = value
        else:
            seconds = value
    if not matched:
        return None
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def extract_title(text: str) -> str:
    lines = [line.strip(" •-*#\t") for line in str(text or "").splitlines()]
    candidates: list[str] = []
    skip_markers = (
        "countdown",
        "available at",
        "available in",
        "starts in",
        "expires",
        "open my verizon",
        "my verizon app",
        "source:",
        "status:",
        "priority:",
        "reward:",
        "shine",
        "myaccess",
        "my access",
    )
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        low = clean.lower()
        if len(clean) < 4:
            continue
        if any(low.startswith(marker) for marker in skip_markers):
            continue
        if TIME_RE.fullmatch(clean):
            continue
        candidates.append(clean)
    if candidates:
        for candidate in candidates:
            if any(k in candidate.lower() for k in ("gift card", "ticket", "fifa", "daily drop", "epic wins", "presale", "merch")):
                return candidate[:160]
        return candidates[0][:160]

    fallback = re.sub(r"\s+", " ", str(text or "")).strip()
    return (fallback[:160] or "Verizon Shine reward").strip()


def parse_reward_text(
    text: str,
    *,
    guild_id: int,
    source: str = "manual",
    now: Optional[datetime] = None,
    priority_keywords: Iterable[str] = DEFAULT_PRIORITY_KEYWORDS,
) -> Optional[VerizonReward]:
    raw = str(text or "").strip()
    if not raw:
        return None

    title = extract_title(raw)
    reward_type = classify_type(raw + "\n" + title)
    status = classify_status(raw)
    available_at = parse_datetime_hint(raw, now=now)
    expires_at = _parse_expires_at(raw, now=now)
    priority = priority_for(raw + "\n" + title, priority_keywords)
    fp = fingerprint_hash(title, status, source)
    rid = reward_id_for(guild_id, title, source)

    return VerizonReward(
        reward_id=rid,
        guild_id=int(guild_id),
        title=title,
        type=reward_type,
        status=status,
        source=source,
        available_at=available_at,
        expires_at=expires_at,
        priority=priority,
        raw_text=raw,
        fingerprint_hash=fp,
        metadata={"parser": "verizon_rewards.v1"},
    )


def parse_rewards_from_text(
    text: str,
    *,
    guild_id: int,
    source: str = "manual",
    now: Optional[datetime] = None,
    priority_keywords: Iterable[str] = DEFAULT_PRIORITY_KEYWORDS,
) -> list[VerizonReward]:
    raw = str(text or "").strip()
    if not raw:
        return []

    blocks = [block.strip() for block in re.split(r"\n\s*\n|---+", raw) if block.strip()]
    if len(blocks) <= 1:
        reward = parse_reward_text(raw, guild_id=guild_id, source=source, now=now, priority_keywords=priority_keywords)
        return [reward] if reward else []

    rewards: list[VerizonReward] = []
    seen: set[str] = set()
    for block in blocks:
        reward = parse_reward_text(block, guild_id=guild_id, source=source, now=now, priority_keywords=priority_keywords)
        if reward and reward.reward_id not in seen:
            seen.add(reward.reward_id)
            rewards.append(reward)
    return rewards


def _parse_expires_at(text: str, *, now: Optional[datetime] = None) -> Optional[datetime]:
    lower = str(text or "").lower()
    match = re.search(r"(?:expires|ends|entry closes|available until)(.+)", lower)
    if not match:
        return None
    return parse_datetime_hint(match.group(1), now=now)
