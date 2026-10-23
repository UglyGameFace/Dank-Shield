from __future__ import annotations

"""4D.3: one-recipient canary dispatcher, intentionally UNWIRED to runtime.

No loop, slash command, startup hook, panel button or scheduled caller imports
this module. A separate rollout must explicitly configure THREE target-locked
environment values and invoke this one-member API after a reviewed migration.
All flags default OFF. Verification DMs remain exclusively owned by the
existing verification reminder worker.
"""

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord

from .action_builder_policy import parse_action_builder
from .action_center_postgres import reserve_member_setup_sync
from .action_delivery_preflight import inspect_member_setup_candidate
from .action_delivery_readiness import inspect_member_setup_readiness
from .action_schedule_policy import load_schedule

ENABLE_ENV = "DANK_ACTION_CENTER_CANARY_SEND"
GUILD_ENV = "DANK_ACTION_CENTER_CANARY_GUILD_ID"
USER_ENV = "DANK_ACTION_CENTER_CANARY_USER_ID"
REVISION_ENV = "DANK_ACTION_CENTER_CANARY_REVISION"
CLAIM_TIMEOUT_SECONDS = 8.0

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CanaryDeliveryResult:
    status: str
    reason: str = ""
    claimed: bool = False
    sent: bool = False

    def __post_init__(self) -> None:
        if self.sent and not self.claimed:
            raise ValueError("A reminder cannot be sent without a committed claim")


def _canary_enabled(guild_id: int, user_id: int, revision: int) -> bool:
    """All four exact environment gates must match, not a general enable flag."""
    return (
        os.getenv(ENABLE_ENV, "") == "1"
        and os.getenv(GUILD_ENV, "") == str(guild_id)
        and os.getenv(USER_ENV, "") == str(user_id)
        and os.getenv(REVISION_ENV, "") == str(revision)
    )


def _still_in_guild(member: discord.Member) -> bool:
    """No mass fetches or member cache rebuilds; uncertain membership blocks."""
    try:
        guild = member.guild
        current = guild.get_member(int(member.id))
        return (
            current is not None
            and int(current.id) == int(member.id)
            and current is member
            and not bool(getattr(member, "bot", True))
        )
    except Exception:
        return False


async def dispatch_single_member_setup_canary(
    member: discord.Member, *, expected_revision: int,
) -> CanaryDeliveryResult:
    """Bounded manual canary. Not a background/scheduled reminder sender.

    Claim occurs before the Discord DM and is intentionally never refunded.
    If any check, send or response is uncertain, another attempt is NOT made.
    Per-revision canary cap is 1; per-guild/day canary cap is 1, regardless of
    looser draft settings. A fresh owner/consent/policy read follows reservation.
    """
    if (
        not isinstance(member, discord.Member)
        or type(expected_revision) is not int or expected_revision <= 0
    ):
        return CanaryDeliveryResult("blocked", "Invalid candidate")
    guild_id, user_id = int(member.guild.id), int(member.id)
    if not _canary_enabled(guild_id, user_id, expected_revision):
        return CanaryDeliveryResult("disabled", "Exact-target canary is not enabled")
    if not _still_in_guild(member):
        return CanaryDeliveryResult("blocked", "Guild membership not verified")
    before_now = _utcnow()
    try:
        cfg, schedule = await load_schedule(guild_id, refresh=True)
        rule = parse_action_builder(cfg).rule_for("member_setup_revision")
        if not rule.included:
            return CanaryDeliveryResult("excluded", "Server draft excludes this condition")
        readiness = await inspect_member_setup_readiness(
            member, expected_revision=expected_revision, now=before_now,
        )
        if readiness.status != "preview_due" or not readiness.ledger_checked:
            return CanaryDeliveryResult("blocked", "Not due or durable ledger unavailable")
        # Re-read server rules around the potentially slow ledger snapshot.
        cfg_latest, schedule_latest = await load_schedule(guild_id, refresh=True)
        rule_latest = parse_action_builder(cfg_latest).rule_for("member_setup_revision")
        if schedule_latest != schedule or rule_latest != rule or not rule_latest.included:
            return CanaryDeliveryResult("stale", "Server policy changed during eligibility check")
        if _utcnow().astimezone(ZoneInfo(schedule.timezone)).date() != before_now.astimezone(
            ZoneInfo(schedule.timezone)
        ).date():
            return CanaryDeliveryResult("blocked", "Guild-local delivery day changed")
        if not _canary_enabled(guild_id, user_id, expected_revision) or not _still_in_guild(member):
            return CanaryDeliveryResult("disabled", "Canary revoked or member departed")
        reserved = await asyncio.wait_for(
            asyncio.to_thread(
                reserve_member_setup_sync,
                guild_id, user_id, expected_revision,
                local_day=before_now.astimezone(ZoneInfo(schedule.timezone)).date(),
                cooldown_hours=schedule.repeat_hours,
                max_attempts=1,                 # One canary per revision, ever.
                max_guild_per_day=1,             # One canary per guild-local day.
            ), timeout=CLAIM_TIMEOUT_SECONDS,
        )
        if reserved is not True:
            return CanaryDeliveryResult("claim_denied", "Atomic claim rejected or unavailable")
    except asyncio.CancelledError:
        raise
    except Exception:
        return CanaryDeliveryResult("unavailable", "Cannot safely establish claim and eligibility")

    # Reservation committed. Every later failure burns the attempt, preventing
    # replays after crashes and failed Discord sends.
    try:
        if not _canary_enabled(guild_id, user_id, expected_revision) or not _still_in_guild(member):
            return CanaryDeliveryResult("cancelled", "Canary revoked or member departed", claimed=True)
        now = _utcnow()
        cfg_final, schedule_final = await load_schedule(guild_id, refresh=True)
        rule_final = parse_action_builder(cfg_final).rule_for("member_setup_revision")
        if schedule_final != schedule or rule_final != rule or not rule_final.included:
            return CanaryDeliveryResult("cancelled", "Server policy changed after claim", claimed=True)
        # Post-claim preflight explicitly rereads fresh opt-in, exemption,
        # published revision, completion and quiet-hour eligibility. It never
        # requests a SECOND claim and never interprets a stale preview as truth.
        final = await inspect_member_setup_candidate(
            member, expected_revision=expected_revision, now=now,
        )
        if (
            final.status != "candidate_preview"
            or final.revision != expected_revision
            or not _canary_enabled(guild_id, user_id, expected_revision)
            or not _still_in_guild(member)
        ):
            return CanaryDeliveryResult("cancelled", "Owner/consent/quiet-hours check changed", claimed=True)
        # No user-provided text or role mentions. Keep a link-free, minimal DM;
        # actual member setup and preferences remain in the existing /dank UI.
        await member.send(
            "📋 A server's Member Setup was updated and still needs your review. "
            "Open /dank in that server and choose My Member Setup. "
            "You can turn off future Action Center reminder DMs in My Reminder Preferences.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return CanaryDeliveryResult("sent", "One approved canary DM delivered", claimed=True, sent=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        return CanaryDeliveryResult("send_failed", "Delivery not confirmed; claim remains consumed", claimed=True)
