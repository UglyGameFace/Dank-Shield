from __future__ import annotations

"""Optional per-guild welcome/goodbye event automation.

This does not replace the static /dank welcome start-here message. It adds
ProBot-style join/leave messages when guild owners enable them in config.
Default behavior is safe: disabled unless explicitly enabled by setup/commands.
"""

from datetime import datetime, timezone
import random
import re
from typing import Any, Mapping, Optional

import discord

_PATCHED = False


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if value is None:
            return bool(default)
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    except Exception:
        return bool(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _cfg_str(cfg: Any, *keys: str, default: str = "") -> str:
    for key in keys:
        try:
            text = str(_cfg_value(cfg, key, "") or "").strip()
            if text:
                return text
        except Exception:
            continue
    return default


def _cfg_bool(cfg: Any, *keys: str, default: bool = False) -> bool:
    for key in keys:
        raw = _cfg_value(cfg, key, None)
        if raw is not None:
            return _safe_bool(raw, default)
    return bool(default)


def _clean_name(value: Any) -> str:
    return str(value or "").lower().replace("_", "-").replace(" ", "-")


def _text_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(int(channel_id or 0)) if int(channel_id or 0) > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def _channel_by_name(guild: discord.Guild, *tokens: str) -> Optional[discord.TextChannel]:
    wanted = tuple(_clean_name(token) for token in tokens if str(token or "").strip())
    if not wanted:
        return None
    for channel in list(getattr(guild, "text_channels", []) or []):
        if not isinstance(channel, discord.TextChannel):
            continue
        name = _clean_name(getattr(channel, "name", ""))
        if any(token in name for token in wanted):
            return channel
    return None



def _role_from_cfg(guild: discord.Guild, cfg: Any, *keys: str) -> Optional[discord.Role]:
    for key in keys:
        try:
            rid = _safe_int(_cfg_value(cfg, key, None), 0)
            role = guild.get_role(int(rid or 0)) if int(rid or 0) > 0 else None
            if isinstance(role, discord.Role):
                return role
        except Exception:
            continue
    return None


def _member_can_view_join_channel(guild: discord.Guild, cfg: Any, channel: Optional[discord.TextChannel]) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        unverified = _role_from_cfg(
            guild,
            cfg,
            "unverified_role_id",
            "verify_unverified_role_id",
            "verification_unverified_role_id",
            "member_unverified_role_id",
        )
        if isinstance(unverified, discord.Role) and channel.permissions_for(unverified).view_channel:
            return True
        everyone = getattr(guild, "default_role", None)
        if isinstance(everyone, discord.Role) and channel.permissions_for(everyone).view_channel:
            return True
    except Exception:
        pass
    return False


def _member_facing_join_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    configured = _target_channel(guild, cfg, kind="join")
    if _member_can_view_join_channel(guild, cfg, configured):
        return configured

    # Do not force Unverified into staff logs. Fall back to visible onboarding channels.
    for key in (
        "verify_channel_id",
        "verification_channel_id",
        "welcome_channel_id",
        "rules_channel_id",
    ):
        channel = _text_channel(guild, _safe_int(_cfg_value(cfg, key, None), 0))
        if _member_can_view_join_channel(guild, cfg, channel):
            return channel

    for tokens in (
        ("welcome", "start-here"),
        ("verification", "verify"),
        ("rules",),
        ("support", "ticket"),
    ):
        channel = _channel_by_name(guild, *tokens)
        if _member_can_view_join_channel(guild, cfg, channel):
            return channel

    return configured


def _target_channel(guild: discord.Guild, cfg: Any, *, kind: str) -> Optional[discord.TextChannel]:
    if kind == "leave":
        cid = _safe_int(_cfg_value(cfg, "goodbye_channel_id", None) or _cfg_value(cfg, "leave_channel_id", None) or _cfg_value(cfg, "welcome_channel_id", None), 0)
        return _text_channel(guild, cid) or _channel_by_name(guild, "goodbye", "farewell", "welcome")
    cid = _safe_int(_cfg_value(cfg, "join_welcome_channel_id", None) or _cfg_value(cfg, "welcome_channel_id", None), 0)
    return _text_channel(guild, cid) or _channel_by_name(guild, "welcome", "start-here")


def _channel_mention(guild: discord.Guild, cfg: Any, *, keys: tuple[str, ...], names: tuple[str, ...]) -> str:
    for key in keys:
        channel = _text_channel(guild, _safe_int(_cfg_value(cfg, key, None), 0))
        if isinstance(channel, discord.TextChannel):
            return channel.mention
    channel = _channel_by_name(guild, *names)
    if isinstance(channel, discord.TextChannel):
        return channel.mention
    return "not set"


def _age_text(dt: Any) -> str:
    try:
        if dt is None:
            return "unknown"
        now = datetime.now(timezone.utc)
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = max(0, int((now - dt).total_seconds()))
        days = seconds // 86400
        if days >= 365:
            years = days // 365
            rem_days = days % 365
            months = rem_days // 30
            return f"{years}y {months}mo" if months else f"{years}y"
        if days >= 30:
            months = days // 30
            rem = days % 30
            return f"{months}mo {rem}d" if rem else f"{months}mo"
        if days >= 1:
            return f"{days}d"
        hours = seconds // 3600
        if hours:
            return f"{hours}h"
        minutes = seconds // 60
        return f"{minutes}m" if minutes else "just now"
    except Exception:
        return "unknown"


def _discord_time(dt: Any) -> str:
    try:
        if dt is None:
            return "unknown"
        unix = int(dt.timestamp())
        return f"<t:{unix}:F> (<t:{unix}:R>)"
    except Exception:
        return "unknown"


def _server_profile(guild: discord.Guild) -> str:
    text_parts = [str(getattr(guild, "name", "") or "")]
    try:
        text_parts.extend(str(c.name or "") for c in getattr(guild, "categories", []) or [])
        text_parts.extend(str(c.name or "") for c in getattr(guild, "text_channels", []) or [])
    except Exception:
        pass
    text = " ".join(text_parts).lower()

    if any(w in text for w in ("game", "gaming", "clips", "lobby", "ranked", "xbox", "playstation", "cod", "minecraft")):
        return "gaming"
    if any(w in text for w in ("support", "ticket", "help", "docs", "faq")):
        return "support"
    if any(w in text for w in ("class", "study", "school", "course", "learn", "lesson")):
        return "education"
    if any(w in text for w in ("shop", "store", "client", "business", "orders", "sales")):
        return "business"
    if any(w in text for w in ("stream", "creator", "youtube", "twitch", "media", "art")):
        return "creator"
    return "community"


def _random_welcome_line(guild: discord.Guild) -> str:
    profile = _server_profile(guild)
    lines = {
        "gaming": [
            "Grab your role, check the rules, and jump into the lobby when you are ready.",
            "Welcome in — get verified, find your channels, and enjoy the games.",
            "Good to have you here. Start with the rules, then head into the community channels.",
        ],
        "support": [
            "Welcome in — check the getting-started info and open a ticket if you need help.",
            "Glad you made it. Read the basics first, then reach out if you need support.",
            "Start with the rules and support info so staff can help you faster.",
        ],
        "education": [
            "Welcome in — start with the rules, then check the learning channels.",
            "Glad you joined. Review the start-here info and settle into the right channels.",
            "Start with the basics, then jump into the learning space that fits you.",
        ],
        "business": [
            "Welcome — please review the rules and start-here information before posting.",
            "Glad you joined. Check the welcome info so you know where to go first.",
            "Start with the rules and support channels so everything stays organized.",
        ],
        "creator": [
            "Welcome in — check the rules, introduce yourself, and explore the creator channels.",
            "Glad you are here. Start with the welcome info, then jump into the community.",
            "Review the basics first, then share and connect when you are ready.",
        ],
        "community": [
            "Welcome in — start with the rules, verify if needed, and enjoy the community.",
            "Glad you made it. Check the start-here info and get comfortable.",
            "Start with the basics, then jump into the channels that fit you.",
        ],
    }
    try:
        return random.choice(lines.get(profile) or lines["community"])
    except Exception:
        return "Welcome in — start with the rules and enjoy the community."


async def _recent_join_context(member: discord.Member) -> dict[str, Any]:
    try:
        from stoney_verify.members_new.join_context_service import get_recent_join_context
        return dict(get_recent_join_context(int(member.guild.id), int(member.id)) or {})
    except Exception:
        return {}


def _invite_placeholder_values(context: Mapping[str, Any] | None) -> dict[str, str]:
    ctx = dict(context or {})
    code = str(ctx.get("invite_code") or "").strip()
    invited_by = str(ctx.get("invited_by") or "").strip()
    invited_by_name = str(ctx.get("invited_by_name") or "").strip()
    channel_name = str(ctx.get("channel_name") or "").strip()
    source = str(ctx.get("join_source") or ctx.get("entry_method") or "unknown").strip()
    confidence = str(ctx.get("entry_confidence") or "").strip()
    invite_link = f"https://discord.gg/{code}" if code and code != "vanity" else ("server vanity URL" if code == "vanity" else "unknown")
    owner = invited_by_name or (f"<@{invited_by}>" if invited_by else "unknown")
    channel = f"#{channel_name}" if channel_name else "unknown"
    if confidence:
        source = f"{source} ({confidence}% confidence)"
    return {
        "invite_code": code or "unknown",
        "invite_link": invite_link,
        "invite_source": source or "unknown",
        "invite_channel": channel,
        "invite_owner": owner,
        "invite_inviter": owner,
        "invite_owner_id": invited_by or "unknown",
        "invite_inviter_id": invited_by or "unknown",
    }


def _format(text: str, member: discord.Member, *, cfg: Any | None = None, context: Mapping[str, Any] | None = None) -> str:
    guild = member.guild
    replacements = {
        "server_name": str(getattr(guild, "name", "this server") or "this server"),
        "member": str(getattr(member, "display_name", "") or member),
        "member_name": str(getattr(member, "display_name", "") or member),
        "user": str(getattr(member, "display_name", "") or member),
        "mention": member.mention,
        "member_mention": member.mention,
        "username": str(member),
        "display_name": str(getattr(member, "display_name", "") or member),
        "member_count": str(getattr(guild, "member_count", "") or ""),
        "account_age": _age_text(getattr(member, "created_at", None)),
        "joined_at": _discord_time(getattr(member, "joined_at", None)),
        "rules_channel": _channel_mention(guild, cfg, keys=("rules_channel_id", "rules_id"), names=("rules",)) if cfg is not None else "not set",
        "verify_channel": _channel_mention(guild, cfg, keys=("verify_channel_id", "verification_channel_id", "verify_id"), names=("verification", "verify")) if cfg is not None else "not set",
        "support_channel": _channel_mention(guild, cfg, keys=("support_channel_id", "ticket_channel_id", "tickets_channel_id", "support_id"), names=("support", "ticket", "help")) if cfg is not None else "not set",
        "random_welcome_line": _random_welcome_line(guild),
    }
    replacements.update(_invite_placeholder_values(context))
    out = str(text or "")
    for key, value in replacements.items():
        out = out.replace("{" + key + "}", value)
    return out[:1900]



def _compact_leave_description(title_text: str, body_text: str, member: discord.Member) -> str:
    """Remove repetitive leave wording like:
    Title: 'Mason left'
    Body:  'Mason left Server. Member count: 28.'
    """

    try:
        title_l = str(title_text or "").casefold()
        body = str(body_text or "").strip()
        if "left" not in title_l:
            return body

        names = [
            str(getattr(member, "display_name", "") or "").strip(),
            str(getattr(member, "name", "") or "").strip(),
            str(member).strip(),
        ]
        names = [name for name in dict.fromkeys(names) if name]

        lowered = body.casefold()
        if not any(lowered.startswith(f"{name.casefold()} left") for name in names):
            return body

        match = re.search(r"member count\s*:\s*([0-9,]+)", body, flags=re.I)
        if match:
            return f"Members now: {match.group(1)}."

        if "." in body:
            rest = body.split(".", 1)[1].strip()
            if rest:
                return rest

        return body
    except Exception:
        return str(body_text or "").strip()


def _embed(title: str, body: str, member: discord.Member, *, goodbye: bool = False, cfg: Any | None = None, context: Mapping[str, Any] | None = None) -> discord.Embed:
    title_text = _format(title, member, cfg=cfg, context=context)[:256]
    body_text = _format(body, member, cfg=cfg, context=context)[:4000]

    if goodbye:
        body_text = _compact_leave_description(title_text, body_text, member)[:4000]

    embed = discord.Embed(
        title=title_text,
        description=body_text,
        color=discord.Color.dark_grey() if goodbye else discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text="dank_shield:welcome_event:v1")
    return embed



def _bot_can_post(channel: Optional[discord.TextChannel]) -> bool:
    try:
        if not isinstance(channel, discord.TextChannel):
            return False
        me = channel.guild.me
        if not isinstance(me, discord.Member):
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.send_messages and perms.embed_links)
    except Exception:
        return False


