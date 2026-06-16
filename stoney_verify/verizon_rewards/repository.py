from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Mapping, Optional

from .models import VerizonReward, VerizonRewardConfig, utc_now, parse_dt

CONFIG_TABLE = "verizon_reward_configs"
REWARDS_TABLE = "verizon_rewards"
REMINDERS_TABLE = "verizon_reward_reminders"

_MEMORY_CONFIGS: dict[int, VerizonRewardConfig] = {}
_MEMORY_REWARDS: dict[tuple[int, str], VerizonReward] = {}
_MEMORY_REMINDERS: dict[tuple[int, str, int], dict[str, Any]] = {}


def _log(message: str) -> None:
    try:
        print(f"📡 verizon_rewards.repo {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verizon_rewards.repo {message}")
    except Exception:
        pass


def _sb():
    try:
        from ..globals import get_supabase

        return get_supabase()
    except Exception:
        return None


async def _to_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _select_config_sync(guild_id: int) -> VerizonRewardConfig:
    sb = _sb()
    if sb is None:
        return _MEMORY_CONFIGS.get(int(guild_id), VerizonRewardConfig(guild_id=int(guild_id)))

    try:
        res = sb.table(CONFIG_TABLE).select("*").eq("guild_id", str(int(guild_id))).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], Mapping):
            return VerizonRewardConfig.from_row(rows[0], guild_id=int(guild_id))
    except Exception as e:
        _warn(f"config read failed guild={guild_id}: {type(e).__name__}")
    return VerizonRewardConfig(guild_id=int(guild_id))


async def get_config(guild_id: int) -> VerizonRewardConfig:
    return await _to_thread(_select_config_sync, int(guild_id))


def _upsert_config_sync(config: VerizonRewardConfig) -> VerizonRewardConfig:
    _MEMORY_CONFIGS[int(config.guild_id)] = config
    sb = _sb()
    if sb is None:
        return config

    payload = config.to_row()
    try:
        try:
            sb.table(CONFIG_TABLE).upsert(payload, on_conflict="guild_id").execute()
        except TypeError:
            sb.table(CONFIG_TABLE).upsert(payload).execute()
    except Exception as e:
        _warn(f"config upsert failed guild={config.guild_id}: {type(e).__name__}")
    return config


async def save_config(config: VerizonRewardConfig) -> VerizonRewardConfig:
    return await _to_thread(_upsert_config_sync, config)


async def patch_config(guild_id: int, **patch: Any) -> VerizonRewardConfig:
    cfg = await get_config(int(guild_id))
    for key, value in patch.items():
        if not hasattr(cfg, key):
            continue
        setattr(cfg, key, value)
    return await save_config(cfg)


async def add_keyword(guild_id: int, keyword: str, *, updated_by: str = "") -> tuple[VerizonRewardConfig, bool]:
    clean = str(keyword or "").strip().lower()
    cfg = await get_config(int(guild_id))
    existing = list(cfg.priority_keywords)
    if not clean or clean in existing:
        return cfg, False
    existing.append(clean)
    cfg.priority_keywords = tuple(existing)
    cfg.updated_by = updated_by
    return await save_config(cfg), True


async def remove_keyword(guild_id: int, keyword: str, *, updated_by: str = "") -> tuple[VerizonRewardConfig, bool]:
    clean = str(keyword or "").strip().lower()
    cfg = await get_config(int(guild_id))
    updated = tuple(k for k in cfg.priority_keywords if k != clean)
    changed = updated != cfg.priority_keywords
    cfg.priority_keywords = updated
    cfg.updated_by = updated_by
    if changed:
        cfg = await save_config(cfg)
    return cfg, changed


