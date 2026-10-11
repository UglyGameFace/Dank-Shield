from __future__ import annotations

"""Opt-in, per-guild Simple Verify nudges backed by canonical role truth.

Only the saved verification mode, mapped roles and channel can authorize a
reminder. Delivery claims are atomic in Supabase and fail closed on errors.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import discord

from .guild_config import get_guild_config
from .role_truth import clear_cache as clear_role_truth_cache
from .role_truth import member_is_pending_verification
from .setup_engine.verification_modes import basic_verify_allowed_for_guild

REMINDER_CONFIG_KEY = "unverified_reminders_v1"
DELIVERY_TABLE = "dank_verify_reminder_delivery"
CLAIM_RPC = "claim_dank_verify_reminder"
SCAN_SIZE = 100
GUILDS_PER_PASS = 25
MAX_DELIVERIES_PER_PASS = 10
MAX_PER_GUILD_PER_PASS = 3
POLL_SECONDS = 180


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
    return member_is_pending_verification(member)


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


async def _ledger_ready() -> bool:
    def check() -> bool:
        from .globals import get_supabase
        sb = get_supabase()
        if sb is None:
            return False
        sb.table(DELIVERY_TABLE).select("guild_id").limit(1).execute()
        probe = sb.rpc(CLAIM_RPC, {
            "p_guild_id": "invalid",
            "p_user_id": "invalid",
            "p_joined_at": datetime.now(timezone.utc).isoformat(),
            "p_repeat_hours": 48,
            "p_max_reminders": 3,
        }).execute()
        return getattr(probe, "data", None) is False

    try:
        return await asyncio.to_thread(check)
    except Exception as exc:
        print(f"⚠️ verify reminders ledger unavailable error={type(exc).__name__}")
        return False


async def ledger_ready() -> bool:
    """Used by the admin UI: never enable an undeliverable feature."""
    return await _ledger_ready()


async def _claim_delivery(guild_id: int, member: discord.Member, policy: ReminderPolicy) -> bool:
    joined = member.joined_at
    if joined is None:
        return False

    def claim() -> bool:
        from .globals import get_supabase
        sb = get_supabase()
        if sb is None:
            return False
        response = sb.rpc(CLAIM_RPC, {
            "p_guild_id": str(guild_id),
            "p_user_id": str(member.id),
            "p_joined_at": joined.astimezone(timezone.utc).isoformat(),
            "p_repeat_hours": policy.repeat_hours,
            "p_max_reminders": policy.max_reminders,
        }).execute()
        # A missing/invalid RPC result is NOT permission to send.
        return getattr(response, "data", None) is True

    try:
        return await asyncio.to_thread(claim)
    except Exception as exc:
        print(f"⚠️ verify reminder atomic claim failed guild={guild_id} error={type(exc).__name__}")
        return False


class ReminderWorker:
    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self.task: asyncio.Task[None] | None = None
        self.guild_cursor = 0
        self.member_cursors: dict[int, int] = {}

    def start(self) -> asyncio.Task[None]:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="verify-unverified-reminders")
        return self.task

    async def scan_guild(self, guild: discord.Guild, *, remaining: int) -> int:
        try:
            cfg = await get_guild_config(guild.id, refresh=True)
            policy = parse_policy(cfg)
            if not policy.enabled or str(cfg.get("source") or "").startswith(("unavailable:", "unconfigured:")):
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
                try:
                    body = reminder_text(guild, channel, cfg, policy)
                    if notice_channel is not None:
                        await notice_channel.send(
                            f"{current.mention} {body}",
                            allowed_mentions=discord.AllowedMentions(users=[current], roles=False, everyone=False),
                        )
                    else:
                        await current.send(body, allowed_mentions=discord.AllowedMentions.none())
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
        self.guild_cursor = (index + count) % len(guilds)
        claimed = 0
        for guild in batch:
            if claimed >= MAX_DELIVERIES_PER_PASS:
                break
            claimed += await self.scan_guild(guild, remaining=MAX_DELIVERIES_PER_PASS - claimed)
            await asyncio.sleep(0)
        return claimed

    async def run(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.run_pass()
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
