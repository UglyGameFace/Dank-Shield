from __future__ import annotations

"""Opt-in, per-guild Simple Verify nudges backed by canonical role truth.

Only the saved verification mode, mapped roles and channel can authorize a
reminder. Delivery claims are atomic in Discloud PostgreSQL and fail closed on errors.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import discord

from .guild_config import get_guild_config
from .role_truth import clear_cache as clear_role_truth_cache
from .role_truth import member_is_pending_for_config
from .setup_engine.verification_modes import basic_verify_allowed_for_guild
from .verification_reminder_postgres import (
    claim_sync as _pg_claim_sync,
    ready_sync as _pg_ready_sync,
    stale_candidates_sync as _pg_stale_candidates_sync,
    delete_departed_sync as _pg_delete_departed_sync,
    manual_stale_candidates_sync as _pg_manual_stale_candidates_sync,
    delete_manual_departed_sync as _pg_delete_manual_departed_sync,
)

REMINDER_CONFIG_KEY = "unverified_reminders_v1"
SCAN_SIZE = 100
GUILDS_PER_PASS = 25
MAX_DELIVERIES_PER_PASS = 10
MAX_PER_GUILD_PER_PASS = 3
MANUAL_COOLDOWN_SECONDS = 60
POLL_SECONDS = 180
RETENTION_DAYS = 90
CLEANUP_BATCH_SIZE = 12
CLEANUP_INTERVAL_SECONDS = 3600


def _as_int(value: Any, default: int) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ReminderPolicy:
    enabled: bool = False
    delay_hours: int = 24
    repeat_hours: int = 48
    max_reminders: int = 3
    message: str = ""
    delivery: str = "dm"
    reminder_channel_id: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "delay_hours": self.delay_hours,
            "repeat_hours": self.repeat_hours,
            "max_reminders": self.max_reminders,
            "message": self.message,
            "delivery": self.delivery,
            "reminder_channel_id": str(self.reminder_channel_id) if self.reminder_channel_id else "",
        }


def parse_policy(cfg: Mapping[str, Any]) -> ReminderPolicy:
    raw = cfg.get(REMINDER_CONFIG_KEY)
    if not isinstance(raw, Mapping):
        raw = {}
    enabled = raw.get("enabled") is True
    return ReminderPolicy(
        enabled=enabled,
        delay_hours=max(1, min(720, _as_int(raw.get("delay_hours"), 24))),
        repeat_hours=max(12, min(720, _as_int(raw.get("repeat_hours"), 48))),
        max_reminders=max(1, min(5, _as_int(raw.get("max_reminders"), 3))),
        message=str(raw.get("message") or "").strip()[:240],
        delivery="channel" if raw.get("delivery") == "channel" else "dm",
        reminder_channel_id=max(0, _as_int(raw.get("reminder_channel_id"), 0)),
    )


def pending_for_reminder(member: discord.Member, cfg: Mapping[str, Any]) -> bool:
    """Use Dank Shield's role-truth owner; never infer verification by name."""
    if getattr(member, "bot", False):
        return False
    if _as_int(cfg.get("unverified_role_id"), 0) <= 0:
        return False
    if _as_int(cfg.get("verified_role_id"), 0) <= 0:
        return False
    return member_is_pending_for_config(member, cfg)


def verification_channel(guild: discord.Guild, cfg: Mapping[str, Any]) -> discord.TextChannel | None:
    # Deliberately no fuzzy channel-name fallback for unsolicited DMs.
    channel_id = _as_int(cfg.get("verify_channel_id"), 0)
    channel = guild.get_channel(channel_id) if channel_id > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def delivery_channel(guild: discord.Guild, policy: ReminderPolicy) -> discord.TextChannel | None:
    if policy.delivery != "channel":
        return None
    channel = guild.get_channel(policy.reminder_channel_id) if policy.reminder_channel_id > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def member_is_due(member: discord.Member, *, delay_hours: int, now: datetime) -> bool:
    joined = getattr(member, "joined_at", None)
    return isinstance(joined, datetime) and joined.tzinfo is not None and now >= joined + timedelta(hours=delay_hours)


