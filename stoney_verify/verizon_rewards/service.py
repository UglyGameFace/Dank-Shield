from __future__ import annotations

from typing import Any, Optional

import discord

from . import repository
from .dedupe import should_alert
from .embeds import build_digest_embed, build_reward_embed
from .models import VerizonReward, VerizonRewardConfig, utc_now
from .parsing import parse_rewards_from_text


def _log(message: str) -> None:
    try:
        print(f"📡 verizon_rewards {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verizon_rewards {message}")
    except Exception:
        pass


async def resolve_alert_channel(bot: discord.Client, guild_id: int, config: Optional[VerizonRewardConfig] = None) -> Optional[discord.TextChannel]:
    cfg = config or await repository.get_config(int(guild_id))
    if not cfg.alert_channel_id:
        return None

    channel = bot.get_channel(int(cfg.alert_channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(cfg.alert_channel_id))
        except Exception:
            channel = None

    if not isinstance(channel, discord.TextChannel):
        return None

    try:
        guild = channel.guild
        me = guild.me
        if me is not None:
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                return None
    except Exception:
        pass

    return channel


async def save_and_maybe_alert(
    *,
    bot: discord.Client,
    reward: VerizonReward,
    config: Optional[VerizonRewardConfig] = None,
    force_alert: bool = False,
) -> dict[str, Any]:
    cfg = config or await repository.get_config(int(reward.guild_id))
    existing = await repository.get_reward(reward.guild_id, reward.reward_id)
    alert, reason = should_alert(existing=existing, incoming=reward, config=cfg, now=utc_now())
    saved = await repository.save_reward(reward)

    scheduled = 0
    try:
        from .scheduler import schedule_reward_reminders

        scheduled = await schedule_reward_reminders(bot, saved, cfg)
    except Exception as e:
        _warn(f"reminder schedule failed guild={reward.guild_id} reward={reward.reward_id}: {type(e).__name__}")

    posted = False
    if force_alert or (cfg.enabled and alert):
        channel = await resolve_alert_channel(bot, reward.guild_id, cfg)
        if channel is not None:
            try:
                await channel.send(embed=build_reward_embed(saved, cfg, reason="forced_test" if force_alert else reason))
                posted = True
            except Exception as e:
                _warn(f"alert post failed guild={reward.guild_id} reward={reward.reward_id}: {type(e).__name__}")

    return {
        "reward": saved,
        "posted": posted,
        "alert_reason": "forced_test" if force_alert else reason,
        "scheduled_reminders": scheduled,
        "new": existing is None,
        "changed": bool(alert),
    }


async def scan_text(
    *,
    bot: discord.Client,
    guild_id: int,
    text: str,
    source: str = "manual",
    force_alert: bool = False,
) -> dict[str, Any]:
    cfg = await repository.get_config(int(guild_id))
    rewards = parse_rewards_from_text(
        text,
        guild_id=int(guild_id),
        source=source,
        priority_keywords=cfg.priority_keywords,
    )

    results = []
    for reward in rewards:
        results.append(await save_and_maybe_alert(bot=bot, reward=reward, config=cfg, force_alert=force_alert))

    return {
        "ok": True,
        "guild_id": str(int(guild_id)),
        "detected": len(rewards),
        "posted": sum(1 for item in results if item.get("posted")),
        "scheduled_reminders": sum(int(item.get("scheduled_reminders") or 0) for item in results),
        "results": results,
    }


async def scan_notification(
    *,
    bot: discord.Client,
    guild_id: int,
    title: str,
    body: str,
    source: str = "android_notification",
) -> dict[str, Any]:
    combined = "\n".join(part for part in (str(title or "").strip(), str(body or "").strip()) if part)
    if not _looks_relevant(combined):
        return {"ok": True, "ignored": True, "reason": "not_relevant"}
    return await scan_text(bot=bot, guild_id=int(guild_id), text=combined, source=source)


def _looks_relevant(text: str) -> bool:
    normalized = str(text or "").lower()
    terms = (
        "verizon",
        "shine",
        "myaccess",
        "my access",
        "reward",
        "daily drop",
        "epic wins",
        "presale",
        "ticket",
        "gift card",
        "fifa",
        "sweepstakes",
    )
    return any(term in normalized for term in terms)


async def send_test_alert(bot: discord.Client, guild_id: int) -> dict[str, Any]:
    cfg = await repository.get_config(int(guild_id))
    sample = (
        "Verizon Shine Daily Drop\n"
        "$25 gift card Daily Drop\n"
        "Available in 30m\n"
        "Open My Verizon app > Shine"
    )
    result = await scan_text(bot=bot, guild_id=int(guild_id), text=sample, source="test-alert", force_alert=True)
    result["configured"] = bool(cfg.alert_channel_id)
    return result


async def build_digest(bot: discord.Client, guild_id: int, *, post: bool = False) -> tuple[discord.Embed, int]:
    cfg = await repository.get_config(int(guild_id))
    rewards = await repository.list_rewards(int(guild_id), limit=15)
    embed = build_digest_embed(cfg, rewards)
    if post and cfg.enabled:
        channel = await resolve_alert_channel(bot, guild_id, cfg)
        if channel is not None:
            await channel.send(embed=embed)
    return embed, len(rewards)


async def recover_pending_reminders(bot: discord.Client) -> int:
    try:
        from .scheduler import recover_pending_reminders as _recover

        return await _recover(bot)
    except Exception as e:
        _warn(f"recover pending reminders failed: {type(e).__name__}")
        return 0


def summarize_scan_result(result: dict[str, Any]) -> str:
    detected = int(result.get("detected") or 0)
    posted = int(result.get("posted") or 0)
    scheduled = int(result.get("scheduled_reminders") or 0)
    if detected <= 0:
        return "No Verizon rewards were detected. Paste the reward title/countdown/status text from Shine and try again."
    return f"Detected `{detected}` reward(s), posted `{posted}` alert(s), scheduled `{scheduled}` reminder(s)."
