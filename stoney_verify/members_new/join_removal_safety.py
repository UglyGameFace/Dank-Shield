from __future__ import annotations

"""
Native fresh-join removal safety helpers.

Public/listing-site servers can receive noisy-looking joins. Stoney must not
instantly kick/ban a brand-new human member unless the server owner explicitly
enables that behavior or a staff-confirmed moderation flow opts into it.

This module owns the real business rules so runtime patches do not keep carrying
production logic forever.

Production behavior:
If verification automation tries to fail-closed kick/ban a fresh join because
the member has no safe verification role state, first try to assign/recover the
configured Unverified role. If recovery succeeds, suppress the removal. If
recovery fails, block the removal anyway and log exactly why setup/role hierarchy
must be fixed.
"""

import asyncio
import contextvars
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import discord


TIMER_TYPES: tuple[str, ...] = ("join_grace", "member_no_ticket")
FRESH_JOIN_OVERRIDE_MARKERS: tuple[str, ...] = (
    "manual_override:fresh_join_removal",
    "confirmed_staff_override:fresh_join_removal",
)
FAIL_CLOSED_RECOVERY_MARKERS: tuple[str, ...] = (
    "verification fail-closed",
    "no safe verification role state",
    "ensured_unverified=false",
)

_ALLOW_FRESH_JOIN_REMOVAL_CONTEXT: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "stoney_allow_fresh_join_removal_context",
    default=False,
)


@dataclass(frozen=True)
class FreshJoinRemovalDecision:
    blocked: bool
    action: str
    reason: str
    member_id: int
    guild_id: int
    join_age_seconds: Optional[float]
    protection_minutes: int
    allow_env_enabled: bool
    staff_confirmed: bool


def _log(message: str) -> None:
    try:
        print(f"🛡️ join_removal_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ join_removal_safety {message}")
    except Exception:
        pass


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    except Exception:
        pass
    return default


def safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def fresh_join_protection_minutes() -> int:
    for key in (
        "FRESH_JOIN_REMOVAL_PROTECTION_MINUTES",
        "VERIFY_FRESH_JOIN_KICK_PROTECTION_MINUTES",
        "JOIN_KICK_PROTECTION_MINUTES",
    ):
        value = safe_int(os.getenv(key), 0)
        if value > 0:
            return value
    return 10


def staff_confirmed_removal_context_active() -> bool:
    try:
        return bool(_ALLOW_FRESH_JOIN_REMOVAL_CONTEXT.get(False))
    except Exception:
        return False


def bot_fresh_join_removal_allowed() -> bool:
    # Default is intentionally false for public/listing-site traffic. The
    # context var is only set around explicit staff-confirmed command runners.
    return bool(
        safe_bool(os.getenv("ALLOW_BOT_FRESH_JOIN_REMOVAL"), False)
        or staff_confirmed_removal_context_active()
    )


def member_join_age_seconds(member: discord.Member) -> Optional[float]:
    try:
        joined_at = getattr(member, "joined_at", None)
        if joined_at is None:
            return None
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - joined_at.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def is_fresh_join(member: discord.Member) -> bool:
    age = member_join_age_seconds(member)
    if age is None:
        # If Discord does not expose joined_at, fail safe: treat as protected.
        return True
    return age < (fresh_join_protection_minutes() * 60)


def has_fresh_join_override(reason: Any) -> bool:
    reason_text = safe_str(reason, "").lower()
    return any(marker in reason_text for marker in FRESH_JOIN_OVERRIDE_MARKERS)


def is_fail_closed_verification_reason(reason: Any) -> bool:
    reason_text = safe_str(reason, "").lower()
    return any(marker in reason_text for marker in FAIL_CLOSED_RECOVERY_MARKERS)


def should_block_bot_fresh_join_removal(member: Any, *, reason: Any = None) -> bool:
    if bot_fresh_join_removal_allowed():
        return False
    if not isinstance(member, discord.Member):
        return False
    if getattr(member, "bot", False):
        return False
    if not is_fresh_join(member):
        return False
    if has_fresh_join_override(reason):
        return False
    return True


