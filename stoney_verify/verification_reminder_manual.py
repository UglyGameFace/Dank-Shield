from __future__ import annotations

"""Manual Simple Verify reminders using the existing worker, never a second scheduler.

No scheduled reminder age/cooldown is consumed. This is intentionally a
page-sized, staff-confirmed sender, not a mass-ping-all-guild-members action.
"""

import asyncio
import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import discord

from .guild_config import get_guild_config
from .role_truth import clear_cache as clear_role_truth_cache, member_has_role_id
from .members_new.membership_authority import collect_membership_snapshot
from .setup_engine.verification_modes import basic_verify_allowed_for_guild
from .verification_reminders import (
    ManualReminderUnavailable,
    ReminderPolicy,
    delivery_channel,
    ledger_ready,
    parse_policy,
    pending_for_reminder,
    reminder_text,
    send_channel_reminder_message,
    verification_channel,
)
from .verification_reminder_postgres import (
    ManualClaimUnavailable,
    manual_ready_sync,
    available_manual_member_ids_sync,
    reserve_manual_batch_sync,
)

MAX_MANUAL_RECIPIENTS = 25


@dataclass(frozen=True)
class ManualPreview:
    after: int
    next_after: int
    scanned: int
    pending: int
    without_access: int
    before_automatic_delay: int
    manual_limited: int
    potential: int
    candidates: tuple[int, ...]
    guild_id: int
    channel_id: int
    policy_proof: str
    message: str
    delivery: str = "channel"
    mapped_pending: int = 0
    already_verified_or_staff: int = 0
    eligible_ids: tuple[int, ...] = ()
    eligible_names: tuple[str, ...] = ()
    role_id: int = 0


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
    verify: discord.TextChannel, notice: discord.TextChannel | None,
) -> str:
    # The saved guild role mapping includes secondary access and staff roles;
    # config changes must invalidate old previews before any reserved send.
    role_keys = (
        "unverified_role_id", "verified_role_id", "staff_role_id",
        "vc_staff_role_id", "resident_role_id", "member_role_id",
        "stoner_role_id", "drunken_role_id",
    )
    fields = (
        str(guild.id), str(notice.id if notice else 0), str(verify.id),
        policy.delivery, *(str(cfg.get(key) or "") for key in role_keys),
        reminder_text(guild, verify, cfg, policy),
    )
    return hashlib.sha256("\\0".join(fields).encode("utf-8")).hexdigest()[:24]

async def _context(guild: discord.Guild, channel_id: int, delivery: str):
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
        raise ManualReminderUnavailable(
            "Map the server's Unverified and Verified roles in Verification Center first."
        )
    policy = parse_policy(cfg)
    if delivery not in ("channel", "dm") or policy.delivery != delivery:
        raise ManualReminderUnavailable("Saved delivery changed. Reopen Send Reminder Now.")
    verify = verification_channel(guild, cfg)
    if verify is None:
        raise ManualReminderUnavailable("The saved verification channel is missing.")
    notice = delivery_channel(guild, policy) if delivery == "channel" else None
    if delivery == "channel":
        if notice is None or notice.id != channel_id:
            raise ManualReminderUnavailable(
                "Save a reminder channel using Use This Channel or Choose Other Channel."
            )
        bot_member = guild.me
        if bot_member is None:
            raise ManualReminderUnavailable("Bot membership is not available.")
        perms = notice.permissions_for(bot_member)
        if not (perms.view_channel and perms.send_messages):
            raise ManualReminderUnavailable(
                "Dank Shield needs View Channel and Send Messages in the reminder channel."
            )
    elif channel_id != 0:
        raise ManualReminderUnavailable("Direct messages cannot target a public channel.")
    if not await ledger_ready() or not await asyncio.to_thread(manual_ready_sync):
        raise ManualReminderUnavailable(
            "The private reminder database is not ready. Manual sends remain disabled."
        )
    clear_role_truth_cache(guild.id)
    return cfg, policy, verify, notice


def _member_visible(
    member: discord.Member, verify: discord.TextChannel,
    notice: discord.TextChannel | None,
) -> bool:
    return bool(
        verify.permissions_for(member).view_channel
        and (notice is None or notice.permissions_for(member).view_channel)
    )


def select_manual_group(preview: ManualPreview) -> ManualPreview:
    """Next eligible group from the SAME authoritative snapshot, no new REST sweep."""
    if not preview.next_after or preview.next_after >= len(preview.eligible_ids):
        raise ManualReminderUnavailable("No more eligible members in this scan.")
    start = preview.next_after
    end = min(start + MAX_MANUAL_RECIPIENTS, len(preview.eligible_ids))
    return replace(
        preview, after=start,
        next_after=end if end < len(preview.eligible_ids) else 0,
        candidates=preview.eligible_ids[start:end],
    )