def _staff_join_log_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    """Private staff audit channel for join/leave records.

    This intentionally prefers leave/log configured channels. It does not require
    Unverified to see message history.
    """

    for key in (
        "welcome_log_channel_id",
        "join_leave_log_channel_id",
        "join_leave_channel_id",
        "member_log_channel_id",
        "goodbye_channel_id",
        "leave_channel_id",
    ):
        channel = _text_channel(guild, _safe_int(_cfg_value(cfg, key, None), 0))
        if _bot_can_post(channel):
            return channel

    for tokens in (
        ("join-leave-log",),
        ("join-leave",),
        ("member-log",),
        ("welcome-log",),
        ("leave-log",),
        ("log",),
    ):
        channel = _channel_by_name(guild, *tokens)
        if _bot_can_post(channel):
            return channel

    return None


def _member_join_audit_embed(
    member: discord.Member,
    *,
    public_channel: Optional[discord.TextChannel],
    public_message_id: int = 0,
    context: Mapping[str, Any] | None = None,
) -> discord.Embed:
    invite = _invite_placeholder_values(context)
    account_age = _age_text(getattr(member, "created_at", None))
    joined_at = _discord_time(getattr(member, "joined_at", None))

    public_text = public_channel.mention if isinstance(public_channel, discord.TextChannel) else "not posted"
    if isinstance(public_channel, discord.TextChannel) and int(public_message_id or 0) > 0:
        public_text += f"\nMessage ID: `{int(public_message_id)}`"

    embed = discord.Embed(
        title=f"👋 {getattr(member, 'display_name', None) or member} joined",
        description=(
            f"{member.mention}\n"
            f"`{int(member.id)}`"
        ),
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Public welcome", value=public_text, inline=False)
    embed.add_field(name="Account", value=f"Age: `{account_age}`\nJoined: {joined_at}", inline=False)
    embed.add_field(
        name="Invite/source",
        value=(
            f"Source: `{invite.get('invite_source', 'unknown')}`\n"
            f"Invite: `{invite.get('invite_code', 'unknown')}`\n"
            f"Creator: {invite.get('invite_owner', 'unknown')}\n"
            f"Target: `{invite.get('invite_channel', 'unknown')}`"
        ),
        inline=False,
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text="dank_shield:staff_join_audit:v1")
    return embed


async def _send_staff_join_audit(
    member: discord.Member,
    cfg: Any,
    *,
    public_channel: Optional[discord.TextChannel],
    public_message_id: int = 0,
    context: Mapping[str, Any] | None = None,
) -> None:
    try:
        log_channel = _staff_join_log_channel(member.guild, cfg)
        if not isinstance(log_channel, discord.TextChannel):
            return

        # Avoid duplicate cards if staff intentionally uses the same channel for public welcome and log.
        if isinstance(public_channel, discord.TextChannel) and int(log_channel.id) == int(public_channel.id):
            return

        await log_channel.send(
            embed=_member_join_audit_embed(
                member,
                public_channel=public_channel,
                public_message_id=public_message_id,
                context=context,
            ),
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
    except Exception as exc:
        try:
            print(
                "⚠️ welcome_member_events staff join audit failed "
                f"guild={getattr(getattr(member, 'guild', None), 'id', 0)} "
                f"user={getattr(member, 'id', 0)} error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass


async def _send_join(member: discord.Member) -> None:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(member.guild.id), refresh=True)
        if not _cfg_bool(cfg, "welcome_join_enabled", "join_welcome_enabled", default=False):
            return
        channel = _member_facing_join_channel(member.guild, cfg)
        if not isinstance(channel, discord.TextChannel):
            return
        title = _cfg_str(cfg, "welcome_join_title", default="Welcome, {display_name}!")
        body = _cfg_str(cfg, "welcome_join_body", default="{random_welcome_line}\n\nStart here: {rules_channel} • Verify: {verify_channel} • Help: {support_channel}")
        context = await _recent_join_context(member)
        sent = await channel.send(
            embed=_embed(title, body, member, cfg=cfg, context=context),
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        await _send_staff_join_audit(
            member,
            cfg,
            public_channel=channel,
            public_message_id=int(getattr(sent, "id", 0) or 0),
            context=context,
        )
    except Exception as exc:
        try:
            print(f"⚠️ welcome_member_events join failed guild={getattr(getattr(member, 'guild', None), 'id', 0)} user={getattr(member, 'id', 0)} error={type(exc).__name__}: {exc}")
        except Exception:
            pass


async def _send_leave(member: discord.Member) -> None:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(member.guild.id), refresh=True)
        if not _cfg_bool(cfg, "welcome_leave_enabled", "goodbye_enabled", "leave_message_enabled", default=False):
            return
        channel = _target_channel(member.guild, cfg, kind="leave")
        if not isinstance(channel, discord.TextChannel):
            return
        title = _cfg_str(cfg, "welcome_leave_title", default="{display_name} left")
        body = _cfg_str(cfg, "welcome_leave_body", default="Members now: {member_count}.")
        await channel.send(embed=_embed(title, body, member, goodbye=True, cfg=cfg, context=None), allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        try:
            print(f"⚠️ welcome_member_events leave failed guild={getattr(getattr(member, 'guild', None), 'id', 0)} user={getattr(member, 'id', 0)} error={type(exc).__name__}: {exc}")
        except Exception:
            pass


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify import client as bot  # type: ignore
    except Exception:
        try:
            from stoney_verify.globals import bot  # type: ignore
        except Exception:
            bot = None  # type: ignore
    if bot is None:
        return False
    try:
        bot.add_listener(_send_join, "on_member_join")
        bot.add_listener(_send_leave, "on_member_remove")
        _PATCHED = True
        print("✅ welcome_member_events_guard active; optional join/leave messages attached")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ welcome_member_events_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
