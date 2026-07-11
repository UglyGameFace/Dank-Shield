from __future__ import annotations

"""Authoritative member join/leave router.

Rules:
- The public welcome channel is for static welcome/rules content only.
- Join/leave event cards are posted only to the explicit join/leave log route.
- Staff join/leave audit goes only to staff/modlog/audit routes.
- Staff audit never falls back into public channels.
- Legacy welcome_member_events_guard listeners are removed when possible.
"""

import asyncio
import time
from typing import Any, Optional

import discord
from discord import app_commands

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

try:
    from stoney_verify.commands_ext.public_setup_group import dank_group
except Exception:  # pragma: no cover
    dank_group = None  # type: ignore

_INSTALLED = False
_INVITE_CACHE: dict[int, dict[str, int]] = {}
_INVITE_META: dict[int, dict[str, dict[str, Any]]] = {}
_INVITE_CACHE_AT: dict[int, float] = {}
_CACHE_LOCKS: dict[int, asyncio.Lock] = {}

PUBLIC_WELCOME_KEYS = (
    "public_welcome_channel_id",
    "welcome_channel_id",
    "welcome_public_channel_id",
    "member_welcome_channel_id",
)

JOIN_LEAVE_KEYS = (
    "join_leave_log_channel_id",
    "join_leave_channel_id",
    "member_join_leave_log_channel_id",
    "member_lifecycle_log_channel_id",
    "member_log_channel_id",
    "member_logs_channel_id",
    "join_log_channel_id",
    "join_exit_log_channel_id",
    "joinlog_channel_id",
    "joinleave_channel_id",
    "leave_log_channel_id",
    "welcome_leave_channel_id",
    "welcome_exit_channel_id",
    "welcome_exit_log_channel_id",
    "leave_channel_id",
)

STAFF_AUDIT_KEYS = (
    "staff_join_audit_channel_id",
    "member_audit_log_channel_id",
    "staff_log_channel_id",
    "staff_logs_channel_id",
    "modlog_channel_id",
    "mod_log_channel_id",
    "audit_log_channel_id",
)


def _log(message: str) -> None:
    try:
        print(f"👋 member_lifecycle_router_guard {message}")
    except Exception:
        pass


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
            if isinstance(nested, dict) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


async def _load_config(guild_id: int) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config
        return await get_guild_config(int(guild_id), refresh=True)
    except Exception:
        return None


def _resolve_channel(guild: discord.Guild, cfg: Any, keys: tuple[str, ...]) -> Optional[discord.TextChannel]:
    for key in keys:
        cid = _safe_int(_cfg_value(cfg, key, None), 0)
        if cid <= 0:
            continue
        channel = guild.get_channel(cid)
        if isinstance(channel, discord.TextChannel):
            return channel
    return None


def _same_channel(a: Any, b: Any) -> bool:
    try:
        return isinstance(a, discord.TextChannel) and isinstance(b, discord.TextChannel) and int(a.id) == int(b.id)
    except Exception:
        return False


def _bot_can_send(channel: Optional[discord.TextChannel]) -> bool:
    try:
        if channel is None:
            return False
        me = channel.guild.me
        if not isinstance(me, discord.Member):
            return False
        perms = channel.permissions_for(me)
        ok = bool(perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history)
        if not ok:
            _log(
                "channel not writable "
                f"guild={channel.guild.id} channel={channel.id} "
                f"view={bool(perms.view_channel)} send={bool(perms.send_messages)} "
                f"embed={bool(perms.embed_links)} history={bool(perms.read_message_history)}"
            )
        return ok
    except Exception as exc:
        _log(f"channel permission check failed: {type(exc).__name__}: {exc}")
        return False


def _bot_can_read_invites(guild: discord.Guild) -> bool:
    try:
        me = guild.me
        if not isinstance(me, discord.Member):
            return False
        perms = me.guild_permissions
        return bool(getattr(perms, "manage_guild", False) or getattr(perms, "administrator", False))
    except Exception:
        return False


def _cache_lock(guild_id: int) -> asyncio.Lock:
    lock = _CACHE_LOCKS.get(int(guild_id))
    if lock is None:
        lock = asyncio.Lock()
        _CACHE_LOCKS[int(guild_id)] = lock
    return lock


