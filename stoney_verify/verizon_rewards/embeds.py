from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

import discord

from .models import VerizonReward, VerizonRewardConfig, parse_dt, utc_now


def discord_timestamp(value: Optional[datetime], style: str = "f") -> str:
    dt = parse_dt(value)
    if dt is None:
        return "Not detected"
    return f"<t:{int(dt.timestamp())}:{style}>"


def reminder_line(reward: VerizonReward, config: VerizonRewardConfig) -> str:
    if not reward.available_at:
        return "No opening time detected yet."
    if not config.reminders_enabled:
        return "Reminders are off."
    parts = []
    for offset in config.reminder_offsets_minutes:
        when = parse_dt(reward.available_at)
        if when is None:
            continue
        parts.append(f"{offset}m before")
    return ", ".join(parts) if parts else "No reminder offsets configured."


def build_reward_embed(
    reward: VerizonReward,
    config: VerizonRewardConfig,
    *,
    reason: str = "new_reward",
    reminder: bool = False,
) -> discord.Embed:
    title_prefix = "⏰ Verizon reward reminder" if reminder else "📡 Verizon Shine alert"
    embed = discord.Embed(
        title=f"{title_prefix}: {reward.title[:180]}",
        description=(
            "**Action:** Open **My Verizon app → Me → Shine**.\n"
            "This alert is read-only. It does **not** claim rewards or touch your Verizon login."
        ),
        color=discord.Color.gold() if str(reward.priority).lower() in {"high", "urgent"} else discord.Color.blurple(),
        timestamp=utc_now(),
    )
    embed.add_field(name="Type", value=str(reward.type or "Unknown"), inline=True)
    embed.add_field(name="Status", value=str(reward.status or "unknown"), inline=True)
    embed.add_field(name="Priority", value=str(reward.priority or "normal"), inline=True)
    embed.add_field(name="Available", value=discord_timestamp(reward.available_at, "F"), inline=True)
    embed.add_field(name="Expires", value=discord_timestamp(reward.expires_at, "F"), inline=True)
    embed.add_field(name="Source", value=str(reward.source or "manual")[:128], inline=True)
    embed.add_field(name="Reminders", value=reminder_line(reward, config)[:1024], inline=False)
    embed.add_field(name="First seen", value=discord_timestamp(reward.first_seen_at, "R"), inline=True)
    embed.add_field(name="Why posted", value=str(reason or "new_reward"), inline=True)

    raw = str(reward.raw_text or "").strip()
    if raw:
        embed.add_field(name="Detected text", value=raw[:1000], inline=False)

    embed.set_footer(text="Dank Shield Verizon alerts • read-only, no auto-claiming")
    return embed


def build_status_embed(config: VerizonRewardConfig, *, stored_rewards: int = 0) -> discord.Embed:
    embed = discord.Embed(
        title="📡 Verizon Shine/myAccess alerts",
        color=discord.Color.green() if config.enabled else discord.Color.orange(),
        timestamp=utc_now(),
    )
    embed.description = (
        "Safe alert-only module for Shine rewards, Daily Drops, Epic Wins, presales, "
        "ticket access, gift cards, merch, and countdowns."
    )
    embed.add_field(name="Enabled", value="Yes" if config.enabled else "No", inline=True)
    embed.add_field(name="Alert channel", value=f"<#{config.alert_channel_id}>" if config.alert_channel_id else "Not set", inline=True)
    embed.add_field(name="Reminders", value="On" if config.reminders_enabled else "Off", inline=True)
    embed.add_field(name="Offsets", value=", ".join(f"{x}m" for x in config.reminder_offsets_minutes), inline=True)
    embed.add_field(name="Stored rewards", value=str(int(stored_rewards)), inline=True)
    embed.add_field(name="Staff only", value="Yes" if config.staff_only_commands else "No", inline=True)
    keywords = ", ".join(config.priority_keywords)
    embed.add_field(name="Priority keywords", value=keywords[:1024] or "None", inline=False)
    embed.set_footer(text="No Verizon passwords, no claiming, no CAPTCHA bypass.")
    return embed


def build_digest_embed(config: VerizonRewardConfig, rewards: Iterable[VerizonReward]) -> discord.Embed:
    embed = discord.Embed(
        title="🗞️ Verizon rewards digest",
        color=discord.Color.blurple(),
        timestamp=utc_now(),
    )
    lines: list[str] = []
    for reward in rewards:
        available = discord_timestamp(reward.available_at, "R") if reward.available_at else "time unknown"
        lines.append(f"• **{reward.title[:90]}** — {reward.type}, `{reward.status}`, {available}, priority `{reward.priority}`")
    embed.description = "\n".join(lines[:20]) if lines else "No rewards have been saved yet."
    embed.set_footer(text="Digest is based on saved manual scans and notification relay events.")
    return embed
