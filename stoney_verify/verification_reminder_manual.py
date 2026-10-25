from __future__ import annotations

"""Manual Simple Verify reminders using the existing worker, never a second scheduler.

No scheduled reminder age/cooldown is consumed. This is intentionally a
page-sized, staff-confirmed sender, not a mass-ping-all-guild-members action.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import discord

from .guild_config import get_guild_config
from .role_truth import clear_cache as clear_role_truth_cache
from .setup_engine.verification_modes import basic_verify_allowed_for_guild
from .verification_reminders import (
    REMINDER_CONFIG_KEY,
    SCAN_SIZE,
    ManualReminderUnavailable,
    ReminderPolicy,
    delivery_channel,
    ledger_ready,
    parse_policy,
    pending_for_reminder,
    reminder_text,
    verification_channel,
)
from .verification_reminder_postgres import (
    ManualClaimUnavailable,
    manual_ready_sync,
    reserve_manual_batch_sync,
)

MAX_MANUAL_RECIPIENTS = 3


@dataclass(frozen=True)
class ManualPreview:
    after: int
    next_after: int
    scanned: int
    pending: int
    without_access: int
    before_automatic_delay: int
    potential: int
    candidates: tuple[int, ...]
    guild_id: int
    channel_id: int
    policy_proof: str
    message: str


@dataclass(frozen=True)
class ManualDelivery:
    selected: int
    reserved: int
    delivered: int
    failed: int
    skipped: int
    blocked_by_manual_limits: int


def _safe_int(value: Any) -> int:
    try:
        return int(str(value or "0"))
    except (TypeError, ValueError):
        return 0


def _worker_for(bot: Any, guild: discord.Guild):
    # The already running singleton is the delivery owner and concurrency lock.
    from . import verification_reminders as reminders

    worker = reminders._WORKER
    if (worker is None or worker.bot is not bot
            or worker.task is None or worker.task.done()
            or bot.get_guild(guild.id) is not guild):
        raise ManualReminderUnavailable(
            "The verification reminder worker is not running for this server."
        )
    return worker


def _policy_proof(
    guild: discord.Guild, cfg: Mapping[str, Any], policy: ReminderPolicy,
    verify: discord.TextChannel, notice: discord.TextChannel,
) -> str:
    fields = (
        str(guild.id), str(notice.id), str(verify.id),
        str(cfg.get("unverified_role_id") or ""),
        str(cfg.get("verified_role_id") or ""),
        reminder_text(guild, verify, cfg, policy),
    )
    return hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()[:24]


async def _context(guild: discord.Guild, channel_id: int):
    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as exc:
        raise ManualReminderUnavailable("Could not read this server's verification settings.") from exc
    if str(cfg.get("source") or "").startswith(("unavailable:", "unconfigured:")):
        raise ManualReminderUnavailable("Saved server settings are unavailable.")
    if not basic_verify_allowed_for_guild(guild, cfg):
        raise ManualReminderUnavailable("Simple Verify must be enabled first.")
    if (guild.get_role(_safe_int(cfg.get("unverified_role_id"))) is None
            or guild.get_role(_safe_int(cfg.get("verified_role_id"))) is None):
        raise ManualReminderUnavailable("Map both verification roles before sending.")
    policy = parse_policy(cfg)
    if (policy.delivery != "channel" or policy.reminder_channel_id != channel_id):
        raise ManualReminderUnavailable(
            "Save the reminder channel with Use This Channel or Choose Other Channel. "
            "Manual sending never switches to DMs or the invoking chat."
        )
    verify = verification_channel(guild, cfg)
    notice = delivery_channel(guild, policy)
    if verify is None or notice is None or notice.id != channel_id:
        raise ManualReminderUnavailable("The saved verification/reminder channel is missing.")
    bot_member = guild.me
    if bot_member is None:
        raise ManualReminderUnavailable("Bot membership is not available.")
    perms = notice.permissions_for(bot_member)
    if not (perms.view_channel and perms.send_messages):
        raise ManualReminderUnavailable(
            "Dank Shield needs View Channel and Send Messages in the reminder channel."
        )
    if not await ledger_ready() or not await asyncio.to_thread(manual_ready_sync):
        raise ManualReminderUnavailable(
            "The manual reminder ledger is not ready. Apply the approved additive "
            "manual PostgreSQL migration; automatic reminders are unchanged."
        )
    clear_role_truth_cache(guild.id)
    return cfg, policy, verify, notice


def _member_visible(member: discord.Member, verify: discord.TextChannel, notice: discord.TextChannel) -> bool:
    return bool(
        verify.permissions_for(member).view_channel
        and notice.permissions_for(member).view_channel
    )


async def preview_manual_reminders(
    bot: Any, guild: discord.Guild, *, channel_id: int, after: int = 0,
) -> ManualPreview:
    _worker_for(bot, guild)
    if after < 0 or channel_id <= 0:
        raise ManualReminderUnavailable("Invalid member page or reminder channel.")
    cfg, policy, verify, notice = await _context(guild, channel_id)
    try:
        members = [
            member async for member in guild.fetch_members(
                limit=SCAN_SIZE, after=discord.Object(id=after) if after else None,
            )
        ]
    except (discord.Forbidden, discord.HTTPException) as exc:
        raise ManualReminderUnavailable(
            "Could not read this page of guild members. Check Server Members Intent and bot access."
        ) from exc
    pending = invisible = early = potential = 0
    candidates: list[int] = []
    now = datetime.now(timezone.utc)
    for member in members:
        if not pending_for_reminder(member, cfg):
            continue
        pending += 1
        if not _member_visible(member, verify, notice):
            invisible += 1
            continue
        if member.joined_at is None or member.joined_at.tzinfo is None:
            continue
        if now < member.joined_at + timedelta(hours=policy.delay_hours):
            early += 1
        potential += 1
        if len(candidates) < MAX_MANUAL_RECIPIENTS:
            candidates.append(int(member.id))
    return ManualPreview(
        after=after,
        next_after=int(members[-1].id) if len(members) == SCAN_SIZE else 0,
        scanned=len(members),
        pending=pending,
        without_access=invisible,
        before_automatic_delay=early,
        potential=potential,
        candidates=tuple(candidates),
        guild_id=int(guild.id),
        channel_id=notice.id,
        policy_proof=_policy_proof(guild, cfg, policy, verify, notice),
        message=reminder_text(guild, verify, cfg, policy),
    )


async def send_previewed_manual_reminders(
    bot: Any, guild: discord.Guild, preview: ManualPreview,
) -> ManualDelivery:
    worker = _worker_for(bot, guild)
    if (preview.guild_id != guild.id or not preview.candidates
            or len(preview.candidates) > MAX_MANUAL_RECIPIENTS
            or len(set(preview.candidates)) != len(preview.candidates)):
        raise ManualReminderUnavailable("The selected preview is empty or invalid.")
    async with worker._scan_lock(guild.id):
        cfg, policy, verify, notice = await _context(guild, preview.channel_id)
        if _policy_proof(guild, cfg, policy, verify, notice) != preview.policy_proof:
            raise ManualReminderUnavailable(
                "Reminder settings changed since the preview. Reopen Send Reminder Now."
            )
        # REST rechecks occur before claims AND after claims. Never trust a stale
        # selected role, guild membership or channel visibility.
        eligible: list[discord.Member] = []
        for user_id in preview.candidates:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                continue
            if (pending_for_reminder(member, cfg)
                    and _member_visible(member, verify, notice)
                    and member.joined_at is not None and member.joined_at.tzinfo is not None):
                eligible.append(member)
        if not eligible:
            return ManualDelivery(len(preview.candidates), 0, 0, 0, len(preview.candidates), 0)
        try:
            reserved = await asyncio.to_thread(
                reserve_manual_batch_sync, int(guild.id),
                [(int(m.id), m.joined_at) for m in eligible],
            )
        except ManualClaimUnavailable as exc:
            raise ManualReminderUnavailable(str(exc)) from exc
        reserved_set = set(reserved)
        # A staff member may reconfigure the guild while PostgreSQL is claiming.
        # Fail closed even after a committed reservation, without retry/refund.
        try:
            fresh_cfg, fresh_policy, fresh_verify, fresh_notice = await _context(
                guild, preview.channel_id,
            )
        except ManualReminderUnavailable:
            return ManualDelivery(
                len(preview.candidates), len(reserved), 0, 0,
                len(preview.candidates) - len(eligible) + len(reserved),
                len(eligible) - len(reserved),
            )
        if _policy_proof(
            guild, fresh_cfg, fresh_policy, fresh_verify, fresh_notice,
        ) != preview.policy_proof:
            return ManualDelivery(
                len(preview.candidates), len(reserved), 0, 0,
                len(preview.candidates) - len(eligible) + len(reserved),
                len(eligible) - len(reserved),
            )
        cfg, policy, verify, notice = fresh_cfg, fresh_policy, fresh_verify, fresh_notice
        delivered = failed = skipped_after_claim = 0
        original_join = {m.id: m.joined_at for m in eligible}
        for member in eligible:
            if member.id not in reserved_set:
                continue
            try:
                current = await guild.fetch_member(member.id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                skipped_after_claim += 1
                continue
            if (current.joined_at != original_join[member.id]
                    or not pending_for_reminder(current, cfg)
                    or not _member_visible(current, verify, notice)):
                skipped_after_claim += 1
                continue
            try:
                # Count only successful Discord sends as delivered.
                await notice.send(
                    f"{current.mention} {reminder_text(guild, verify, cfg, policy)}",
                    allowed_mentions=discord.AllowedMentions(
                        users=[current], roles=False, everyone=False,
                    ),
                )
                delivered += 1
            except Exception as exc:
                # The reservation is already durable; even unexpected Discord
                # send errors cannot authorize a retry of this manual attempt.
                print(f"ℹ️ verify manual delivery unavailable guild={guild.id} kind={type(exc).__name__}")
                failed += 1
            await asyncio.sleep(0.5)
        return ManualDelivery(
            selected=len(preview.candidates), reserved=len(reserved),
            delivered=delivered, failed=failed,
            skipped=len(preview.candidates) - len(eligible) + skipped_after_claim,
            blocked_by_manual_limits=len(eligible) - len(reserved),
        )
