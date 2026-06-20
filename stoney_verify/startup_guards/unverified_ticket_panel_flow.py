from __future__ import annotations

"""Auto-route verification-needed users from the public ticket panel.

The public Create Ticket button should feel like TicketTool for verified users,
but members who still need verification should not be asked to describe a
support issue. They should get a verification ticket immediately.

Production rule:
A member is verification-needed when they are not staff/admin, do not have the
configured approved/verified role, and do not have the configured full access
member/resident role. The configured waiting/pending role is helpful, but it is
not required because fresh joins can press the panel before Discord/the bot
finishes assigning that role.

Hardening:
- Never retire existing/open ticket rows before a replacement channel is created.
- Create tickets with only the critical overwrites first. Staff overwrites are
  applied after creation so one high/blocked staff role cannot break the whole
  ticket.
- Treat bot + owner access as critical. Staff overwrite failures are warnings,
  not a reason to leave the member with a blank ticket.
- Before routing a verification-needed member, repair the configured VC verify
  channel into a locked waiting-room state. If Discord will not let Dank Shield lock
  it, the flow stops instead of running half-configured.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import discord

_PATCHED = False
_TICKET_NUM_RE = re.compile(r"^(?:ticket|closed)-(\d+)$", re.I)


def _log(message: str) -> None:
    try:
        print(f"🎟️ unverified_ticket_panel_flow {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ unverified_ticket_panel_flow {message}")
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _member_has_role(member: discord.Member, role_id: int) -> bool:
    if role_id <= 0:
        return False
    try:
        return any(int(getattr(role, "id", 0) or 0) == int(role_id) for role in (member.roles or []))
    except Exception:
        return False


def _member_role_ids(member: discord.Member) -> set[int]:
    try:
        return {int(getattr(role, "id", 0) or 0) for role in (member.roles or []) if int(getattr(role, "id", 0) or 0) > 0}
    except Exception:
        return set()


def _config_role_id(cfg: Any, *names: str) -> int:
    for name in names:
        value = _safe_int(getattr(cfg, name, 0), 0)
        if value > 0:
            return value
    return 0


def _config_channel_id(cfg: Any, *names: str) -> int:
    for name in names:
        value = _safe_int(getattr(cfg, name, 0), 0)
        if value > 0:
            return value
    return 0


async def _get_guild_config_safe(guild_id: int) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        return await get_guild_config(int(guild_id), refresh=True)
    except Exception as e:
        _warn(f"config lookup failed guild={guild_id}: {e!r}")
        return None


def _is_staff(member: discord.Member, cfg: Any) -> bool:
    try:
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild or member.guild_permissions.manage_channels:
            return True
    except Exception:
        pass

    staff_like_role_ids = {
        _config_role_id(cfg, "staff_role_id"),
        _config_role_id(cfg, "ticket_staff_role_id"),
        _config_role_id(cfg, "support_role_id"),
        _config_role_id(cfg, "vc_staff_role_id"),
        _config_role_id(cfg, "server_control_role_id"),
        _config_role_id(cfg, "control_role_id"),
        _config_role_id(cfg, "perm_role_id"),
        _config_role_id(cfg, "bot_manager_role_id"),
    }
    staff_like_role_ids.discard(0)
    return bool(staff_like_role_ids and _member_role_ids(member).intersection(staff_like_role_ids))


async def _is_unverified_only_member(member: discord.Member) -> bool:
    if getattr(member, "bot", False):
        return False

    cfg = await _get_guild_config_safe(member.guild.id)
    if cfg is None or _is_staff(member, cfg):
        return False

    waiting_role_id = _config_role_id(cfg, "unverified_role_id")
    approved_role_id = _config_role_id(cfg, "verified_role_id")
    member_role_id = _config_role_id(cfg, "resident_role_id", "member_role_id")

    has_waiting = waiting_role_id > 0 and _member_has_role(member, waiting_role_id)
    has_approved = approved_role_id > 0 and _member_has_role(member, approved_role_id)
    has_member = member_role_id > 0 and _member_has_role(member, member_role_id)

    if has_approved or has_member:
        return False
    if has_waiting:
        _log(f"verification-needed user matched by waiting/pending role guild={member.guild.id} user={member.id}")
        return True
    if waiting_role_id > 0 or approved_role_id > 0 or member_role_id > 0:
        _log(
            "verification-needed user matched by missing approved/member roles "
            f"guild={member.guild.id} user={member.id} roles={sorted(_member_role_ids(member))} "
            f"configured_waiting={waiting_role_id} configured_approved={approved_role_id} configured_member={member_role_id}"
        )
        return True
    return False


def _interaction_member(interaction: discord.Interaction) -> Optional[discord.Member]:
    try:
        if isinstance(interaction.user, discord.Member):
            return interaction.user
        guild = interaction.guild
        if guild is None:
            return None
        return guild.get_member(int(interaction.user.id))
    except Exception:
        return None


def _site_url() -> str:
    try:
        from stoney_verify import globals as g

        return str(getattr(g, "VERIFY_SITE_URL", "") or getattr(g, "SITE_URL", "") or "").strip()
    except Exception:
        return ""


def _token_ttl_minutes() -> int:
    try:
        from stoney_verify import globals as g

        return int(getattr(g, "TOKEN_TTL_MINUTES", 20) or 20)
    except Exception:
        return 20


def _allow_user_regen() -> bool:
    try:
        from stoney_verify import globals as g

        return bool(getattr(g, "ALLOW_USER_VERIFYLINK", False))
    except Exception:
        return False


async def _reply(interaction: discord.Interaction, content: str) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _existing_open_ticket_channel(guild: discord.Guild, owner_id: int) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.tickets_new.service import find_open_ticket_for_owner

        row = await find_open_ticket_for_owner(guild_id=int(guild.id), owner_id=int(owner_id), category=None)
        if not isinstance(row, dict):
            return None
        channel_id = _safe_int(row.get("discord_thread_id") or row.get("channel_id"), 0)
        if channel_id <= 0:
            return None
        ch = guild.get_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            return ch
        fetched = await guild.fetch_channel(channel_id)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


async def _force_retire_open_ticket_rows(
    *,
    guild_id: int,
    owner_id: int,
    channel_id: Optional[int] = None,
    reason: str,
) -> bool:
    payload = {
        "status": "deleted",
        "closed_at": _now_iso(),
        "deleted_at": _now_iso(),
        "closed_reason": reason,
        "close_reason": reason,
        "delete_reason": reason,
        "updated_at": _now_iso(),
    }

    def _sync() -> bool:
        try:
            from stoney_verify.globals import get_supabase

            sb = get_supabase()
            changed = False
            if channel_id and int(channel_id) > 0:
                for col in ("channel_id", "discord_thread_id"):
                    try:
                        sb.table("tickets").update(payload).eq("guild_id", str(guild_id)).eq("user_id", str(owner_id)).eq(col, str(channel_id)).execute()
                        changed = True
                    except Exception as e:
                        _warn(f"direct stale ticket row retire update failed col={col}: {e!r}")
            else:
                try:
                    sb.table("tickets").update(payload).eq("guild_id", str(guild_id)).eq("user_id", str(owner_id)).in_("status", ["open", "claimed"]).execute()
                    changed = True
                except Exception as e:
                    _warn(f"direct stale owner ticket row retire fallback failed: {e!r}")
            return changed
        except Exception as e:
            _warn(f"force retire stale ticket rows failed guild={guild_id} owner={owner_id}: {e!r}")
            return False

    ok = await asyncio.to_thread(_sync)
    if ok:
        _warn(f"force-retired inaccessible/open ticket row(s) guild={guild_id} owner={owner_id} channel={channel_id or 0}")
    return ok


def _ticket_bot_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        use_application_commands=True,
        manage_messages=True,
        manage_channels=True,
    )


def _ticket_owner_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        use_application_commands=True,
    )


def _ticket_staff_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
        use_application_commands=True,
        manage_messages=True,
    )


def _ticket_everyone_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=False)


def _configured_staff_role_ids(cfg: Any) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()

    def add(value: Any) -> None:
        rid = _safe_int(value, 0)
        if rid <= 0 or rid in seen:
            return
        seen.add(rid)
        ids.append(rid)

    for name in (
        "staff_role_id",
        "ticket_staff_role_id",
        "support_role_id",
        "server_control_role_id",
        "control_role_id",
        "perm_role_id",
        "bot_manager_role_id",
    ):
        try:
            add(getattr(cfg, name, 0))
        except Exception:
            pass
    try:
        from stoney_verify import globals as g

        for name in ("STAFF_ROLE_ID", "SUPPORT_ROLE_ID", "MOD_ROLE_ID", "ADMIN_ROLE_ID"):
            add(getattr(g, name, 0))
    except Exception:
        pass
    return ids


def _configured_open_ticket_category(guild: discord.Guild, cfg: Any) -> Optional[discord.CategoryChannel]:
    ids: list[int] = []
    for names in (
        ("open_ticket_category_id",),
        ("ticket_open_category_id",),
        ("active_ticket_category_id",),
        ("ticket_category_id",),
        ("tickets_category_id",),
        ("open_category_id",),
    ):
        cid = _config_channel_id(cfg, *names)
        if cid > 0:
            ids.append(cid)
    try:
        from stoney_verify import globals as g

        for name in ("TICKET_CATEGORY_ID", "OPEN_TICKET_CATEGORY_ID", "ACTIVE_TICKET_CATEGORY_ID"):
            cid = _safe_int(getattr(g, name, 0), 0)
            if cid > 0:
                ids.append(cid)
    except Exception:
        pass

    seen: set[int] = set()
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        channel = guild.get_channel(int(cid))
        if isinstance(channel, discord.CategoryChannel):
            return channel
    return None


def _configured_vc_verify_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.abc.GuildChannel]:
    cid = _config_channel_id(cfg, "vc_verify_channel_id", "vc_verify_vc_id")
    if cid <= 0:
        return None
    try:
        return guild.get_channel(int(cid))
    except Exception:
        return None


def _is_voice_like(channel: Any) -> bool:
    voice_types: list[type] = [discord.VoiceChannel]
    stage_type = getattr(discord, "StageChannel", None)
    if stage_type is not None:
        voice_types.append(stage_type)
    return isinstance(channel, tuple(voice_types))


def _vc_lock_everyone_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=False, connect=False)


def _vc_lock_waiting_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, connect=False, speak=False)


def _vc_lock_bot_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True, manage_channels=True)


def _vc_lock_staff_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True)


def _bot_member_for_guild(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        if isinstance(guild.me, discord.Member):
            return guild.me
    except Exception:
        pass
    try:
        state = getattr(guild, "_state", None)
        user = getattr(state, "user", None)
        user_id = int(getattr(user, "id", 0) or 0)
        member = guild.get_member(user_id)
        return member if isinstance(member, discord.Member) else None
    except Exception:
        return None


def _can_manage_channel(guild: discord.Guild, channel: discord.abc.GuildChannel) -> tuple[bool, str]:
    bot_member = _bot_member_for_guild(guild)
    if bot_member is None:
        return False, "bot member is not available in guild cache"
    try:
        if bot_member.guild_permissions.administrator or bot_member.guild_permissions.manage_channels:
            return True, "server Manage Channels"
    except Exception:
        pass
    try:
        if channel.permissions_for(bot_member).manage_channels:
            return True, f"Manage Channels in #{getattr(channel, 'name', 'channel')}"
    except Exception:
        pass
    return False, "missing Manage Channels on the configured VC verification channel"


async def _ensure_configured_vc_verify_locked(guild: discord.Guild, cfg: Any) -> tuple[bool, str]:
    """Repair the configured VC verify channel into a locked waiting-room state.

    VC verification is supposed to be private/controlled: normal members should
    not freely connect. Staff grants temporary per-member access from the VC
    request panel. If the channel is not configured, VC verify is treated as
    disabled and this does not block web verification tickets.
    """

    channel = _configured_vc_verify_channel(guild, cfg)
    if channel is None:
        return True, "VC verify disabled/not configured"
    if not _is_voice_like(channel):
        return False, "configured VC verify channel is not a voice/stage channel"

    can_manage, why = _can_manage_channel(guild, channel)
    if not can_manage:
        return False, why

    critical_ok = True
    critical_targets: list[tuple[Any, discord.PermissionOverwrite, str]] = [
        (guild.default_role, _vc_lock_everyone_overwrite(), "everyone"),
    ]

    bot_member = _bot_member_for_guild(guild)
    if bot_member is not None:
        critical_targets.append((bot_member, _vc_lock_bot_overwrite(), "bot"))

    waiting_role_id = _config_role_id(cfg, "unverified_role_id")
    waiting_role = guild.get_role(waiting_role_id) if waiting_role_id > 0 else None
    if waiting_role is not None:
        critical_targets.append((waiting_role, _vc_lock_waiting_overwrite(), f"waiting:{waiting_role_id}"))

    for target, overwrite, label in critical_targets:
        try:
            await channel.set_permissions(target, overwrite=overwrite, reason="Dank Shield setup safety: lock VC verification waiting room")
        except Exception as e:
            critical_ok = False
            _warn(f"VC lock critical overwrite failed channel={getattr(channel, 'id', 0)} target={label}: {e!r}")

    for rid in _configured_staff_role_ids(cfg):
        role = guild.get_role(int(rid))
        if role is None:
            continue
        try:
            await channel.set_permissions(role, overwrite=_vc_lock_staff_overwrite(), reason="Dank Shield setup safety: staff VC verification access")
        except Exception as e:
            _warn(f"VC lock staff overwrite skipped channel={getattr(channel, 'id', 0)} role={rid}: {e!r}")

    if critical_ok:
        _log(f"VC verify channel locked guild={guild.id} channel={getattr(channel, 'id', 0)}")
        return True, "VC verify channel locked"
    return False, "could not apply critical VC verification lock overwrites"


def _critical_creation_overwrites(guild: discord.Guild, member: discord.Member) -> Dict[Any, discord.PermissionOverwrite]:
    """Only include overwrites that must exist for the ticket to work.

    Staff roles are intentionally not included here. Some customer servers put
    staff/admin roles above the bot, and Discord can reject channel creation
    when those role overwrites are included in the create payload. Staff access
    is added after creation best-effort.
    """

    overwrites: Dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: _ticket_everyone_overwrite(),
        member: _ticket_owner_overwrite(),
    }
    bot_member = _bot_member_for_guild(guild)
    if bot_member is not None:
        overwrites[bot_member] = _ticket_bot_overwrite()
    return overwrites


def _can_create_ticket_channel(guild: discord.Guild, category: Optional[discord.CategoryChannel]) -> tuple[bool, str]:
    bot_member = _bot_member_for_guild(guild)
    if bot_member is None:
        return False, "bot member is not available in guild cache"

    try:
        if bot_member.guild_permissions.administrator:
            return True, "administrator"
        if bot_member.guild_permissions.manage_channels:
            return True, "server Manage Channels"
    except Exception:
        pass

    try:
        if category is not None and category.permissions_for(bot_member).manage_channels:
            return True, f"category Manage Channels in #{category.name}"
    except Exception:
        pass

    return (
        False,
        "missing Manage Channels. Give the bot Manage Channels server-wide, "
        "or allow Manage Channels on the configured active ticket category.",
    )


async def _apply_staff_ticket_access(channel: discord.TextChannel, cfg: Any) -> None:
    for rid in _configured_staff_role_ids(cfg):
        role = channel.guild.get_role(int(rid))
        if role is None:
            continue
        try:
            await channel.set_permissions(
                role,
                overwrite=_ticket_staff_overwrite(),
                reason="Grant staff access to verification ticket",
            )
        except Exception as e:
            _warn(f"staff ticket access skipped channel={channel.id} role={rid}: {e!r}")


async def _ensure_ticket_channel_access(channel: discord.TextChannel, member: discord.Member) -> bool:
    guild = channel.guild
    cfg = await _get_guild_config_safe(guild.id)
    critical_ok = True

    critical_targets: list[tuple[Any, discord.PermissionOverwrite, str]] = [
        (guild.default_role, _ticket_everyone_overwrite(), "everyone"),
        (member, _ticket_owner_overwrite(), "owner"),
    ]
    bot_member = _bot_member_for_guild(guild)
    if bot_member is not None:
        critical_targets.append((bot_member, _ticket_bot_overwrite(), "bot"))

    for target, overwrite, label in critical_targets:
        try:
            await channel.set_permissions(
                target,
                overwrite=overwrite,
                reason="Repair verification ticket access before posting verification panel",
            )
        except Exception as e:
            critical_ok = False
            _warn(f"critical ticket access repair failed channel={channel.id} target={label}: {e!r}")

    await _apply_staff_ticket_access(channel, cfg)

    if critical_ok:
        _log(f"ticket access repaired channel={channel.id} owner={member.id}")
        await asyncio.sleep(0.35)
    return critical_ok


async def _post_verify_ui(channel: discord.TextChannel, member: discord.Member) -> bool:
    try:
        if not await _ensure_ticket_channel_access(channel, member):
            return False
        from stoney_verify.verify_ui import post_or_replace_verify_ui

        await post_or_replace_verify_ui(
            channel,
            requester_id=int(member.id),
            reason="auto-routed from public ticket panel",
            site_url=_site_url(),
            ttl_minutes=_token_ttl_minutes(),
            allow_regen=_allow_user_regen(),
        )
        return True
    except Exception as e:
        _warn(f"verify UI post failed channel={getattr(channel, 'id', None)} user={getattr(member, 'id', None)}: {e!r}")
        return False


def _channel_ticket_number(channel: discord.TextChannel) -> int:
    try:
        m = _TICKET_NUM_RE.match(str(channel.name or "").strip().lower())
        if m:
            return int(m.group(1))
    except Exception:
        pass
    try:
        topic = channel.topic or ""
        m = re.search(r"(?:^|;)ticket_number=(\d+)(?:;|$)", topic)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 0


def _scan_max_ticket_number(guild: discord.Guild) -> int:
    max_num = 0
    for channel in list(getattr(guild, "text_channels", []) or []):
        max_num = max(max_num, _channel_ticket_number(channel))
    return max_num


async def _db_max_ticket_number(guild_id: int) -> int:
    def _sync() -> int:
        try:
            from stoney_verify.globals import get_supabase

            sb = get_supabase()
            resp = sb.table("tickets").select("ticket_number").eq("guild_id", str(guild_id)).order("ticket_number", desc=True).limit(1).execute()
            rows = getattr(resp, "data", None) or []
            if rows and isinstance(rows[0], dict):
                return _safe_int(rows[0].get("ticket_number"), 0)
        except Exception as e:
            _warn(f"direct ticket number DB lookup failed guild={guild_id}: {e!r}")
        return 0

    return await asyncio.to_thread(_sync)


async def _reserve_direct_ticket_number(guild: discord.Guild) -> int:
    return max(_scan_max_ticket_number(guild), await _db_max_ticket_number(int(guild.id))) + 1


async def _insert_direct_ticket_row(channel: discord.TextChannel, member: discord.Member, *, ticket_number: int, category: str) -> None:
    payload = {
        "guild_id": str(channel.guild.id),
        "user_id": str(member.id),
        "owner_id": str(member.id),
        "requester_id": str(member.id),
        "username": str(member),
        "owner_name": str(member),
        "requester_name": str(member),
        "title": "Verification",
        "category": category,
        "status": "open",
        "priority": "medium",
        "initial_message": "Verification assistance requested from the public ticket panel.",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "discord_thread_id": str(channel.id),
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "is_ghost": False,
        "source": "discord_button_verification_auto_route_direct",
        "matched_category_slug": category,
        "matched_category_name": "Verification",
        "matched_intake_type": "verification",
        "matched_category_reason": "Verification-needed member pressed public ticket panel.",
        "matched_category_score": 100,
        "ticket_number": int(ticket_number),
    }

    def _sync() -> None:
        try:
            from stoney_verify.globals import get_supabase

            sb = get_supabase()
            sb.table("tickets").insert(payload).execute()
            _log(f"direct ticket row inserted channel={channel.id} owner={member.id} ticket_number={ticket_number}")
        except Exception as e:
            _warn(f"direct ticket row insert failed channel={channel.id} owner={member.id}: {e!r}")
        try:
            from stoney_verify.globals import get_supabase

            sb = get_supabase()
            sb.table("ticket_counters").upsert(
                {
                    "guild_id": str(channel.guild.id),
                    "last_ticket_number": int(ticket_number),
                    "updated_at": _now_iso(),
                },
                on_conflict="guild_id",
            ).execute()
        except Exception:
            pass

    await asyncio.to_thread(_sync)


async def _send_direct_ticket_intro(channel: discord.TextChannel, member: discord.Member) -> None:
    try:
        embed = discord.Embed(
            title="✅ Verification Ticket Opened",
            description=(
                f"Hi {member.mention}. This private ticket was opened because your account still needs verification.\n\n"
                "Use the verification panel below. Staff can help here if anything fails."
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Dank Shield • verification ticket")
        await channel.send(content=member.mention, embed=embed, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    except Exception as e:
        _warn(f"direct ticket intro send failed channel={getattr(channel, 'id', None)}: {e!r}")


async def _create_direct_verification_ticket(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    cfg = await _get_guild_config_safe(guild.id)
    ticket_number = await _reserve_direct_ticket_number(guild)
    channel_name = f"ticket-{int(ticket_number):04d}"
    topic = f"owner_id={member.id};category=verification_issue;ghost=false;ticket_number={int(ticket_number)}"
    category = _configured_open_ticket_category(guild, cfg)
    can_create, create_reason = _can_create_ticket_channel(guild, category)
    if not can_create:
        raise PermissionError(create_reason)

    overwrites = _critical_creation_overwrites(guild, member)
    channel = await guild.create_text_channel(
        channel_name,
        category=category,
        overwrites=overwrites,
        topic=topic,
        reason="Verification-needed member pressed public ticket panel",
    )
    _log(
        "direct access-safe verification ticket channel created "
        f"guild={guild.id} owner={member.id} channel={channel.id} "
        f"category={getattr(category, 'id', 0) or 0} permission_source={create_reason}"
    )

    await _apply_staff_ticket_access(channel, cfg)

    await _force_retire_open_ticket_rows(
        guild_id=int(guild.id),
        owner_id=int(member.id),
        channel_id=None,
        reason="Verification auto-route replaced stale ticket rows after creating an access-safe ticket",
    )
    await _insert_direct_ticket_row(channel, member, ticket_number=ticket_number, category="verification_issue")
    await _send_direct_ticket_intro(channel, member)
    return channel


async def _open_fresh_verification_ticket(interaction: discord.Interaction, guild: discord.Guild, member: discord.Member) -> bool:
    try:
        channel = await _create_direct_verification_ticket(guild, member)
    except PermissionError as e:
        _warn(f"verification ticket auto-route missing permissions guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)}: {e!r}")
        await _reply(
            interaction,
            "❌ I could not open your verification ticket because Dank Shield is missing **Manage Channels** for ticket creation. "
            "Staff should run `/dank setup` → **Health Check** and fix the bot/category permissions.",
        )
        return True
    except discord.Forbidden as e:
        _warn(f"verification ticket auto-route missing permissions guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)}: {e!r}")
        await _reply(
            interaction,
            "❌ I could not open your verification ticket because Discord denied Dank Shield permission to create the private ticket. "
            "Staff should run `/dank setup` → **Health Check** and fix the bot/category permissions.",
        )
        return True
    except Exception as e:
        _warn(f"verification ticket auto-route failed guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)}: {e!r}")
        await _reply(interaction, "❌ I could not open your verification ticket. Staff should run `/dank setup` → Health Check.")
        return True

    if channel is None:
        await _reply(interaction, "❌ I tried to open your verification ticket, but ticket creation did not return a channel.")
        return True

    posted = await _post_verify_ui(channel, member)
    if posted:
        await _reply(interaction, f"✅ Opened your verification ticket: {channel.mention}\nUse the verification buttons inside that ticket.")
    else:
        await _reply(interaction, f"⚠️ Opened your verification ticket: {channel.mention}\nBut I could not post the verification panel because Dank Shield lacks access inside that channel.")
    return True


async def _handle_unverified_panel_click(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    member = _interaction_member(interaction)
    if guild is None or member is None:
        return False
    if not await _is_unverified_only_member(member):
        return False
    await _defer(interaction)

    cfg = await _get_guild_config_safe(guild.id)
    vc_locked, vc_message = await _ensure_configured_vc_verify_locked(guild, cfg)
    if not vc_locked:
        await _reply(
            interaction,
            "❌ Verification is not safe to run yet because the configured VC verification channel is not locked correctly.\n"
            f"Reason: **{vc_message}**\n\n"
            "Staff should run `/dank setup` → **Auto-Build Missing Items** or fix the bot's Manage Channels permission on the VC verification channel.",
        )
        return True

    existing = await _existing_open_ticket_channel(guild, int(member.id))
    if existing is not None:
        access_ok = await _ensure_ticket_channel_access(existing, member)
        if access_ok and await _post_verify_ui(existing, member):
            await _reply(interaction, f"✅ You already have a verification ticket open: {existing.mention}\nI refreshed the verification panel there.")
            return True

        # Only retire the broken row that points at the inaccessible channel.
        # Do not wipe all owner rows until a replacement channel is successfully created.
        await _force_retire_open_ticket_rows(
            guild_id=int(guild.id),
            owner_id=int(member.id),
            channel_id=int(existing.id),
            reason="Verification auto-route found an inaccessible stale ticket; replacing it with an access-safe ticket",
        )
        return await _open_fresh_verification_ticket(interaction, guild, member)

    return await _open_fresh_verification_ticket(interaction, guild, member)


def _button_looks_like_create_ticket(item: Any) -> bool:
    try:
        if not isinstance(item, discord.ui.Button):
            return False
        label = str(getattr(item, "label", "") or "").lower()
        custom_id = str(getattr(item, "custom_id", "") or "").lower()
        text = f"{label} {custom_id}"
        return "ticket" in text and ("create" in text or "open" in text or "panel" in text)
    except Exception:
        return False


def patch_ticket_panel_view() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.tickets_new import panel

        view_cls = getattr(panel, "TicketPanelView", None)
        if view_cls is None:
            _warn("TicketPanelView not found")
            return False
        original_init = getattr(view_cls, "__init__", None)
        if not callable(original_init) or getattr(original_init, "_unverified_flow_wrapped", False):
            _PATCHED = True
            return True

        def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            try:
                for item in list(getattr(self, "children", []) or []):
                    if not _button_looks_like_create_ticket(item):
                        continue
                    original_callback = getattr(item, "callback", None)
                    if not callable(original_callback) or getattr(original_callback, "_unverified_flow_wrapped", False):
                        continue

                    async def _wrapped_callback(interaction: discord.Interaction, *, _original=original_callback) -> Any:
                        handled = await _handle_unverified_panel_click(interaction)
                        if handled:
                            return None
                        return await _original(interaction)

                    try:
                        setattr(_wrapped_callback, "_unverified_flow_wrapped", True)
                    except Exception:
                        pass
                    item.callback = _wrapped_callback
            except Exception as e:
                _warn(f"failed wiring TicketPanelView button callback: {e!r}")

        try:
            setattr(_patched_init, "_unverified_flow_wrapped", True)
        except Exception:
            pass
        setattr(view_cls, "__init__", _patched_init)
        _PATCHED = True
        _log("patched TicketPanelView so verification-needed members open verification tickets directly")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


patch_ticket_panel_view()


__all__ = ["patch_ticket_panel_view"]