async def _fetch_invite_snapshot(guild: discord.Guild) -> tuple[dict[str, int], dict[str, dict[str, Any]], str]:
    if not _bot_can_read_invites(guild):
        return {}, {}, "missing Manage Server permission"
    try:
        invites = await guild.invites()
    except discord.Forbidden:
        return {}, {}, "forbidden reading invites"
    except Exception as exc:
        return {}, {}, f"{type(exc).__name__}: {_safe_str(exc)[:80]}"

    uses: dict[str, int] = {}
    meta: dict[str, dict[str, Any]] = {}
    for invite in invites:
        code = _safe_str(getattr(invite, "code", "")).lower()
        if not code:
            continue
        uses[code] = int(getattr(invite, "uses", 0) or 0)
        inviter = getattr(invite, "inviter", None)
        channel = getattr(invite, "channel", None)
        target_user = getattr(invite, "target_user", None)
        meta[code] = {
            "code": code,
            "uses": uses[code],
            "inviter_id": str(getattr(inviter, "id", "") or ""),
            "inviter_name": str(inviter) if inviter else "",
            "channel_id": str(getattr(channel, "id", "") or ""),
            "channel_name": _safe_str(getattr(channel, "name", "")),
            "target_id": str(getattr(target_user, "id", "") or ""),
            "target_name": str(target_user) if target_user else "",
        }
    return uses, meta, "ok"


async def _warm_invite_cache(guild: discord.Guild, *, reason: str = "warm") -> None:
    async with _cache_lock(int(guild.id)):
        uses, meta, status = await _fetch_invite_snapshot(guild)
        if status == "ok":
            _INVITE_CACHE[int(guild.id)] = dict(uses)
            _INVITE_META[int(guild.id)] = dict(meta)
            _INVITE_CACHE_AT[int(guild.id)] = time.monotonic()
        _log(f"invite cache {status} guild={guild.id} reason={reason} invites={len(uses)}")


async def _detect_invite(member: discord.Member) -> dict[str, Any]:
    guild = member.guild
    gid = int(guild.id)
    async with _cache_lock(gid):
        old = dict(_INVITE_CACHE.get(gid) or {})
        old_meta = dict(_INVITE_META.get(gid) or {})
        had_cache = bool(old)
        current, meta, status = await _fetch_invite_snapshot(guild)
        if status != "ok":
            return {"source": "unknown", "invite": "unknown", "creator": "unknown", "target": "unknown", "confidence": status}
        _INVITE_CACHE[gid] = dict(current)
        _INVITE_META[gid] = dict(meta)
        _INVITE_CACHE_AT[gid] = time.monotonic()
        if not had_cache:
            return {"source": "unknown", "invite": "unknown", "creator": "unknown", "target": "unknown", "confidence": "invite cache was cold; warmed now"}

        candidates: list[tuple[int, str]] = []
        for code, uses in current.items():
            before = int(old.get(code, 0) or 0)
            delta = int(uses or 0) - before
            if delta > 0:
                candidates.append((delta, code))
        if not candidates:
            return {"source": "unknown", "invite": "unknown", "creator": "unknown", "target": "unknown", "confidence": "no invite use delta detected"}

        candidates.sort(reverse=True)
        _, code = candidates[0]
        item = meta.get(code) or old_meta.get(code) or {}
        channel_id = _safe_int(item.get("channel_id"), 0)
        channel = guild.get_channel(channel_id) if channel_id > 0 else None
        inviter_id = _safe_str(item.get("inviter_id"))
        inviter_text = "unknown"
        if inviter_id:
            user = guild.get_member(_safe_int(inviter_id, 0))
            inviter_text = f"{user.mention} (`{inviter_id}`)" if user else f"{_safe_str(item.get('inviter_name'), 'unknown')} (`{inviter_id}`)"
        target_text = "unknown"
        target_id = _safe_str(item.get("target_id"))
        if target_id:
            target_text = f"{_safe_str(item.get('target_name'), 'unknown')} (`{target_id}`)"
        return {
            "source": channel.mention if isinstance(channel, discord.TextChannel) else _safe_str(item.get("channel_name"), "unknown"),
            "invite": code,
            "creator": inviter_text,
            "target": target_text,
            "confidence": "matched invite use delta",
        }