def reminder_text(
    guild: discord.Guild, channel: discord.TextChannel,
    cfg: Mapping[str, Any], policy: ReminderPolicy,
) -> str:
    # Channel link remains valid if staff remove/repost the Verify panel.
    # Never direct a member to an old saved message ID that may be deleted.
    destination = f"https://discord.com/channels/{guild.id}/{channel.id}"
    greeting = policy.message or (
        "Friendly reminder: you haven't finished verifying yet. "
        "To unlock your server access, open the verification channel and tap **Verify**."
    )
    return f"**{guild.name}** verification reminder\n\n{greeting}\n\n✅ Verify here: {destination}\n\nIf you've already verified, you can ignore this message."


async def send_channel_reminder_message(
    guild: discord.Guild, verify: discord.TextChannel,
    notice: discord.TextChannel, member: discord.Member,
    cfg: Mapping[str, Any], policy: ReminderPolicy,
) -> None:
    """Single authoritative Discord channel-post shape for scheduled and manual checks."""
    await notice.send(
        f"{member.mention} {reminder_text(guild, verify, cfg, policy)}",
        allowed_mentions=discord.AllowedMentions(
            users=[member], roles=False, everyone=False,
        ),
    )


async def ledger_ready() -> bool:
    """Check the Discloud PostgreSQL ledger; fail closed if not provisioned."""
    return await asyncio.to_thread(_pg_ready_sync)


async def _claim_delivery(guild_id: int, member: discord.Member, policy: ReminderPolicy) -> bool:
    return await asyncio.to_thread(
        _pg_claim_sync,
        int(guild_id),
        int(member.id),
        getattr(member, "joined_at", None),
        repeat_hours=policy.repeat_hours,
        max_reminders=policy.max_reminders,
    )


class ManualReminderUnavailable(RuntimeError):
    """Explicit reason no manual due scan was allowed; never fallback to DMs."""