def _get_reward_sync(guild_id: int, reward_id: str) -> Optional[VerizonReward]:
    key = (int(guild_id), str(reward_id))
    if key in _MEMORY_REWARDS:
        return _MEMORY_REWARDS[key]

    sb = _sb()
    if sb is None:
        return None

    try:
        res = (
            sb.table(REWARDS_TABLE)
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .eq("reward_id", str(reward_id))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows and isinstance(rows[0], Mapping):
            reward = VerizonReward.from_row(rows[0])
            _MEMORY_REWARDS[key] = reward
            return reward
    except Exception as e:
        _warn(f"reward read failed guild={guild_id} reward={reward_id}: {type(e).__name__}")
    return None


async def get_reward(guild_id: int, reward_id: str) -> Optional[VerizonReward]:
    return await _to_thread(_get_reward_sync, int(guild_id), str(reward_id))


def _upsert_reward_sync(reward: VerizonReward) -> VerizonReward:
    key = (int(reward.guild_id), str(reward.reward_id))
    old_memory = _MEMORY_REWARDS.get(key)
    old_db: Optional[VerizonReward] = None

    sb = _sb()
    if sb is not None:
        try:
            old_db = _get_reward_sync(reward.guild_id, reward.reward_id)
        except Exception:
            old_db = None

    old = old_db or old_memory
    if old and old.first_seen_at:
        reward.first_seen_at = old.first_seen_at
    reward.last_seen_at = utc_now()
    _MEMORY_REWARDS[key] = reward

    if sb is None:
        return reward

    payload = reward.to_row()
    try:
        if old_db:
            payload["first_seen_at"] = old_db.first_seen_at.isoformat()
            sb.table(REWARDS_TABLE).update(payload).eq("guild_id", str(int(reward.guild_id))).eq("reward_id", reward.reward_id).execute()
        else:
            try:
                sb.table(REWARDS_TABLE).upsert(payload, on_conflict="guild_id,reward_id").execute()
            except TypeError:
                sb.table(REWARDS_TABLE).upsert(payload).execute()
    except Exception as e:
        _warn(f"reward upsert failed guild={reward.guild_id} reward={reward.reward_id}: {type(e).__name__}")
    return reward


async def save_reward(reward: VerizonReward) -> VerizonReward:
    return await _to_thread(_upsert_reward_sync, reward)


def _list_rewards_sync(guild_id: int, *, limit: int = 10) -> list[VerizonReward]:
    sb = _sb()
    rewards: list[VerizonReward] = []
    if sb is not None:
        try:
            res = (
                sb.table(REWARDS_TABLE)
                .select("*")
                .eq("guild_id", str(int(guild_id)))
                .order("last_seen_at", desc=True)
                .limit(int(limit))
                .execute()
            )
            for row in getattr(res, "data", None) or []:
                if isinstance(row, Mapping):
                    rewards.append(VerizonReward.from_row(row))
            return rewards
        except Exception as e:
            _warn(f"reward list failed guild={guild_id}: {type(e).__name__}")

    values = [reward for (gid, _rid), reward in _MEMORY_REWARDS.items() if gid == int(guild_id)]
    values.sort(key=lambda r: r.last_seen_at, reverse=True)
    return values[: int(limit)]


async def list_rewards(guild_id: int, *, limit: int = 10) -> list[VerizonReward]:
    return await _to_thread(_list_rewards_sync, int(guild_id), limit=limit)


def _pending_reminders_sync(*, before: Optional[datetime] = None) -> list[dict[str, Any]]:
    cutoff = parse_dt(before) or utc_now()
    sb = _sb()
    rows: list[dict[str, Any]] = []

    if sb is not None:
        try:
            res = (
                sb.table(REMINDERS_TABLE)
                .select("*")
                .eq("sent", False)
                .lte("remind_at", cutoff.isoformat())
                .limit(100)
                .execute()
            )
            for row in getattr(res, "data", None) or []:
                if isinstance(row, Mapping):
                    rows.append(dict(row))
            return rows
        except Exception as e:
            _warn(f"pending reminders read failed: {type(e).__name__}")

    for item in _MEMORY_REMINDERS.values():
        when = parse_dt(item.get("remind_at"))
        if not item.get("sent") and when and when <= cutoff:
            rows.append(dict(item))
    return rows


async def list_due_reminders(*, before: Optional[datetime] = None) -> list[dict[str, Any]]:
    return await _to_thread(_pending_reminders_sync, before=before)


def _upsert_reminder_sync(guild_id: int, reward_id: str, offset_minutes: int, remind_at: datetime) -> dict[str, Any]:
    payload = {
        "guild_id": str(int(guild_id)),
        "reward_id": str(reward_id),
        "offset_minutes": int(offset_minutes),
        "remind_at": parse_dt(remind_at).isoformat(),
        "sent": False,
        "updated_at": utc_now().isoformat(),
    }
    key = (int(guild_id), str(reward_id), int(offset_minutes))
    _MEMORY_REMINDERS[key] = payload

    sb = _sb()
    if sb is not None:
        try:
            try:
                sb.table(REMINDERS_TABLE).upsert(payload, on_conflict="guild_id,reward_id,offset_minutes").execute()
            except TypeError:
                sb.table(REMINDERS_TABLE).upsert(payload).execute()
        except Exception as e:
            _warn(f"reminder upsert failed guild={guild_id} reward={reward_id} offset={offset_minutes}: {type(e).__name__}")
    return payload


async def save_reminder(guild_id: int, reward_id: str, offset_minutes: int, remind_at: datetime) -> dict[str, Any]:
    return await _to_thread(_upsert_reminder_sync, int(guild_id), str(reward_id), int(offset_minutes), remind_at)


def _mark_reminder_sent_sync(guild_id: int, reward_id: str, offset_minutes: int) -> None:
    key = (int(guild_id), str(reward_id), int(offset_minutes))
    if key in _MEMORY_REMINDERS:
        _MEMORY_REMINDERS[key]["sent"] = True
        _MEMORY_REMINDERS[key]["sent_at"] = utc_now().isoformat()

    sb = _sb()
    if sb is not None:
        try:
            sb.table(REMINDERS_TABLE).update({"sent": True, "sent_at": utc_now().isoformat()}).eq("guild_id", str(int(guild_id))).eq("reward_id", str(reward_id)).eq("offset_minutes", int(offset_minutes)).execute()
        except Exception as e:
            _warn(f"mark reminder sent failed guild={guild_id} reward={reward_id}: {type(e).__name__}")


async def mark_reminder_sent(guild_id: int, reward_id: str, offset_minutes: int) -> None:
    await _to_thread(_mark_reminder_sent_sync, int(guild_id), str(reward_id), int(offset_minutes))