async def preview_manual_reminders(
    bot: Any, guild: discord.Guild, *, channel_id: int, after: int = 0,
) -> ManualPreview:
    _worker_for(bot, guild)
    if after != 0 or channel_id < 0:
        raise ManualReminderUnavailable("Open a fresh member preview.")
    delivery = "dm" if channel_id == 0 else "channel"
    cfg, policy, verify, notice = await _context(guild, channel_id, delivery)
    # The pre-existing shared enumerator budgets REST requests and marks failed
    # cache-only scans nonauthoritative. Never call 100 guild members "all".
    snapshot = await collect_membership_snapshot(guild)
    if not snapshot.authoritative:
        raise ManualReminderUnavailable(
            "Could not finish the live server-member scan. No reminders authorized. "
            "Check Server Members Intent and Discord connectivity."
        )
    pending = mapped = conflicts = invisible = early = 0
    pool: list[tuple[int, datetime]] = []
    names: dict[int, str] = {}
    now = datetime.now(timezone.utc)
    unverified_role_id = _safe_int(cfg.get("unverified_role_id"))
    for member in snapshot.members:
        if getattr(member, "bot", False):
            continue
        mapped_role = member_has_role_id(member, unverified_role_id)
        if mapped_role:
            mapped += 1
        if not pending_for_reminder(member, cfg):
            if mapped_role:
                conflicts += 1
            continue
        pending += 1
        if not _member_visible(member, verify, notice):
            invisible += 1
            continue
        if member.joined_at is None or member.joined_at.tzinfo is None:
            continue
        if now < member.joined_at + timedelta(hours=policy.delay_hours):
            early += 1
        uid = int(member.id)
        pool.append((uid, member.joined_at))
        names[uid] = str(getattr(member, "display_name", "") or uid)[:85]
    available_ids: set[int] = set()
    try:
        for offset in range(0, len(pool), 100):
            available_ids.update(
                await asyncio.to_thread(
                    available_manual_member_ids_sync, guild.id, pool[offset:offset + 100],
                )
            )
    except ManualClaimUnavailable as exc:
        raise ManualReminderUnavailable(str(exc)) from exc
    eligible_ids = tuple(uid for uid, _ in pool if uid in available_ids)
    return ManualPreview(
        after=0,
        next_after=MAX_MANUAL_RECIPIENTS if len(eligible_ids) > MAX_MANUAL_RECIPIENTS else 0,
        scanned=len(snapshot.members),
        pending=pending,
        without_access=invisible,
        before_automatic_delay=early,
        manual_limited=len(pool) - len(available_ids),
        potential=len(eligible_ids),
        candidates=eligible_ids[:MAX_MANUAL_RECIPIENTS],
        guild_id=guild.id,
        channel_id=notice.id if notice is not None else 0,
        policy_proof=_policy_proof(guild, cfg, policy, verify, notice),
        message=reminder_text(guild, verify, cfg, policy),
        delivery=delivery,
        mapped_pending=mapped,
        already_verified_or_staff=conflicts,
        eligible_ids=eligible_ids,
        eligible_names=tuple(names[uid] for uid in eligible_ids),
        role_id=unverified_role_id,
    )

async def send_previewed_manual_reminders(
    bot: Any, guild: discord.Guild, preview: ManualPreview,
) -> ManualDelivery:
    worker = _worker_for(bot, guild)
    if (preview.guild_id != guild.id or not preview.candidates
            or len(preview.candidates) > MAX_MANUAL_RECIPIENTS
            or len(set(preview.candidates)) != len(preview.candidates)
            or not set(preview.candidates).issubset(preview.eligible_ids)):
        raise ManualReminderUnavailable("The selected preview is empty or invalid.")
    async with worker._scan_lock(guild.id):
        cfg, policy, verify, notice = await _context(
            guild, preview.channel_id, preview.delivery,
        )
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
                guild, preview.channel_id, preview.delivery,
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
        current_members: list[discord.Member] = []
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
            current_members.append(current)
        if preview.delivery == "channel" and notice is not None and current_members:
            try:
                # One reminder with up to 25 mentions, not 25 duplicate channel posts.
                await send_channel_reminder_message(
                    guild, verify, notice, current_members, cfg, policy,
                )
                delivered = len(current_members)
            except Exception as exc:
                print(
                    f"ℹ️ verify manual channel delivery unavailable "
                    f"guild={guild.id} kind={type(exc).__name__}"
                )
                failed = len(current_members)
        elif preview.delivery == "dm":
            for current in current_members:
                try:
                    await current.send(
                        reminder_text(guild, verify, cfg, policy),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    delivered += 1
                except Exception as exc:
                    print(
                        f"ℹ️ verify manual DM unavailable "
                        f"guild={guild.id} kind={type(exc).__name__}"
                    )
                    failed += 1
                await asyncio.sleep(0.8)
        return ManualDelivery(
            selected=len(preview.candidates), reserved=len(reserved),
            delivered=delivered, failed=failed,
            skipped=len(preview.candidates) - len(eligible) + skipped_after_claim,
            blocked_by_manual_limits=len(eligible) - len(reserved),
        )
