from __future__ import annotations

"""Passive verification role drift monitor.

When an established member loses all configured safe access roles, staff should
get a review card. The monitor never punishes the member; it only reports the
state change so role/setup mistakes can be repaired quickly.
"""

from datetime import datetime, timezone
from typing import Any

import discord

_ATTACHED = False
_LAST_ALERT: dict[tuple[int, int], float] = {}
_ALERT_COOLDOWN_SECONDS = 300

_SAFE_KEYS: tuple[str, ...] = (
    "verified_role_id",
    "resident_role_id",
    "staff_role_id",
    "vc_staff_role_id",
    "stoner_role_id",
    "drunken_role_id",
    "member_role_id",
)
_PENDING_KEYS: tuple[str, ...] = ("unverified_role_id",)


def _log(message: str) -> None:
    try:
        print(f"🧭 verification_role_drift_monitor {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ verification_role_drift_monitor {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _role_ids(member: discord.Member) -> set[int]:
    try:
        return {int(role.id) for role in getattr(member, "roles", []) or []}
    except Exception:
        return set()


def _cfg_id(cfg: Any, key: str) -> int:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return _safe_int(value, 0)
    except Exception:
        pass
    try:
        return _safe_int(getattr(cfg, key, 0), 0)
    except Exception:
        return 0


def _configured_ids(cfg: Any, keys: tuple[str, ...]) -> set[int]:
    out: set[int] = set()
    for key in keys:
        value = _cfg_id(cfg, key)
        if value > 0:
            out.add(value)
    return out


def _joined_age_seconds(member: discord.Member) -> float | None:
    try:
        joined_at = getattr(member, "joined_at", None)
        if joined_at is None:
            return None
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - joined_at.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _is_established(member: discord.Member) -> bool:
    age = _joined_age_seconds(member)
    if age is None:
        return True
    return age >= 10 * 60


async def _get_cfg(guild_id: int) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        try:
            return await get_guild_config(int(guild_id), refresh=True)
        except TypeError:
            return await get_guild_config(int(guild_id))
    except Exception:
        return None


def _role_names(member: discord.Member, ids: set[int]) -> str:
    names: list[str] = []
    try:
        for role in getattr(member, "roles", []) or []:
            if int(role.id) in ids:
                names.append(f"{role.name} (`{role.id}`)")
    except Exception:
        pass
    return "\n".join(names[:10]) if names else "None"


def _changed_roles(before: discord.Member, after: discord.Member) -> tuple[set[int], set[int]]:
    before_ids = _role_ids(before)
    after_ids = _role_ids(after)
    return before_ids - after_ids, after_ids - before_ids


def _should_rate_limit(guild_id: int, user_id: int) -> bool:
    key = (int(guild_id), int(user_id))
    now = datetime.now(timezone.utc).timestamp()
    last = _LAST_ALERT.get(key, 0.0)
    if now - last < _ALERT_COOLDOWN_SECONDS:
        return True
    _LAST_ALERT[key] = now
    return False


async def _post_review_card(before: discord.Member, after: discord.Member, *, safe_ids: set[int], pending_ids: set[int]) -> None:
    try:
        from stoney_verify.modlog import _get_modlog_channel

        channel = await _get_modlog_channel(after.guild)  # type: ignore[misc]
        if not isinstance(channel, discord.TextChannel):
            return
        removed, added = _changed_roles(before, after)
        embed = discord.Embed(
            title="🧭 Verification Role Drift Review",
            description=(
                "An established member lost all configured safe access roles. "
                "No automatic punishment was taken. Staff should review whether this was intentional."
            ),
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="User", value=f"{after.mention}\n`{after}`\n`{after.id}`", inline=False)
        embed.add_field(name="Configured Safe Roles Before", value=_role_names(before, safe_ids)[:1024], inline=False)
        embed.add_field(name="Safe Roles After", value=_role_names(after, safe_ids)[:1024], inline=False)
        embed.add_field(name="Pending/Unverified Roles After", value=_role_names(after, pending_ids)[:1024], inline=False)
        embed.add_field(name="Roles Added", value=_role_names(after, added)[:1024], inline=False)
        embed.add_field(name="Roles Removed", value=_role_names(before, removed)[:1024], inline=False)
        embed.add_field(
            name="Recommended Staff Action",
            value="Confirm the member still has the correct Verified/Resident/member role, or update `/dank setup` if your safe role changed.",
            inline=False,
        )
        embed.set_footer(text=f"Guild {after.guild.id} • verification role drift monitor")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _on_member_update(before: discord.Member, after: discord.Member) -> None:
    try:
        if getattr(after, "bot", False) or not _is_established(after):
            return
        if _role_ids(before) == _role_ids(after):
            return
        cfg = await _get_cfg(after.guild.id)
        safe_ids = _configured_ids(cfg, _SAFE_KEYS)
        if not safe_ids:
            return
        pending_ids = _configured_ids(cfg, _PENDING_KEYS)
        before_had_safe = bool(_role_ids(before) & safe_ids)
        after_has_safe = bool(_role_ids(after) & safe_ids)
        if not before_had_safe or after_has_safe:
            return
        if _should_rate_limit(after.guild.id, after.id):
            return
        await _post_review_card(before, after, safe_ids=safe_ids, pending_ids=pending_ids)
        _warn(f"verification role drift review posted guild={after.guild.id} user={after.id}")
    except Exception as e:
        _warn(f"member update check failed: {e!r}")


def apply() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True
    try:
        from stoney_verify.globals import bot

        bot.add_listener(_on_member_update, "on_member_update")
        _ATTACHED = True
        _log("active; established-member role drift review listener attached")
        return True
    except Exception as e:
        _warn(f"attach failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
