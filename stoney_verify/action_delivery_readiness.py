from __future__ import annotations

"""4D.2 read-only delivery readiness over actual private ledger history.

Not a sender or scheduler. No membership sweep, SQL mutation, claim, Discord
message or automatic enablement occurs here. Existing verification reminder
worker is the only owner of verification DM delivery.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord

from .action_builder_policy import parse_action_builder
from .action_center_postgres import read_member_setup_claim_snapshot_sync
from .action_delivery_preflight import inspect_member_setup_candidate
from .action_schedule_policy import load_schedule, preview_candidate

READ_TIMEOUT_SECONDS = 8.0
_PRECHECK_STATES = frozenset({"candidate_preview", "waiting", "quiet"})


@dataclass(frozen=True)
class ActionDeliveryReadiness:
    status: str
    reason: str
    revision: int = 0
    next_at: datetime | None = None
    attempts: int | None = None
    daily_count: int | None = None
    ledger_checked: bool = False
    delivery_authorized: bool = False

    def __post_init__(self) -> None:
        if self.delivery_authorized is not False:
            raise ValueError("Readiness is never authority to send a DM")


async def inspect_member_setup_readiness(
    member: discord.Member, *, expected_revision: int,
    now: datetime | None = None,
) -> ActionDeliveryReadiness:
    """Recheck consent and owner state around a read-only ledger snapshot.

    The snapshot is not transactional with owner state; therefore it can ONLY
    inform a private preview, never authorize a future worker to send. A real
    sender needs an atomic reservation and final owner/opt-out checks.
    """
    moment = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        return ActionDeliveryReadiness("blocked", "Timezone-aware inspection time required")
    first = await inspect_member_setup_candidate(
        member, expected_revision=expected_revision, now=moment,
    )
    if first.status not in _PRECHECK_STATES:
        return ActionDeliveryReadiness(
            first.status, first.reason, revision=first.revision,
            next_at=first.candidate_at,
        )
    try:
        original_cfg, before_schedule = await load_schedule(member.guild.id, refresh=True)
        original_rule = parse_action_builder(original_cfg).rule_for("member_setup_revision")
        if not original_rule.included:
            return ActionDeliveryReadiness("excluded", "Draft no longer includes setup reminders")
        local_day = moment.astimezone(ZoneInfo(before_schedule.timezone)).date()
        history = await asyncio.wait_for(
            asyncio.to_thread(
                read_member_setup_claim_snapshot_sync,
                int(member.guild.id), int(member.id), int(expected_revision),
                local_day=local_day,
            ),
            timeout=READ_TIMEOUT_SECONDS,
        )
        # This is a second FRESH, canonical profile/Member Setup owner check.
        after = await inspect_member_setup_candidate(
            member, expected_revision=expected_revision, now=moment,
        )
        if after.status not in _PRECHECK_STATES:
            return ActionDeliveryReadiness(
                after.status, after.reason, revision=after.revision, ledger_checked=True,
            )
        latest_cfg, latest_schedule = await load_schedule(member.guild.id, refresh=True)
        latest_rule = parse_action_builder(latest_cfg).rule_for("member_setup_revision")
        if latest_schedule != before_schedule or latest_rule != original_rule:
            return ActionDeliveryReadiness(
                "stale", "Server draft or safety schedule changed during inspection",
                revision=expected_revision, ledger_checked=True,
            )
        if not latest_rule.included:
            return ActionDeliveryReadiness(
                "excluded", "Member Setup rule was disabled during inspection",
                revision=expected_revision, ledger_checked=True,
            )
        result = preview_candidate(
            latest_schedule,
            condition="member_setup_revision", owner_status="pending",
            now=moment, joined_at=member.joined_at,
            condition_started_at=after.condition_started_at,
            last_claimed_at=history.last_claimed_at,
            attempts=history.attempts,
            guild_claims_today=history.guild_claims_today,
            included=True,
        )
        return ActionDeliveryReadiness(
            "preview_due" if result.status == "preview_due" else result.status,
            result.reason if result.status != "preview_due" else
            "Timing and stored counters appear eligible. Delivery is still disabled.",
            revision=expected_revision,
            next_at=result.next_at,
            attempts=history.attempts,
            daily_count=history.guild_claims_today,
            ledger_checked=True,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        return ActionDeliveryReadiness(
            "ledger_unavailable",
            "Private reminder history or authoritative state could not be verified",
            revision=expected_revision,
        )