def removal_decision(member: Any, *, action: str, reason: Any = None, staff_confirmed: bool = False) -> FreshJoinRemovalDecision:
    guild = getattr(member, "guild", None)
    blocked = False if staff_confirmed else should_block_bot_fresh_join_removal(member, reason=reason)
    return FreshJoinRemovalDecision(
        blocked=blocked,
        action=safe_str(action, "remove").lower(),
        reason=safe_str(reason, "No reason provided"),
        member_id=safe_int(getattr(member, "id", 0), 0),
        guild_id=safe_int(getattr(guild, "id", 0), 0),
        join_age_seconds=member_join_age_seconds(member) if isinstance(member, discord.Member) else None,
        protection_minutes=fresh_join_protection_minutes(),
        allow_env_enabled=safe_bool(os.getenv("ALLOW_BOT_FRESH_JOIN_REMOVAL"), False),
        staff_confirmed=bool(staff_confirmed),
    )


async def clear_persisted_member_wait_timers(guild_id: int, user_id: int, *, reason: str) -> None:
    try:
        from stoney_verify.commands_ext import kick_timers

        try:
            cancel_all = getattr(kick_timers, "cancel_verification_wait_timers_for_member", None)
            if callable(cancel_all):
                await cancel_all(int(guild_id), int(user_id))
        except Exception:
            pass

        delete_fn = getattr(kick_timers, "member_wait_timer_persist_delete", None)
        if callable(delete_fn):
            for timer_type in TIMER_TYPES:
                try:
                    await delete_fn(int(guild_id), int(user_id), timer_type=timer_type)
                except TypeError:
                    try:
                        await delete_fn(int(guild_id), int(user_id), timer_type)
                    except Exception:
                        pass
                except Exception:
                    pass
        _log(f"cleared stale verification wait timers guild={guild_id} user={user_id} reason={reason}")
    except Exception as e:
        _warn(f"failed clearing stale verification timers guild={guild_id} user={user_id}: {e!r}")


async def clear_stale_timers_for_join(member: discord.Member, *, reason: str = "fresh member join") -> None:
    if getattr(member, "bot", False):
        return
    await clear_persisted_member_wait_timers(member.guild.id, member.id, reason=reason)


