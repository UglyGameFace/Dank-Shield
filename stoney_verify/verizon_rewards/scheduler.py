from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from . import repository
from .models import VerizonReward, VerizonRewardConfig, parse_dt, utc_now

_TASKS: dict[tuple[int, str, int], asyncio.Task] = {}


def _log(message: str) -> None:
    try:
        print(f"⏰ verizon_rewards.scheduler {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verizon_rewards.scheduler {message}")
    except Exception:
        pass


async def schedule_reward_reminders(bot: Any, reward: VerizonReward, config: VerizonRewardConfig) -> int:
    if not config.reminders_enabled or not reward.available_at:
        return 0

    available = parse_dt(reward.available_at)
    if available is None:
        return 0

    scheduled = 0
    for offset, remind_at in plan_reminder_times(reward, config):
        key = (int(reward.guild_id), str(reward.reward_id), int(offset))
        old = _TASKS.get(key)
        if old and not old.done():
            old.cancel()
        await repository.save_reminder(reward.guild_id, reward.reward_id, int(offset), remind_at)
        _TASKS[key] = asyncio.create_task(_reminder_task(bot, reward, config, int(offset), remind_at))
        scheduled += 1
    return scheduled


def plan_reminder_times(reward: VerizonReward, config: VerizonRewardConfig, *, now=None) -> list[tuple[int, Any]]:
    available = parse_dt(reward.available_at)
    if available is None or not config.reminders_enabled:
        return []
    reference = parse_dt(now) or utc_now()
    planned: list[tuple[int, Any]] = []
    seen: set[int] = set()
    for offset in config.reminder_offsets_minutes:
        offset_int = int(offset)
        if offset_int in seen:
            continue
        seen.add(offset_int)
        remind_at = available - timedelta(minutes=offset_int)
        if remind_at <= reference - timedelta(seconds=30):
            continue
        planned.append((offset_int, remind_at))
    return planned


async def _reminder_task(
    bot: Any,
    reward: VerizonReward,
    config: VerizonRewardConfig,
    offset_minutes: int,
    remind_at,
) -> None:
    try:
        delay = max(0.0, (parse_dt(remind_at) - utc_now()).total_seconds())
        if delay > 0:
            await asyncio.sleep(delay)
        await send_reminder(bot, reward, config, offset_minutes)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _warn(f"task failed guild={reward.guild_id} reward={reward.reward_id} offset={offset_minutes}: {type(e).__name__}")


async def send_reminder(bot: Any, reward: VerizonReward, config: VerizonRewardConfig, offset_minutes: int) -> bool:
    try:
        from .service import resolve_alert_channel
        from .embeds import build_reward_embed

        channel = await resolve_alert_channel(bot, reward.guild_id, config)
        if channel is None:
            return False
        await channel.send(
            content=f"⏰ **Verizon Shine reminder:** `{offset_minutes}m` until this opens.",
            embed=build_reward_embed(reward, config, reason=f"{offset_minutes}m_reminder", reminder=True),
        )
        await repository.mark_reminder_sent(reward.guild_id, reward.reward_id, int(offset_minutes))
        return True
    except Exception as e:
        _warn(f"send reminder failed guild={reward.guild_id} reward={reward.reward_id}: {type(e).__name__}")
        return False


async def recover_pending_reminders(bot: Any) -> int:
    due = await repository.list_due_reminders(before=utc_now() + timedelta(seconds=30))
    sent = 0
    for row in due:
        try:
            gid = int(str(row.get("guild_id") or 0))
            rid = str(row.get("reward_id") or "")
            offset = int(row.get("offset_minutes") or 0)
            if gid <= 0 or not rid:
                continue
            reward = await repository.get_reward(gid, rid)
            if reward is None:
                continue
            cfg = await repository.get_config(gid)
            if not cfg.enabled or not cfg.reminders_enabled:
                continue
            if await send_reminder(bot, reward, cfg, offset):
                sent += 1
        except Exception as e:
            _warn(f"recover one reminder failed: {type(e).__name__}")
    if sent:
        _log(f"recovered_sent={sent}")
    return sent


def cancel_reward_reminders(guild_id: int, reward_id: str) -> int:
    count = 0
    prefix = (int(guild_id), str(reward_id))
    for key, task in list(_TASKS.items()):
        if key[:2] == prefix:
            if not task.done():
                task.cancel()
                count += 1
            _TASKS.pop(key, None)
    return count
