from __future__ import annotations

"""4D safety preflight: fresh owner truth + member consent, no side effects.

This module is a read-only candidate audit. It has no send, worker, claim, or
DB migration entrypoint. Future delivery must separately verify ledger state,
re-read completion immediately before/after an atomic reservation, and obtain
explicit production enablement. Verification reminders never route here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

from .action_builder_policy import parse_action_builder
from .action_member_consent import read_member_consent
from .action_schedule_policy import load_schedule, preview_candidate


@dataclass(frozen=True)
class DeliveryPreflight:
    status: str
    reason: str
    revision: int = 0
    candidate_at: datetime | None = None
    condition_started_at: datetime | None = None
    delivery_authorized: bool = False

    def __post_init__(self) -> None:
        if self.delivery_authorized is not False:
            raise ValueError("No automatic Action Center sender is authorized by 4D")


def _revision_date(guild_state: Any, revision: int) -> datetime | None:
    from .member_setup_service import latest_revision
    entry = latest_revision(guild_state)
    if not entry or type(entry.get("revision")) is not int or entry["revision"] != revision:
        return None
    value = entry.get("published_at")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


async def inspect_member_setup_candidate(
    member: discord.Member, *, expected_revision: int, now: datetime | None = None
) -> DeliveryPreflight:
    """Read only this member; fail closed on unknown, stale, or disabled truth."""
    if not isinstance(member, discord.Member) or member.bot:
        return DeliveryPreflight("blocked", "Only current non-bot guild members are eligible")
    if type(expected_revision) is not int or expected_revision <= 0:
        return DeliveryPreflight("blocked", "Invalid requested revision")
    guild = member.guild
    try:
        # Preserve canonical exemption and Member Setup revision/grandfather
        # logic, rather than reconstructing separate completion authority.
        from .commands_ext.public_member_setup import _member_exempt, _member_payload
        if _member_exempt(member):
            return DeliveryPreflight("exempt", "Staff/owner/bot exemption")
        cfg, schedule = await load_schedule(guild.id, refresh=True)
        draft = parse_action_builder(cfg)
        rule = draft.rule_for("member_setup_revision")
        consent = await read_member_consent(guild.id, member.id)
        if not consent.member_setup_dm:
            return DeliveryPreflight("opted_out", "Member Setup DMs not explicitly enabled")
        if not rule.included:
            return DeliveryPreflight("excluded", "Member Setup action not included in the server draft")
        guild_state, _effective, status = await _member_payload(member)
        revision = status.get("current_revision")
        if type(revision) is not int or revision != expected_revision:
            return DeliveryPreflight("stale", "Revision changed since candidate selection")
        if not status.get("enabled") or status.get("is_current") or not status.get("pending_sections"):
            return DeliveryPreflight("complete", "Canonical Member Setup owner has no pending requirement")
        started_at = _revision_date(guild_state, revision)
        decision = preview_candidate(
            schedule,
            condition="member_setup_revision",
            owner_status="pending",
            now=now or datetime.now(timezone.utc),
            joined_at=member.joined_at,
            condition_started_at=started_at,
            included=rule.included,
        )
        # Dry-run assumes no prior claims, so it CANNOT authorize a real send.
        return DeliveryPreflight(
            status="candidate_preview" if decision.status == "preview_due" else decision.status,
            reason="Owner checked; no real delivery or ledger authorization" if decision.status == "preview_due" else decision.reason,
            revision=revision,
            candidate_at=decision.next_at,
            condition_started_at=started_at,
        )
    except Exception:
        return DeliveryPreflight("unavailable", "Authoritative state unavailable; no delivery authorized")
