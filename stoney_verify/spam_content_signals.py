from __future__ import annotations

"""Conservative, explainable single-message promotional-spam signals.

These rules consider message behavior rather than names, gender, ethnicity,
or other personal characteristics. They do not assert that an offer is a scam.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class ContentSpamFinding:
    category: str
    signals: tuple[str, ...]


_CONTACT_RE = re.compile(
    r"\b(?:hmu|hit me up|dm(?: me)?|pm(?: me)?|message me|inbox me|contact me)\b"
)
_FLIRTY_BAIT_PATTERNS = (
    ("romantic_brag", re.compile(r"\b(?:hottest|sexiest)\b.{0,90}\bon discord\b")),
    ("rating_lure", re.compile(r"\b10\s*/\s*10\b")),
    ("adult_lure", re.compile(r"\b(?:hot treats|spicy pics|private pics|adult fun)\b")),
    ("scripted_opener", re.compile(r"\bnot here for games\b")),
)
_GIVEAWAY_RE = re.compile(
    r"\b(?:give(?:\s+it)?\s+(?:out|away)|giving\s+(?:out|away)|giveaway|donating|free)\b"
)
_TECH_RE = re.compile(r"\b(?:pc|gaming rig|computer|laptop|console|graphics card|gpu)\b")
_INTEREST_RE = re.compile(
    r"\b(?:dm(?: me)?|pm(?: me)?|message me|inbox me|contact me|reach out|"
    r"if you(?:'re| are) interested)\b"
)
_EVERYONE_RE = re.compile(r"@(?:everyone|here)\b")


def classify_unsolicited_promotion(
    content: str, *, mentions_everyone: bool = False
) -> Optional[ContentSpamFinding]:
    """Detect only multi-signal unsolicited promotion patterns.

    A single suspicious word, mention, or giveaway offer cannot trigger a
    decision. Staff and channel exemptions are handled by SpamGuard itself.
    """
    text = re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", str(content or "")).casefold()
    ).strip()
    if not text:
        return None

    if _CONTACT_RE.search(text) and len(text) >= 80:
        clues = tuple(label for label, regex in _FLIRTY_BAIT_PATTERNS if regex.search(text))
        if len(clues) >= 2 and ("adult_lure" in clues or "romantic_brag" in clues):
            return ContentSpamFinding(
                "unsolicited_personal_promotion", clues + ("contact_request",)
            )

    if (
        (bool(mentions_everyone) or bool(_EVERYONE_RE.search(text)))
        and _GIVEAWAY_RE.search(text)
        and _TECH_RE.search(text)
        and _INTEREST_RE.search(text)
    ):
        return ContentSpamFinding(
            "mass_mention_giveaway_dm",
            ("mass_mention", "device_giveaway", "private_contact_request"),
        )

    return None


def recently_joined_or_created(member: Any, *, now: Optional[datetime] = None) -> bool:
    """Recent membership is a risk modifier, never proof of automation."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    for attr, days in (("joined_at", 2), ("created_at", 7)):
        when = getattr(member, attr, None)
        if not isinstance(when, datetime):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        age = current - when
        if timedelta(0) <= age <= timedelta(days=days):
            return True
    return False


def single_message_enforcement_mode(configured_mode: str, *, recent_member: bool) -> str:
    """One heuristic message must never cause an automatic kick or ban."""
    mode = str(configured_mode or "timeout").strip().lower()
    if mode in {"log_only", "delete_only"}:
        return mode
    if not recent_member:
        return "delete_only"
    return "quarantine" if mode == "quarantine" else "timeout"
