from __future__ import annotations

"""Bounded Member Setup automatic delivery using existing profile + PG owners.

Not a second verification sender. Default OFF for every guild. Uses the same
durable claim, preflight, readiness and schedule services as the manual canary,
without its single-recipient environment gates. Saving the server-owned toggle
authorizes this *Member Setup only* worker; members still individually opt in.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import discord

from .action_builder_policy import parse_action_builder
from .action_center_postgres import reserve_member_setup_sync
from .action_delivery_preflight import inspect_member_setup_candidate
from .action_delivery_readiness import inspect_member_setup_readiness
from .action_member_consent import read_member_setup_opt_in_page
from .action_member_setup_automation import (
    activation_prerequisites, load_automation,
)
from .action_schedule_policy import load_schedule

POLL_SECONDS = 180
STARTUP_DELAY_SECONDS = 120
GUILDS_PER_PASS = 8
MAX_CLAIMS_PER_PASS = 4
MAX_CLAIMS_PER_GUILD = 2
MAX_TARGETED_MEMBER_FETCHES_PER_PASS = 8
OPT_IN_PAGE_SIZE = 5
READ_TIMEOUT_SECONDS = 12
CLAIM_TIMEOUT_SECONDS = 8
DM_TEXT = (
    "📋 Your server's Member Setup was updated and still needs your review. "
    "Open /dank in that server and choose My Member Setup. "
    "You can stop future reminders in My Reminder Preferences."
)


@dataclass(frozen=True)
class AutoDeliveryResult:
    status: str
    claimed: bool = False
    sent: bool = False

    def __post_init__(self) -> None:
        if self.sent and not self.claimed:
            raise ValueError("Sending requires a committed durable claim")


@dataclass
class RuntimeCounters:
    passes: int = 0
    attempted_claims: int = 0
    sent: int = 0
    cancelled_after_claim: int = 0
    send_failed: int = 0
    unavailable: int = 0
    last_pass_at: str = ""
    last_error_type: str = ""

    def summary(self) -> str:
        return (
            f"Passes **{self.passes}** · Claimed **{self.attempted_claims}** · "
            f"Sent **{self.sent}** · Cancelled **{self.cancelled_after_claim}** · "
            f"Failed **{self.send_failed}** · Unavailable **{self.unavailable}**"
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _member_valid(guild: discord.Guild, member: object, uid: int) -> bool:
    """Only a known cached or freshly fetched member of exactly this guild."""
    return (
        isinstance(member, discord.Member)
        and member.guild is guild and int(member.id) == uid
        and not member.bot
    )


def _cached_member(guild: discord.Guild, uid: int) -> discord.Member | None:
    member = guild.get_member(uid)
    return member if _member_valid(guild, member, uid) else None


async def _eligible(guild_id: int, member: discord.Member, revision: int):
    cfg, automation = await asyncio.wait_for(load_automation(guild_id), READ_TIMEOUT_SECONDS)
    if not automation.enabled or activation_prerequisites(cfg):
        return None
    schedule_cfg, schedule = await asyncio.wait_for(
        load_schedule(guild_id, refresh=True), READ_TIMEOUT_SECONDS,
    )
    from .action_member_setup_automation import verified_config
    if not verified_config(schedule_cfg):
        return None
    if parse_action_builder(schedule_cfg).rule_for("member_setup_revision").included is not True:
        return None
    readiness = await asyncio.wait_for(
        inspect_member_setup_readiness(member, expected_revision=revision),
        READ_TIMEOUT_SECONDS,
    )
    if (
        readiness.status != "preview_due"
        or not readiness.ledger_checked
        or readiness.delivery_authorized
    ):
        return None
    return schedule, parse_action_builder(schedule_cfg).rule_for("member_setup_revision")


async def dispatch_member_setup_automatic(
    member: discord.Member, *, expected_revision: int,
    fetched_from_discord: bool = False,
) -> AutoDeliveryResult:
    """Revalidate fresh owner and consent before/after PostgreSQL reservation.

    An uncertain or failed claim never authorizes a DM. Post-claim failure
    burns the attempt, preventing replays after process crashes/restarts.
    """
    if not isinstance(member, discord.Member) or type(expected_revision) is not int or expected_revision <= 0:
        return AutoDeliveryResult("invalid")
    guild = member.guild
    gid, uid = int(guild.id), int(member.id)
    if not _member_valid(guild, member, uid):
        return AutoDeliveryResult("not_current_member")
    if not fetched_from_discord and _cached_member(guild, uid) is not member:
        return AutoDeliveryResult("not_cached")
    began = _utcnow()
    try:
        found = await _eligible(gid, member, expected_revision)
        if found is None:
            return AutoDeliveryResult("not_due")
        schedule, rule = found
        local_day = began.astimezone(ZoneInfo(schedule.timezone)).date()
        rechecked = await _eligible(gid, member, expected_revision)
        if rechecked != (schedule, rule):
            return AutoDeliveryResult("stale")
        if _utcnow().astimezone(ZoneInfo(schedule.timezone)).date() != local_day:
            return AutoDeliveryResult("day_changed")
        # Existing PG transaction serializes guild/day budget across shards.
        claimed = await asyncio.wait_for(
            asyncio.to_thread(
                reserve_member_setup_sync, gid, uid, expected_revision,
                local_day=local_day, cooldown_hours=schedule.repeat_hours,
                max_attempts=schedule.max_attempts,
                max_guild_per_day=schedule.max_guild_per_day,
            ),
            timeout=CLAIM_TIMEOUT_SECONDS,
        )
        if claimed is not True:
            return AutoDeliveryResult("claim_denied")
    except asyncio.CancelledError:
        raise
    except Exception:
        return AutoDeliveryResult("unavailable")

    try:
        target = member
        if fetched_from_discord:
            # A cache miss must NEVER become proof of current membership.
            # A second bounded REST get after claim verifies still in guild.
            target = await asyncio.wait_for(guild.fetch_member(uid), READ_TIMEOUT_SECONDS)
            if not _member_valid(guild, target, uid):
                return AutoDeliveryResult("cancelled", claimed=True)
        elif _cached_member(guild, uid) is not member:
            return AutoDeliveryResult("cancelled", claimed=True)
        cfg, active = await asyncio.wait_for(load_automation(gid), READ_TIMEOUT_SECONDS)
        cfg_latest, schedule_latest = await asyncio.wait_for(
            load_schedule(gid, refresh=True), READ_TIMEOUT_SECONDS,
        )
        rule_latest = parse_action_builder(cfg_latest).rule_for("member_setup_revision")
        if (
            not active.enabled or activation_prerequisites(cfg)
            or schedule_latest != schedule or rule_latest != rule
            or _utcnow().astimezone(ZoneInfo(schedule.timezone)).date() != local_day
        ):
            return AutoDeliveryResult("cancelled", claimed=True)
        # Source-of-truth refresh after claim verifies present revision, consent,
        # completion, exemptions, quiet hours. Claim remains consumed on denial.
        final = await asyncio.wait_for(
            inspect_member_setup_candidate(target, expected_revision=expected_revision),
            READ_TIMEOUT_SECONDS,
        )
        if (
            final.status != "candidate_preview"
            or final.revision != expected_revision
            or not _member_valid(guild, target, uid)
            or (not fetched_from_discord and _cached_member(guild, uid) is not member)
        ):
            return AutoDeliveryResult("cancelled", claimed=True)
        # Minimal link-free, mention-free Discord DM. Only this path sends.
        await asyncio.wait_for(
            target.send(DM_TEXT, allowed_mentions=discord.AllowedMentions.none()),
            READ_TIMEOUT_SECONDS,
        )
        return AutoDeliveryResult("sent", claimed=True, sent=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        return AutoDeliveryResult("send_failed", claimed=True)


class MemberSetupReminderWorker:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.task: asyncio.Task[None] | None = None
        self.guild_cursor = 0
        self.member_cursors: dict[int, int] = {}
        self.counters = RuntimeCounters()
        self.guild_counters: dict[int, RuntimeCounters] = {}
        self.remaining_targeted_fetches = MAX_TARGETED_MEMBER_FETCHES_PER_PASS

    def start(self) -> asyncio.Task[None]:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(
                self.run(), name="action-member-setup-automatic-reminders",
            )
        return self.task

    async def scan_guild(self, guild: discord.Guild, remaining: int) -> int:
        if remaining <= 0:
            return 0
        gid = int(guild.id)
        try:
            cfg, automation = await asyncio.wait_for(load_automation(gid), READ_TIMEOUT_SECONDS)
            if not automation.enabled or activation_prerequisites(cfg):
                self.member_cursors.pop(gid, None)
                return 0
            # Fast fail before any candidate IDs or network activity.
            from .member_setup_service import GUILD_SETUP_KEY, normalize_guild_setup_state
            state = normalize_guild_setup_state(cfg.get(GUILD_SETUP_KEY))
            revision = int(state["current_revision"])
            cursor = self.member_cursors.get(gid, 0)
            page = await asyncio.wait_for(
                read_member_setup_opt_in_page(
                    gid, after_user_id=cursor, limit=OPT_IN_PAGE_SIZE,
                ),
                READ_TIMEOUT_SECONDS,
            )
            if not page.user_ids:
                self.member_cursors[gid] = 0
                return 0
            budget = min(remaining, MAX_CLAIMS_PER_GUILD)
            claims = 0
            last_seen = cursor
            guild_stats = self.guild_counters.setdefault(gid, RuntimeCounters())
            for uid in page.user_ids:
                if claims >= budget:
                    break
                previous_seen = last_seen
                last_seen = uid
                member = _cached_member(guild, uid)
                fetched = False
                if member is None:
                    # Only REST fetch an ID from an explicit opt-in page,
                    # never a guild-wide member list. Reserve 2 of the 8
                    # per-pass calls so a post-claim recheck is guaranteed.
                    if self.remaining_targeted_fetches < 2:
                        last_seen = previous_seen  # Retry unprocessed ID next pass.
                        break
                    self.remaining_targeted_fetches -= 2
                    try:
                        member = await asyncio.wait_for(guild.fetch_member(uid), READ_TIMEOUT_SECONDS)
                    except (discord.HTTPException, asyncio.TimeoutError):
                        continue
                    if not _member_valid(guild, member, uid):
                        continue
                    fetched = True
                result = await dispatch_member_setup_automatic(
                    member, expected_revision=revision, fetched_from_discord=fetched,
                )
                if result.claimed:
                    claims += 1
                    self.counters.attempted_claims += 1
                    guild_stats.attempted_claims += 1
                    if result.sent:
                        self.counters.sent += 1
                        guild_stats.sent += 1
                    elif result.status == "send_failed":
                        self.counters.send_failed += 1
                        guild_stats.send_failed += 1
                    else:
                        self.counters.cancelled_after_claim += 1
                        guild_stats.cancelled_after_claim += 1
                elif result.status == "unavailable":
                    self.counters.unavailable += 1
                    guild_stats.unavailable += 1
                    last_seen = previous_seen  # Failed read is never a completed scan.
                    # Back-end uncertainty: abort pass rather than amplify load.
                    break
                await asyncio.sleep(0)
            # Resume after the last *inspected* ID; do not skip the unprocessed
            # tail when the per-guild delivery budget was reached.
            self.member_cursors[gid] = (
                0 if last_seen == page.user_ids[-1] and len(page.user_ids) < OPT_IN_PAGE_SIZE
                else last_seen
            )
            guild_stats.passes += 1
            guild_stats.last_pass_at = _utcnow().isoformat()
            return claims
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.counters.unavailable += 1
            self.counters.last_error_type = type(exc).__name__
            print(f"⚠️ Action Center reminder guild scan unavailable guild={gid} type={type(exc).__name__}")
            return 0

    async def run_pass(self) -> int:
        guilds = sorted(list(getattr(self.bot, "guilds", []) or []), key=lambda g: int(g.id))
        if not guilds:
            self.counters.passes += 1
            return 0
        self.remaining_targeted_fetches = MAX_TARGETED_MEMBER_FETCHES_PER_PASS
        start = self.guild_cursor % len(guilds)
        total = min(GUILDS_PER_PASS, len(guilds))
        count = 0
        visited = 0
        for idx in range(total):
            if count >= MAX_CLAIMS_PER_PASS:
                break
            guild = guilds[(start + idx) % len(guilds)]
            visited += 1
            count += await self.scan_guild(guild, MAX_CLAIMS_PER_PASS - count)
            await asyncio.sleep(0)
        self.guild_cursor = (start + max(visited, 1)) % len(guilds)
        self.counters.passes += 1
        self.counters.last_pass_at = _utcnow().isoformat()
        return count

    async def run(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
        while not self.bot.is_closed():
            try:
                await self.run_pass()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.counters.last_error_type = type(exc).__name__
                print(f"⚠️ Action Center reminder pass error={type(exc).__name__}")
            await asyncio.sleep(POLL_SECONDS)


_WORKER: MemberSetupReminderWorker | None = None


def start_member_setup_automatic_worker(bot: Any) -> asyncio.Task[None]:
    global _WORKER
    if _WORKER is None or _WORKER.bot is not bot:
        _WORKER = MemberSetupReminderWorker(bot)
    return _WORKER.start()


def get_member_setup_automation_runtime(guild_id: int) -> RuntimeCounters | None:
    """Only return this guild's non-durable aggregate operational counters."""
    if type(guild_id) is not int or guild_id <= 0 or _WORKER is None:
        return None
    return _WORKER.guild_counters.get(guild_id)