def _member_age_text(member: discord.Member) -> str:
    try:
        delta = discord.utils.utcnow() - member.created_at
        days = max(0, int(delta.total_seconds() // 86400))
        years = days // 365
        months = (days % 365) // 30
        if years:
            return f"{years}y {months}mo"
        if months:
            return f"{months}mo"
        return f"{days}d"
    except Exception:
        return "unknown"


def _avatar_url(member: discord.Member) -> str:
    try:
        return str(member.display_avatar.url)
    except Exception:
        return ""


async def _send_join_leave_join(member: discord.Member, channel: Optional[discord.TextChannel]) -> None:
    if not _bot_can_send(channel):
        _log(f"join log skipped guild={member.guild.id} member={member.id}: join/leave target missing or not writable")
        return
    embed = discord.Embed(
        title=f"👋 {member.display_name} joined",
        description=f"Member: {member.mention}\nMembers now: **{member.guild.member_count or 'unknown'}**.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    avatar = _avatar_url(member)
    if avatar:
        embed.set_thumbnail(url=avatar)
    embed.set_footer(text="dank_shield:join_leave_event:v3")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    _log(f"join log sent guild={member.guild.id} member={member.id} channel={channel.id}")


async def _send_public_join(member: discord.Member, channel: Optional[discord.TextChannel]) -> None:
    # Backward-compatible symbol for older imports. It now intentionally routes
    # only as a join/leave log card and must never be used for welcome_channel_id.
    await _send_join_leave_join(member, channel)


async def _send_public_leave(member: discord.Member, channel: Optional[discord.TextChannel]) -> None:
    if not _bot_can_send(channel):
        _log(f"leave log skipped guild={member.guild.id} member={member.id}: join/leave target missing or not writable")
        return
    embed = discord.Embed(
        title=f"👋 {member.display_name} left",
        description=f"Members now: **{member.guild.member_count or 'unknown'}**.",
        color=discord.Color.dark_gray(),
        timestamp=discord.utils.utcnow(),
    )
    avatar = _avatar_url(member)
    if avatar:
        embed.set_thumbnail(url=avatar)
    embed.set_footer(text="dank_shield:join_leave_event:v3")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    _log(f"leave log sent guild={member.guild.id} member={member.id} channel={channel.id}")


async def _send_staff_join_audit(member: discord.Member, channel: Optional[discord.TextChannel], public_channel: Optional[discord.TextChannel], invite: dict[str, Any]) -> None:
    if not _bot_can_send(channel):
        _log(f"staff audit skipped guild={member.guild.id} member={member.id}: no staff audit channel")
        return
    embed = discord.Embed(title=f"👋 {member.display_name} joined", color=discord.Color.green(), timestamp=discord.utils.utcnow())
    avatar = _avatar_url(member)
    if avatar:
        embed.set_thumbnail(url=avatar)
    embed.add_field(name="Member", value=f"{member.mention}\n`{member.id}`", inline=False)
    embed.add_field(name="Public welcome", value=public_channel.mention if isinstance(public_channel, discord.TextChannel) else "`Not configured`", inline=False)
    embed.add_field(name="Account", value=f"Age: **{_member_age_text(member)}**\nJoined: {discord.utils.format_dt(discord.utils.utcnow(), style='F')}", inline=False)
    embed.add_field(
        name="Invite/source",
        value=(
            f"Source: {invite.get('source') or 'unknown'}\n"
            f"Invite: `{invite.get('invite') or 'unknown'}`\n"
            f"Creator: {invite.get('creator') or 'unknown'}\n"
            f"Target: {invite.get('target') or 'unknown'}\n"
            f"Confidence: `{invite.get('confidence') or 'unknown'}`"
        )[:1024],
        inline=False,
    )
    # Add only the most useful intelligence fields so the mobile card stays readable.
    try:
        from stoney_verify.modlog import _build_member_context_fields

        context_fields = await _build_member_context_fields(member.guild, member)
        preferred_names = (
            "Join Intelligence",
            "Evidence & Source",
            "Identity Links",
            "Smart Join Intelligence",
            "Evidence Health",
            "Containment Posture",
        )
        selected = []
        for wanted in preferred_names:
            for item in context_fields:
                if item[0] == wanted and wanted not in {row[0] for row in selected}:
                    selected.append(item)
                    break
            if len(selected) >= 3:
                break

        for name, value, inline in selected:
            if len(embed.fields) >= 24:
                break
            embed.add_field(name=name, value=str(value)[:1024], inline=bool(inline))
    except Exception as exc:
        _log(
            "staff join intelligence unavailable "
            f"guild={member.guild.id} member={member.id}: "
            f"{type(exc).__name__}: {exc}"
        )

    review_view = None
    try:
        from stoney_verify.member_review_feedback import (
            feedback_display_value,
            get_latest_member_review_feedback,
            get_latest_source_review_feedback,
            source_key_from_join_context,
        )
        from stoney_verify.member_review_ui import build_member_review_view

        source_key = source_key_from_join_context(invite)

        latest_feedback = await asyncio.to_thread(
            get_latest_member_review_feedback,
            guild_id=str(member.guild.id),
            user_id=str(member.id),
        )
        previous_value = feedback_display_value(latest_feedback)
        if previous_value and len(embed.fields) < 24:
            embed.add_field(
                name="Previous Staff Verdict",
                value=previous_value[:1024],
                inline=False,
            )

        if source_key:
            latest_source = await asyncio.to_thread(
                get_latest_source_review_feedback,
                guild_id=str(member.guild.id),
                source_key=source_key,
            )
            source_value = feedback_display_value(latest_source)
            if source_value and len(embed.fields) < 24:
                embed.add_field(
                    name="Previous Source Verdict",
                    value=(
                        f"Source: `{source_key}`\n{source_value}"
                    )[:1024],
                    inline=False,
                )

        review_view = build_member_review_view(
            guild_id=int(member.guild.id),
            target_user_id=int(member.id),
            target_is_bot=bool(member.bot),
            source_key=source_key,
            evidence_snapshot={
                "invite_context": dict(invite or {}),
                "account_age": _member_age_text(member),
                "member_is_bot": bool(member.bot),
                "member_name": str(member),
            },
        )
    except Exception as exc:
        _log(
            "staff verdict controls unavailable "
            f"guild={member.guild.id} member={member.id}: "
            f"{type(exc).__name__}: {exc}"
        )

    embed.set_footer(text="dank_shield:staff_join_audit:v4")
    await channel.send(
        embed=embed,
        view=review_view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _send_staff_leave_audit(member: discord.Member, channel: Optional[discord.TextChannel]) -> None:
    if not _bot_can_send(channel):
        return
    embed = discord.Embed(title=f"👋 {member.display_name} left", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Member", value=f"{member.mention}\n`{member.id}`", inline=False)
    embed.add_field(name="Members now", value=str(member.guild.member_count or "unknown"), inline=False)
    embed.set_footer(text="dank_shield:staff_leave_audit:v3")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def _join_listener(member: discord.Member) -> None:
    try:
        guild = member.guild
        cfg = await _load_config(int(guild.id))
        public_channel = _resolve_channel(guild, cfg, PUBLIC_WELCOME_KEYS)
        join_leave_channel = _resolve_channel(guild, cfg, JOIN_LEAVE_KEYS)
        staff_channel = _resolve_channel(guild, cfg, STAFF_AUDIT_KEYS)
        invite = await _detect_invite(member)

        if _same_channel(public_channel, join_leave_channel):
            _log(f"join log suppressed because join/leave route equals welcome guild={guild.id} channel={getattr(public_channel, 'id', None)}")
            join_leave_channel = None

        # Critical: never post automatic join cards to the public welcome channel.
        await _send_join_leave_join(member, join_leave_channel)
        await _send_staff_join_audit(member, staff_channel, public_channel, invite)
    except Exception as exc:
        _log(f"join failed guild={getattr(member.guild, 'id', 'unknown')} member={getattr(member, 'id', 'unknown')}: {type(exc).__name__}: {exc}")


async def _leave_listener(member: discord.Member) -> None:
    try:
        guild = member.guild
        cfg = await _load_config(int(guild.id))
        public_channel = _resolve_channel(guild, cfg, PUBLIC_WELCOME_KEYS)
        join_leave_channel = _resolve_channel(guild, cfg, JOIN_LEAVE_KEYS)
        staff_channel = _resolve_channel(guild, cfg, STAFF_AUDIT_KEYS)
        if _same_channel(public_channel, join_leave_channel):
            _log(f"leave log suppressed because join/leave route equals welcome guild={guild.id} channel={getattr(public_channel, 'id', None)}")
            join_leave_channel = None
        await _send_public_leave(member, join_leave_channel)
        await _send_staff_leave_audit(member, staff_channel)
        await _warm_invite_cache(guild, reason="member_remove")
    except Exception as exc:
        _log(f"leave failed guild={getattr(member.guild, 'id', 'unknown')} member={getattr(member, 'id', 'unknown')}: {type(exc).__name__}: {exc}")


async def _ready_listener() -> None:
    try:
        if bot is None:
            return
        intents = getattr(bot, "intents", None)
        if not bool(getattr(intents, "members", False)):
            _log("members intent is disabled in code; join/leave events will not fire")
        for guild in list(getattr(bot, "guilds", []) or []):
            try:
                cfg = await _load_config(int(guild.id))
                join_leave_channel = _resolve_channel(guild, cfg, JOIN_LEAVE_KEYS)
                public_channel = _resolve_channel(guild, cfg, PUBLIC_WELCOME_KEYS)
                staff_channel = _resolve_channel(guild, cfg, STAFF_AUDIT_KEYS)
                route_note = "join/leave disabled because it equals welcome" if _same_channel(public_channel, join_leave_channel) else "ok"
                _log(
                    "member lifecycle routes ready "
                    f"guild={guild.id} public={getattr(public_channel, 'id', None) or '-'} "
                    f"join_leave={getattr(join_leave_channel, 'id', None) or '-'} "
                    f"staff={getattr(staff_channel, 'id', None) or '-'} route={route_note}"
                )
                await _warm_invite_cache(guild, reason="ready")
            except Exception:
                pass
    except Exception as exc:
        _log(f"ready warm failed: {type(exc).__name__}: {exc}")


async def _guild_join_listener(guild: discord.Guild) -> None:
    await _warm_invite_cache(guild, reason="guild_join")


async def _invite_create_listener(invite: discord.Invite) -> None:
    guild = getattr(invite, "guild", None)
    if isinstance(guild, discord.Guild):
        await _warm_invite_cache(guild, reason="invite_create")


async def _invite_delete_listener(invite: discord.Invite) -> None:
    guild = getattr(invite, "guild", None)
    if isinstance(guild, discord.Guild):
        await _warm_invite_cache(guild, reason="invite_delete")


def _remove_old_welcome_listeners() -> None:
    if bot is None:
        return
    try:
        extra = getattr(bot, "extra_events", {}) or {}
        for event_name in ("on_member_join", "on_member_remove"):
            listeners = list(extra.get(event_name) or [])
            kept = []
            removed = 0
            for fn in listeners:
                module = _safe_str(getattr(fn, "__module__", ""))
                name = _safe_str(getattr(fn, "__name__", ""))
                if "welcome_member_events_guard" in module:
                    removed += 1
                    continue
                if "member_lifecycle_verify_runtime_hardening" in module and name in {"_patched_join_listener", "_patched_leave_listener"}:
                    removed += 1
                    continue
                kept.append(fn)
            extra[event_name] = kept
            if removed:
                _log(f"removed old/conflicting member lifecycle listeners event={event_name} count={removed}")
    except Exception as exc:
        _log(f"old listener removal failed: {type(exc).__name__}: {exc}")


def _install_listener(fn: Any, event_name: str) -> None:
    if bot is None:
        return
    existing = list((getattr(bot, "extra_events", {}) or {}).get(event_name) or [])
    if any(getattr(x, "__name__", "") == getattr(fn, "__name__", "") and getattr(x, "__module__", "") == __name__ for x in existing):
        return
    bot.add_listener(fn, event_name)


async def _member_logs_command(
    interaction: discord.Interaction,
    public_welcome: Optional[discord.TextChannel] = None,
    join_leave_log: Optional[discord.TextChannel] = None,
    staff_audit_log: Optional[discord.TextChannel] = None,
) -> None:
    try:
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (getattr(perms, "administrator", False) or getattr(perms, "manage_guild", False) or getattr(perms, "manage_channels", False)):
            return await interaction.response.send_message("❌ You need **Manage Server** or **Manage Channels** to configure member logs.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

        guild = interaction.guild
        payload: dict[str, Any] = {}
        if public_welcome is not None:
            payload["public_welcome_channel_id"] = str(public_welcome.id)
            payload["welcome_channel_id"] = str(public_welcome.id)
        if join_leave_log is not None:
            for key in JOIN_LEAVE_KEYS:
                payload[key] = str(join_leave_log.id)
        if staff_audit_log is not None:
            payload["staff_join_audit_channel_id"] = str(staff_audit_log.id)
            payload["member_audit_log_channel_id"] = str(staff_audit_log.id)
            payload["modlog_channel_id"] = str(staff_audit_log.id)
        if payload:
            from stoney_verify.commands_ext.public_setup_config_writer import upsert_guild_config
            from stoney_verify.guild_config import invalidate_guild_config
            payload.update({"__config_write_mode": "setup_builder", "__config_write_source": "/dank member-logs", "configured_by_id": str(interaction.user.id), "configured_by_name": str(interaction.user), "configured_at": discord.utils.utcnow().isoformat()})
            await upsert_guild_config(int(guild.id), payload)
            invalidate_guild_config(int(guild.id))

        cfg = await _load_config(int(guild.id))
        public_channel = _resolve_channel(guild, cfg, PUBLIC_WELCOME_KEYS)
        join_leave_channel = _resolve_channel(guild, cfg, JOIN_LEAVE_KEYS)
        staff_channel = _resolve_channel(guild, cfg, STAFF_AUDIT_KEYS)
        same_route = _same_channel(public_channel, join_leave_channel)

        embed = discord.Embed(
            title="👋 Member Lifecycle Routing",
            description=(
                "Welcome content and join/leave event logs are separated.\n\n"
                "Automatic join/leave cards are **never** posted to the welcome channel."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Public welcome", value=public_channel.mention if public_channel else "`Not set`", inline=False)
        embed.add_field(name="Join / leave log", value=(join_leave_channel.mention if join_leave_channel and not same_route else "`Not set or same as welcome — event cards disabled`"), inline=False)
        embed.add_field(name="Staff audit / invite source", value=staff_channel.mention if staff_channel else "`Not set — detailed audit will not be posted publicly`", inline=False)
        embed.add_field(name="Leak guard", value="Join/leave cards skip the welcome channel even if an old alias points there.", inline=False)
        invite_status = "Can read invites ✅" if _bot_can_read_invites(guild) else "Missing Manage Server permission ⚠️ invite source may stay unknown"
        embed.add_field(name="Invite tracking", value=invite_status, inline=False)
        if payload:
            embed.add_field(name="Saved", value="Updated member lifecycle routes.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        try:
            await interaction.response.send_message(f"❌ Could not update member logs: `{type(exc).__name__}: {exc}`", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass


def _install_command() -> bool:
    if dank_group is None:
        _log("dank_group unavailable; /dank member-logs not installed")
        return False
    try:
        existing = {getattr(command, "name", "") for command in getattr(dank_group, "commands", []) or []}
        if "member-logs" in existing:
            return True
        decorated = app_commands.describe(
            public_welcome="Static welcome/rules channel. Join cards are never posted here.",
            join_leave_log="Channel for simple join/leave event cards.",
            staff_audit_log="Staff-only channel for detailed join audit and invite source.",
        )(_member_logs_command)
        try:
            decorated = app_commands.default_permissions(manage_guild=True)(decorated)
        except Exception:
            pass
        dank_group.command(name="member-logs", description="Configure member lifecycle routes without leaking joins into welcome.")(decorated)
        return True
    except Exception as exc:
        _log(f"command install failed: {type(exc).__name__}: {exc}")
        return False


def install() -> bool:
    global _INSTALLED
    _install_command()
    if _INSTALLED:
        return True
    if bot is None:
        _log("bot unavailable; listeners not installed")
        return False
    try:
        _remove_old_welcome_listeners()
        _install_listener(_join_listener, "on_member_join")
        _install_listener(_leave_listener, "on_member_remove")
        _install_listener(_ready_listener, "on_ready")
        _install_listener(_guild_join_listener, "on_guild_join")
        _install_listener(_invite_create_listener, "on_invite_create")
        _install_listener(_invite_delete_listener, "on_invite_delete")
        _INSTALLED = True
        _log("active; join/leave logs never post to welcome channel")
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = ["install"]