class ReminderWorker:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.task: asyncio.Task[None] | None = None
        self.guild_cursor = 0
        self.member_cursors: dict[int, int] = {}
        self.guild_scan_locks: dict[int, asyncio.Lock] = {}
        self.manual_trigger_at: dict[int, float] = {}
        # In-memory keyset cursor only controls scan progress. Actual reminder
        # budgets are durable in PostgreSQL and never reset by cleanup.
        self.cleanup_cursor: tuple[datetime, str, str] | None = None
        self.manual_cleanup_cursor: tuple[datetime, str, str] | None = None
        # Do not add cleanup traffic to Discord's busy startup window.
        self.cleanup_due = time.monotonic() + CLEANUP_INTERVAL_SECONDS

    def start(self) -> asyncio.Task[None]:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="verify-unverified-reminders")
        return self.task

    def _scan_lock(self, guild_id: int) -> asyncio.Lock:
        return self.guild_scan_locks.setdefault(int(guild_id), asyncio.Lock())

    async def scan_guild(self, guild: discord.Guild, *, remaining: int) -> int:
        # Automatic passes and manual "Send Due Now" share the same cursor.
        # Serialize them within a guild to avoid concurrent cursor races.
        async with self._scan_lock(guild.id):
            return await self._scan_guild_unlocked(guild, remaining=remaining)

    async def scan_due_now_in_channel(
        self, guild: discord.Guild, *, channel_id: int,
    ) -> int:
        """Run ONE bounded scheduled-equivalent pass in the saved channel.

        Does not override the first delay, repeat cooldown, maximum attempts,
        authoritative verification truth or the atomic PostgreSQL claim.
        """
        if int(guild.id) <= 0 or int(channel_id) <= 0:
            raise ManualReminderUnavailable("Choose a saved reminder text channel first.")
        if getattr(self.bot, "get_guild", None) is None or self.bot.get_guild(guild.id) is not guild:
            raise ManualReminderUnavailable("This bot process cannot verify that it owns this server.")
        async with self._scan_lock(guild.id):
            now = time.monotonic()
            previous = self.manual_trigger_at.get(guild.id)
            if previous is not None and now - previous < MANUAL_COOLDOWN_SECONDS:
                raise ManualReminderUnavailable(
                    "A due-reminder check was already requested recently. "
                    "Try again after a minute; normal automatic delivery continues."
                )
            try:
                cfg = await get_guild_config(guild.id, refresh=True)
            except Exception as exc:
                raise ManualReminderUnavailable("Cannot load saved verification settings.") from exc
            policy = parse_policy(cfg)
            if (not policy.enabled
                    or str(cfg.get("source") or "").startswith(("unavailable:", "unconfigured:"))):
                raise ManualReminderUnavailable("Enable reminders and restore healthy guild settings first.")
            if policy.delivery != "channel" or policy.reminder_channel_id != int(channel_id):
                raise ManualReminderUnavailable(
                    "Select the designated reminder channel with Use This Channel or Choose Other Channel first."
                )
            if not basic_verify_allowed_for_guild(guild, cfg):
                raise ManualReminderUnavailable("Simple Verify is not configured for this server.")
            verify = verification_channel(guild, cfg)
            notice = delivery_channel(guild, policy)
            if verify is None or notice is None or int(notice.id) != int(channel_id):
                raise ManualReminderUnavailable("The saved verification or reminder channel no longer exists.")
            if (guild.get_role(_as_int(cfg.get("unverified_role_id"), 0)) is None
                    or guild.get_role(_as_int(cfg.get("verified_role_id"), 0)) is None):
                raise ManualReminderUnavailable("Map both verification roles before sending reminders.")
            sender = guild.me
            if sender is None:
                raise ManualReminderUnavailable("Cannot verify Dank Shield's channel permissions.")
            permissions = notice.permissions_for(sender)
            if not (permissions.view_channel and permissions.send_messages):
                raise ManualReminderUnavailable(
                    "Dank Shield needs View Channel and Send Messages in the designated channel."
                )
            if not await ledger_ready():
                raise ManualReminderUnavailable(
                    "The private PostgreSQL reminder ledger is unavailable. Nothing was sent."
                )
            self.manual_trigger_at[guild.id] = now
            # At most 3 attempt claims, at most 100 fetched members.
            # The cursor is retained so later checks can continue fairly.
            return await self._scan_guild_unlocked(
                guild, remaining=MAX_PER_GUILD_PER_PASS,
                required_channel_id=int(channel_id),
            )

    async def _scan_guild_unlocked(
        self, guild: discord.Guild, *, remaining: int,
        required_channel_id: int = 0,
    ) -> int:
        try:
            cfg = await get_guild_config(guild.id, refresh=True)
            policy = parse_policy(cfg)
            if not policy.enabled or str(cfg.get("source") or "").startswith(("unavailable:", "unconfigured:")):
                return 0
            # A manual request must never switch into DM mode just because
            # another moderator changed the policy while the button ran.
            if required_channel_id and (
                policy.delivery != "channel"
                or policy.reminder_channel_id != required_channel_id
            ):
                return 0
            if not basic_verify_allowed_for_guild(guild, cfg):
                return 0
            channel = verification_channel(guild, cfg)
            if channel is None:
                return 0
            if guild.get_role(_as_int(cfg.get("unverified_role_id"), 0)) is None:
                return 0
            if guild.get_role(_as_int(cfg.get("verified_role_id"), 0)) is None:
                return 0
            notice_channel = delivery_channel(guild, policy) if policy.delivery == "channel" else None
            if policy.delivery == "channel":
                if notice_channel is None:
                    return 0
                sender = guild.me
                if sender is None:
                    return 0
                permissions = notice_channel.permissions_for(sender)
                if not permissions.view_channel or not permissions.send_messages:
                    return 0
            # Skip expensive member pagination if the private PostgreSQL ledger
            # cannot be reached. No Supabase fallback may send duplicates.
            if not await ledger_ready():
                return 0
            clear_role_truth_cache(guild.id)
            cursor = self.member_cursors.get(guild.id, 0)
            after = discord.Object(id=cursor) if cursor else None
            # Fresh guild member API page, not an incomplete client cache.
            fetched = [m async for m in guild.fetch_members(limit=SCAN_SIZE, after=after)]
            if not fetched:
                self.member_cursors[guild.id] = 0
                return 0
            claims = 0
            remaining = min(remaining, MAX_PER_GUILD_PER_PASS)
            now = datetime.now(timezone.utc)
            last_processed = cursor
            for member in fetched:
                if claims >= remaining:
                    break
                last_processed = int(member.id)
                if not pending_for_reminder(member, cfg):
                    continue
                if not member_is_due(member, delay_hours=policy.delay_hours, now=now):
                    continue
                if not channel.permissions_for(member).view_channel:
                    continue
                if notice_channel is not None and not notice_channel.permissions_for(member).view_channel:
                    continue
                # Discord can update member roles while we page through a guild.
                # Re-fetch before taking a durable attempt from the member's budget.
                try:
                    current = await guild.fetch_member(int(member.id))
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    continue
                if not pending_for_reminder(current, cfg) or not channel.permissions_for(current).view_channel:
                    continue
                if notice_channel is not None and not notice_channel.permissions_for(current).view_channel:
                    continue
                if not await _claim_delivery(guild.id, current, policy):
                    continue
                claims += 1  # Budget even failures; avoid an unbounded DM storm.
                # A member can finish Simple Verify during the PostgreSQL
                # transaction. Fetch their current roles one last time before
                # delivery rather than sending an already-obsolete reminder.
                try:
                    current = await guild.fetch_member(int(member.id))
                except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                    continue
                if not pending_for_reminder(current, cfg) or not channel.permissions_for(current).view_channel:
                    continue
                if notice_channel is not None and not notice_channel.permissions_for(current).view_channel:
                    continue
                try:
                    if notice_channel is not None:
                        await send_channel_reminder_message(
                            guild, channel, notice_channel, current, cfg, policy,
                        )
                    else:
                        await current.send(
                            reminder_text(guild, channel, cfg, policy),
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"ℹ️ verify reminder delivery unavailable guild={guild.id} kind={type(exc).__name__}")
                await asyncio.sleep(0.5)
            # Never advance past members not yet processed when the delivery
            # budget runs out halfway through a page.
            page_exhausted = last_processed == int(fetched[-1].id)
            self.member_cursors[guild.id] = (
                0 if page_exhausted and len(fetched) < SCAN_SIZE
                else last_processed
            )
            return claims
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ verify reminder scan skipped guild={guild.id} error={type(exc).__name__}")
            return 0

    async def run_pass(self) -> int:
        guilds = sorted(list(getattr(self.bot, "guilds", []) or []), key=lambda g: int(g.id))
        if not guilds:
            return 0
        index = self.guild_cursor % len(guilds)
        count = min(GUILDS_PER_PASS, len(guilds))
        batch = [guilds[(index + i) % len(guilds)] for i in range(count)]
        claimed = 0
        visited = 0
        for guild in batch:
            if claimed >= MAX_DELIVERIES_PER_PASS:
                break
            visited += 1
            claimed += await self.scan_guild(guild, remaining=MAX_DELIVERIES_PER_PASS - claimed)
            await asyncio.sleep(0)
        # Resume at the first guild we could not scan when the global budget
        # was exhausted. Otherwise advance the normal batch window. A complete
        # sweep of a small guild set also rotates its first guild, so one busy
        # server cannot permanently keep less-active servers at the end.
        self.guild_cursor = (index + (1 if visited == len(guilds) else visited)) % len(guilds)
        return claimed

    async def cleanup_stale_history(self) -> int:
        """Delete old delivery counters ONLY after authoritative member absence.

        A still-unverified member may be present for longer than 90 days.
        Their tiny claim record must survive so the per-join maximum cannot
        silently reset. Cache misses/permissions/HTTP errors never authorize
        deletion. The hourly sweep checks at most 12 stale candidates.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        try:
            rows = await asyncio.to_thread(
                _pg_stale_candidates_sync,
                cutoff,
                after=self.cleanup_cursor,
                limit=CLEANUP_BATCH_SIZE,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ verify reminder cleanup unavailable error={type(exc).__name__}")
            return 0

        deleted = 0
        for guild_id, user_id, joined_at, last_claimed_at in rows:
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue  # Never infer departure from guild cache absence.
            try:
                await guild.fetch_member(int(user_id))
            except discord.NotFound as exc:
                if getattr(exc, "code", None) != 10007:
                    continue  # 404 Unknown Guild or another resource is not proof.
                try:
                    if await asyncio.to_thread(
                        _pg_delete_departed_sync,
                        guild_id, user_id, joined_at, cutoff,
                    ):
                        deleted += 1
                except asyncio.CancelledError:
                    raise
                except Exception as delete_exc:
                    print(f"⚠️ verify reminder cleanup delete unavailable error={type(delete_exc).__name__}")
            except (discord.Forbidden, discord.HTTPException):
                continue
            await asyncio.sleep(0)

        if rows and len(rows) == CLEANUP_BATCH_SIZE:
            guild_id, user_id, _, last_claimed_at = rows[-1]
            self.cleanup_cursor = (last_claimed_at, guild_id, user_id)
        else:
            self.cleanup_cursor = None
        return deleted


    async def cleanup_stale_manual_history(self) -> int:
        """Same verified-departure requirement as scheduled attempt retention."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
        try:
            rows = await asyncio.to_thread(
                _pg_manual_stale_candidates_sync,
                cutoff, after=self.manual_cleanup_cursor, limit=CLEANUP_BATCH_SIZE,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Expected before the separate manual schema is enabled. No deletion
            # and no auto-migration when this table does not exist.
            return 0
        deleted = 0
        for guild_id, user_id, joined_at, last_claimed_at in rows:
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue
            try:
                await guild.fetch_member(int(user_id))
            except discord.NotFound as exc:
                if getattr(exc, "code", None) != 10007:
                    continue
                try:
                    removed = await asyncio.to_thread(
                        _pg_delete_manual_departed_sync,
                        guild_id, user_id, joined_at, cutoff,
                    )
                    if removed:
                        deleted += 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
            except (discord.Forbidden, discord.HTTPException):
                continue
            await asyncio.sleep(0)
        if rows and len(rows) == CLEANUP_BATCH_SIZE:
            guild_id, user_id, _, last_claimed_at = rows[-1]
            self.manual_cleanup_cursor = (last_claimed_at, guild_id, user_id)
        else:
            self.manual_cleanup_cursor = None
        return deleted

    async def run(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.run_pass()
                if time.monotonic() >= self.cleanup_due:
                    self.cleanup_due = time.monotonic() + CLEANUP_INTERVAL_SECONDS
                    await self.cleanup_stale_history()
                    await self.cleanup_stale_manual_history()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"⚠️ verify reminder worker pass failed error={type(exc).__name__}")
            await asyncio.sleep(POLL_SECONDS)


_WORKER: ReminderWorker | None = None


def start_unverified_reminder_worker(bot: Any) -> asyncio.Task[None]:
    global _WORKER
    if _WORKER is None or _WORKER.bot is not bot:
        _WORKER = ReminderWorker(bot)
    return _WORKER.start()


async def send_due_now_in_designated_channel(
    bot: Any, guild: discord.Guild, *, channel_id: int,
) -> int:
    """Use the already-running reminder worker, not a parallel sender."""
    worker = _WORKER
    if worker is None or worker.bot is not bot or worker.task is None or worker.task.done():
        raise ManualReminderUnavailable(
            "The reminder worker is not running. Check bot readiness before trying again."
        )
    return await worker.scan_due_now_in_channel(guild, channel_id=channel_id)
