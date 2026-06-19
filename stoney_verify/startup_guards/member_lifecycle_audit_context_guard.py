from __future__ import annotations

"""Richer staff-only member join audit context.

This guard is intentionally scoped to the existing member lifecycle router. It only
changes the staff audit embed and invite context text; it does not change routing,
roles, permissions, welcome messages, moderation, tickets, setup, or verification.
"""

import time
from typing import Any, Optional

import discord

try:
    from stoney_verify.startup_guards import member_lifecycle_router_guard as router
except Exception:  # pragma: no cover
    router = None  # type: ignore

_PATCHED = False
_ORIGINAL_DETECT = None


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip().strip("<#@!&>")
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _fmt_dt(value: Any, *, style: str = "F", default: str = "unknown") -> str:
    try:
        if value is None:
            return default
        return discord.utils.format_dt(value, style=style)
    except Exception:
        return default


def _avatar_url(member: discord.Member) -> str:
    try:
        return str(member.display_avatar.url)
    except Exception:
        return ""


def _channel_text(guild: discord.Guild, channel_id: Any, channel_name: Any = "") -> str:
    cid = _safe_int(channel_id, 0)
    channel = guild.get_channel(cid) if cid > 0 else None
    if isinstance(channel, discord.TextChannel):
        return channel.mention
    name = _safe_str(channel_name)
    if name:
        return f"#{name}"
    return "unknown"


def _user_text(guild: discord.Guild, user_id: Any, user_name: Any = "") -> str:
    uid = _safe_int(user_id, 0)
    if uid > 0:
        user = guild.get_member(uid)
        if user is not None:
            return f"{user.mention} (`{uid}`)"
        name = _safe_str(user_name, "unknown")
        return f"{name} (`{uid}`)"
    return _safe_str(user_name, "unknown")


def _hidden_dank_context(member: discord.Member) -> str:
    """Return privacy-safe cross-server context.

    For a public multi-server bot, do not reveal other server names/IDs in a staff
    log. This only tells current staff whether the user is already visible to this
    bot somewhere else, without exposing the other customer/server.
    """

    try:
        bot = getattr(router, "bot", None)
        guilds = list(getattr(bot, "guilds", []) or [])
        seen_elsewhere = 0
        checked = 0
        for guild in guilds:
            try:
                if int(getattr(guild, "id", 0)) == int(member.guild.id):
                    continue
                checked += 1
                if guild.get_member(int(member.id)) is not None:
                    seen_elsewhere += 1
            except Exception:
                continue
        if seen_elsewhere:
            return f"Known to Dank Shield elsewhere: **Yes** (`{seen_elsewhere}+` server hidden for privacy)"
        if checked:
            return "Known to Dank Shield elsewhere: `Not seen in cache`"
        return "Known to Dank Shield elsewhere: `unknown`"
    except Exception:
        return "Known to Dank Shield elsewhere: `unknown`"


async def _detect_invite_full(member: discord.Member) -> dict[str, Any]:
    if router is None:
        return {"confidence": "router unavailable"}

    guild = member.guild
    gid = int(guild.id)
    async with router._cache_lock(gid):  # type: ignore[attr-defined]
        old = dict(router._INVITE_CACHE.get(gid) or {})  # type: ignore[attr-defined]
        old_meta = dict(router._INVITE_META.get(gid) or {})  # type: ignore[attr-defined]
        had_cache = bool(old)
        cache_age = max(0, int(time.monotonic() - float(router._INVITE_CACHE_AT.get(gid, 0) or 0))) if had_cache else 0  # type: ignore[attr-defined]

        current, meta, status = await router._fetch_invite_snapshot(guild)  # type: ignore[attr-defined]
        if status != "ok":
            return {
                "source": "unknown",
                "invite": "unknown",
                "creator": "unknown",
                "target": "unknown",
                "destination": "unknown",
                "special_target": "none",
                "confidence": status,
                "cache_state": "invite snapshot unavailable",
                "use_delta": "unknown",
                "uses": "unknown",
            }

        router._INVITE_CACHE[gid] = dict(current)  # type: ignore[attr-defined]
        router._INVITE_META[gid] = dict(meta)  # type: ignore[attr-defined]
        router._INVITE_CACHE_AT[gid] = time.monotonic()  # type: ignore[attr-defined]

        if not had_cache:
            return {
                "source": "unknown",
                "invite": "unknown",
                "creator": "unknown",
                "target": "unknown",
                "destination": "unknown",
                "special_target": "none",
                "confidence": "invite cache was cold; warmed now",
                "cache_state": "cold cache",
                "use_delta": "unknown",
                "uses": "unknown",
            }

        candidates: list[tuple[int, str]] = []
        for code, uses in current.items():
            before = int(old.get(code, 0) or 0)
            delta = int(uses or 0) - before
            if delta > 0:
                candidates.append((delta, code))

        if not candidates:
            return {
                "source": "unknown",
                "invite": "unknown",
                "creator": "unknown",
                "target": "unknown",
                "destination": "unknown",
                "special_target": "none",
                "confidence": "no invite use delta detected",
                "cache_state": f"warm cache, age {cache_age}s",
                "use_delta": "0",
                "uses": "unknown",
            }

        candidates.sort(reverse=True)
        delta, code = candidates[0]
        item = meta.get(code) or old_meta.get(code) or {}
        destination = _channel_text(guild, item.get("channel_id"), item.get("channel_name"))
        creator = _user_text(guild, item.get("inviter_id"), item.get("inviter_name"))
        target_id = _safe_str(item.get("target_id"))
        special_target = _user_text(guild, target_id, item.get("target_name")) if target_id else "none"
        uses = current.get(code, item.get("uses", "unknown"))
        previous = old.get(code, "unknown")

        return {
            "source": destination,
            "invite": code,
            "creator": creator,
            "target": destination,
            "destination": destination,
            "special_target": special_target,
            "confidence": "matched invite use delta",
            "cache_state": f"warm cache, age {cache_age}s",
            "use_delta": f"+{delta}",
            "uses": str(uses),
            "previous_uses": str(previous),
        }