async def _get_cfg(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        return await asyncio.wait_for(get_guild_config(guild.id), timeout=3.0)
    except Exception:
        return None


async def configured_fresh_join_safety_log_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    channels: list[discord.TextChannel] = []
    seen: set[int] = set()
    cfg = await _get_cfg(guild)

    # Staff-first routing. Do not put removal evidence in public welcome/exit if
    # modlog/raidlog is available.
    for attr in (
        "modlog_channel_id",
        "raid_log_channel_id",
        "raidlog_channel_id",
        "force_verify_log_channel_id",
        "join_log_channel_id",
        "join_exit_log_channel_id",
        "member_log_channel_id",
    ):
        cid = safe_int(getattr(cfg, attr, 0), 0) if cfg is not None else 0
        if cid <= 0 or cid in seen:
            continue
        try:
            ch = guild.get_channel(cid)
            if not isinstance(ch, discord.TextChannel):
                continue
            perms = ch.permissions_for(guild.me) if guild.me else None
            if perms is not None and (not perms.view_channel or not perms.send_messages or not perms.embed_links):
                continue
            channels.append(ch)
            seen.add(cid)
        except Exception:
            continue

    return channels[:3]


def build_fresh_join_removal_embed(
    guild: discord.Guild,
    member: discord.Member,
    *,
    action: str,
    reason: Any,
    prevented: bool,
    staff_confirmed: bool = False,
) -> discord.Embed:
    age = member_join_age_seconds(member)
    age_text = "unknown" if age is None else f"{int(age)}s"
    clean_action = safe_str(action, "remove").lower()
    title = f"🛡️ Fresh Join {clean_action.title()} Blocked" if prevented else f"👢 Fresh Join {clean_action.title()} Executed"

    embed = discord.Embed(
        title=title,
        color=discord.Color.gold() if prevented else discord.Color.orange(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="User", value=f"{member.mention}\n`{member}`\n`{member.id}`", inline=False)
    embed.add_field(name="Bot Action", value=clean_action.upper(), inline=True)
    embed.add_field(name="Joined Age", value=age_text, inline=True)
    embed.add_field(name="Protection Window", value=f"{fresh_join_protection_minutes()} minute(s)", inline=True)
    embed.add_field(name="Staff Confirmed", value="yes" if staff_confirmed else "no", inline=True)
    embed.add_field(name="Reason", value=safe_str(reason, "No reason provided")[:1024], inline=False)
    embed.add_field(
        name="Result",
        value=(
            "Blocked. This bot is not allowed to instantly remove fresh joins. This protects public invite/listing-site traffic from over-tight automation."
            if prevented
            else "Executed after protection checks."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • fresh join removal safety")
    return embed


def build_fresh_join_recovery_embed(
    guild: discord.Guild,
    member: discord.Member,
    *,
    action: str,
    reason: Any,
    recovery_detail: str,
    success: bool,
) -> discord.Embed:
    age = member_join_age_seconds(member)
    age_text = "unknown" if age is None else f"{int(age)}s"
    clean_action = safe_str(action, "remove").lower()
    title = "🧷 Fresh Join Verification Role Recovered" if success else "⚠️ Fresh Join Verification Recovery Failed"
    embed = discord.Embed(
        title=title,
        color=discord.Color.green() if success else discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="User", value=f"{member.mention}\n`{member}`\n`{member.id}`", inline=False)
    embed.add_field(name="Original Bot Action", value=clean_action.upper(), inline=True)
    embed.add_field(name="Joined Age", value=age_text, inline=True)
    embed.add_field(name="Reason", value=safe_str(reason, "No reason provided")[:1024], inline=False)
    embed.add_field(name="Recovery Result", value=safe_str(recovery_detail, "unknown")[:1024], inline=False)
    embed.add_field(
        name="Action Taken",
        value=(
            "The bot assigned/recovered the safe verification role and suppressed the instant removal."
            if success
            else "The instant removal was still blocked. Fix the Unverified role, bot Manage Roles permission, or bot role hierarchy."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • fresh join verification recovery")
    return embed


async def post_fresh_join_removal_log(
    guild: discord.Guild,
    member: discord.Member,
    *,
    action: str,
    reason: Any,
    prevented: bool,
    staff_confirmed: bool = False,
) -> None:
    try:
        embed = build_fresh_join_removal_embed(
            guild,
            member,
            action=action,
            reason=reason,
            prevented=prevented,
            staff_confirmed=staff_confirmed,
        )
        sent = False
        for channel in await configured_fresh_join_safety_log_channels(guild):
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                sent = True
            except Exception:
                continue
        if not sent:
            _warn(
                f"fresh join {action} {'blocked' if prevented else 'executed'} but no writable log channel "
                f"guild={guild.id} user={member.id} reason={reason!r}"
            )
    except Exception:
        pass


async def post_fresh_join_recovery_log(
    guild: discord.Guild,
    member: discord.Member,
    *,
    action: str,
    reason: Any,
    recovery_detail: str,
    success: bool,
) -> None:
    try:
        embed = build_fresh_join_recovery_embed(
            guild,
            member,
            action=action,
            reason=reason,
            recovery_detail=recovery_detail,
            success=success,
        )
        sent = False
        for channel in await configured_fresh_join_safety_log_channels(guild):
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                sent = True
            except Exception:
                continue
        if not sent:
            _warn(
                f"fresh join recovery {'succeeded' if success else 'failed'} but no writable log channel "
                f"guild={guild.id} user={member.id} detail={recovery_detail!r}"
            )
    except Exception:
        pass


async def try_recover_unverified_before_removal(
    member: discord.Member,
    *,
    action: str,
    reason: Any,
) -> tuple[bool, str]:
    """Recover safe verification role state before blocking/removing a fresh join."""
    if not isinstance(member, discord.Member) or getattr(member, "bot", False):
        return False, "not a human guild member"
    if not is_fail_closed_verification_reason(reason):
        return False, "removal reason is not verification fail-closed"

    try:
        from stoney_verify.startup_guards.fresh_join_role_recovery import ensure_fresh_join_unverified_role

        ok, detail = await ensure_fresh_join_unverified_role(
            member,
            source="native_join_removal_safety_fail_closed",
            log_success=False,
        )
        if ok:
            await clear_persisted_member_wait_timers(
                member.guild.id,
                member.id,
                reason="fresh join verification role recovered before fail-closed removal",
            )
            await post_fresh_join_recovery_log(
                member.guild,
                member,
                action=action,
                reason=reason,
                recovery_detail=detail,
                success=True,
            )
            _log(f"suppressed fail-closed fresh join {action} after native role recovery guild={member.guild.id} user={member.id}")
            return True, detail

        await post_fresh_join_recovery_log(
            member.guild,
            member,
            action=action,
            reason=reason,
            recovery_detail=detail,
            success=False,
        )
        _warn(f"native role recovery failed before fresh join {action} guild={member.guild.id} user={member.id}: {detail}")
        return False, detail
    except Exception as e:
        detail = f"native role recovery crashed: {type(e).__name__}: {e}"
        await post_fresh_join_recovery_log(
            member.guild,
            member,
            action=action,
            reason=reason,
            recovery_detail=detail,
            success=False,
        )
        _warn(f"{detail} guild={member.guild.id} user={member.id}")
        return False, detail


async def block_or_run_bot_removal(
    *,
    action: str,
    guild: discord.Guild,
    member: discord.Member,
    reason: Any,
    runner: Callable[[], Awaitable[Any]],
    staff_confirmed: bool = False,
) -> Any:
    decision = removal_decision(member, action=action, reason=reason, staff_confirmed=staff_confirmed)
    if decision.blocked:
        recovered, recovery_detail = await try_recover_unverified_before_removal(member, action=action, reason=reason)
        if recovered:
            return None

        await clear_persisted_member_wait_timers(guild.id, member.id, reason=f"blocked fresh join bot {action}")
        _warn(
            f"blocked fresh-join bot {action} guild={guild.id} user={member.id} "
            f"age={decision.join_age_seconds} recovery={recovery_detail!r} reason={reason!r}"
        )
        await post_fresh_join_removal_log(
            guild,
            member,
            action=action,
            reason=f"{reason}\n\nRecovery detail: {recovery_detail}",
            prevented=True,
            staff_confirmed=staff_confirmed,
        )
        return None

    token = None
    if staff_confirmed:
        try:
            token = _ALLOW_FRESH_JOIN_REMOVAL_CONTEXT.set(True)
        except Exception:
            token = None

    try:
        result = await runner()
    finally:
        if token is not None:
            try:
                _ALLOW_FRESH_JOIN_REMOVAL_CONTEXT.reset(token)
            except Exception:
                pass

    try:
        age = member_join_age_seconds(member)
        if age is not None and age <= (fresh_join_protection_minutes() * 60):
            await post_fresh_join_removal_log(
                guild,
                member,
                action=action,
                reason=reason,
                prevented=False,
                staff_confirmed=staff_confirmed,
            )
    except Exception:
        pass
    return result


__all__ = [
    "FAIL_CLOSED_RECOVERY_MARKERS",
    "FRESH_JOIN_OVERRIDE_MARKERS",
    "FreshJoinRemovalDecision",
    "TIMER_TYPES",
    "block_or_run_bot_removal",
    "bot_fresh_join_removal_allowed",
    "build_fresh_join_recovery_embed",
    "build_fresh_join_removal_embed",
    "clear_persisted_member_wait_timers",
    "clear_stale_timers_for_join",
    "configured_fresh_join_safety_log_channels",
    "fresh_join_protection_minutes",
    "has_fresh_join_override",
    "is_fail_closed_verification_reason",
    "is_fresh_join",
    "member_join_age_seconds",
    "post_fresh_join_recovery_log",
    "post_fresh_join_removal_log",
    "removal_decision",
    "safe_bool",
    "safe_int",
    "safe_str",
    "should_block_bot_fresh_join_removal",
    "staff_confirmed_removal_context_active",
    "try_recover_unverified_before_removal",
]