async def _send_staff_join_audit_full(
    member: discord.Member,
    channel: Optional[discord.TextChannel],
    public_channel: Optional[discord.TextChannel],
    invite: dict[str, Any],
) -> None:
    if router is None:
        return
    if not router._bot_can_send(channel):  # type: ignore[attr-defined]
        router._log(f"staff audit skipped guild={member.guild.id} member={member.id}: no staff audit channel")  # type: ignore[attr-defined]
        return

    now = discord.utils.utcnow()
    embed = discord.Embed(
        title=f"👋 {member.display_name} joined",
        description="Staff-only join audit with invite routing, account age, and privacy-safe Dank Shield context.",
        color=discord.Color.green(),
        timestamp=now,
    )
    avatar = _avatar_url(member)
    if avatar:
        embed.set_thumbnail(url=avatar)

    username = _safe_str(getattr(member, "name", ""), "unknown")
    discriminator = _safe_str(getattr(member, "discriminator", ""))
    tag = username if discriminator in {"", "0"} else f"{username}#{discriminator}"
    embed.add_field(
        name="Member",
        value=(
            f"Mention: {member.mention}\n"
            f"Display: **{member.display_name}**\n"
            f"Username: `{tag}`\n"
            f"ID: `{member.id}`\n"
            f"Bot account: **{'Yes' if getattr(member, 'bot', False) else 'No'}**"
        )[:1024],
        inline=False,
    )

    embed.add_field(
        name="Account",
        value=(
            f"Created: {_fmt_dt(getattr(member, 'created_at', None), style='F')}\n"
            f"Age: **{router._member_age_text(member)}**\n"  # type: ignore[attr-defined]
            f"Joined server: {_fmt_dt(now, style='F')}\n"
            f"Server member count: **{member.guild.member_count or 'unknown'}**"
        )[:1024],
        inline=False,
    )

    embed.add_field(
        name="Routing",
        value=(
            f"Public welcome: {public_channel.mention if isinstance(public_channel, discord.TextChannel) else '`Not configured`'}\n"
            f"Staff audit: {channel.mention if isinstance(channel, discord.TextChannel) else '`Not configured`'}"
        )[:1024],
        inline=False,
    )

    embed.add_field(
        name="Invite/source",
        value=(
            f"Invite destination: {invite.get('destination') or invite.get('source') or 'unknown'}\n"
            f"Invite code: `{invite.get('invite') or 'unknown'}`\n"
            f"Creator: {invite.get('creator') or 'unknown'}\n"
            f"Special target: `{invite.get('special_target') or 'none'}`\n"
            f"Uses: `{invite.get('previous_uses') or 'unknown'} → {invite.get('uses') or 'unknown'}` ({invite.get('use_delta') or 'unknown'})\n"
            f"Confidence: `{invite.get('confidence') or 'unknown'}`\n"
            f"Cache: `{invite.get('cache_state') or 'unknown'}`"
        )[:1024],
        inline=False,
    )

    embed.add_field(name="Dank Shield context", value=_hidden_dank_context(member)[:1024], inline=False)
    embed.set_footer(text="dank_shield:staff_join_audit:v3")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


def install() -> bool:
    global _PATCHED, _ORIGINAL_DETECT
    if _PATCHED:
        return True
    if router is None:
        return False
    try:
        _ORIGINAL_DETECT = getattr(router, "_detect_invite", None)
        router._detect_invite = _detect_invite_full  # type: ignore[attr-defined]
        router._send_staff_join_audit = _send_staff_join_audit_full  # type: ignore[attr-defined]
        _PATCHED = True
        print("✅ member_lifecycle_audit_context_guard active; staff join audit v3 has full invite/account context")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ member_lifecycle_audit_context_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


install()

__all__ = ["install"]
