from __future__ import annotations

"""Force VC staff actions to use the server's saved VC voice channel.

Bug fixed:
- Staff panel can show the correct per-guild VC channel.
- But Accept/Reissue can still use the old global/env VC id.
- That causes: "VC verification channel not found" or "channel is not a voice channel".

This guard patches the VC accept/unlock path so it resolves the voice channel from:
1) active session/cache if valid
2) this guild's saved setup config
3) old env fallback only if it is actually a voice/stage channel in this guild

No channels, roles, tickets, or messages are created/deleted here.
"""

from datetime import datetime, timezone
from typing import Any, Optional, Tuple

import discord

_PATCHED = False


def _log(msg: str) -> None:
    try:
        print(f"✅ vc_per_guild_access_fix: {msg}")
    except Exception:
        pass


def _warn(msg: str) -> None:
    try:
        print(f"⚠️ vc_per_guild_access_fix: {msg}")
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


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _voice_types() -> tuple[type, ...]:
    items: list[type] = [discord.VoiceChannel]
    stage = getattr(discord, "StageChannel", None)
    if stage is not None:
        items.append(stage)
    return tuple(items)


def _is_voice(channel: Any) -> bool:
    return isinstance(channel, _voice_types())


def _cfg_get(cfg: Any, *names: str) -> int:
    for name in names:
        try:
            if hasattr(cfg, "get"):
                value = _safe_int(cfg.get(name), 0)  # type: ignore[attr-defined]
                if value > 0:
                    return value
        except Exception:
            pass
        try:
            value = _safe_int(getattr(cfg, name, 0), 0)
            if value > 0:
                return value
        except Exception:
            pass
    return 0


def _cached_cfg(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import guild_config_cache_snapshot

        return guild_config_cache_snapshot(int(guild.id))
    except Exception:
        return None


async def _fresh_cfg(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        return await get_guild_config(int(guild.id), refresh=True)
    except Exception:
        return None


def _vc_id_from_cfg(cfg: Any) -> int:
    return _cfg_get(
        cfg,
        "vc_verify_channel_id",
        "vc_verify_vc_id",
        "voice_verify_channel_id",
        "voice_verification_channel_id",
    )


def _queue_id_from_cfg(cfg: Any) -> int:
    return _cfg_get(
        cfg,
        "vc_verify_queue_channel_id",
        "vc_queue_channel_id",
        "vc_verify_requests_channel_id",
        "vc_requests_channel_id",
    )


def _guild_voice_cached(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    if channel_id <= 0:
        return None
    try:
        channel = guild.get_channel(int(channel_id))
        return channel if _is_voice(channel) else None
    except Exception:
        return None


async def _guild_voice(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    channel = _guild_voice_cached(guild, channel_id)
    if channel is not None:
        return channel
    try:
        fetched = await guild.fetch_channel(int(channel_id))
        return fetched if _is_voice(fetched) else None
    except Exception:
        return None


def _module_row(module: Any, key: str) -> dict[str, Any]:
    try:
        rows = getattr(module, "VC_REQUESTS", None)
        if isinstance(rows, dict):
            row = rows.get(str(key)) or {}
            return dict(row) if isinstance(row, dict) else {}
    except Exception:
        pass
    return {}


def _patch_rows(key: str, patch: dict[str, Any], *modules: Any) -> None:
    for module in modules:
        try:
            rows = getattr(module, "VC_REQUESTS", None)
            if not isinstance(rows, dict):
                continue
            row = rows.get(str(key)) or {}
            if not isinstance(row, dict):
                row = {}
            row.update(dict(patch or {}))
            rows[str(key)] = row
        except Exception:
            pass


def _session_row(vc_verify: Any, key: str) -> dict[str, Any]:
    try:
        sessions = getattr(vc_verify, "vc_sessions", None)
        if sessions is not None and hasattr(sessions, "get_session"):
            row = sessions.get_session(str(key)) or {}
            return dict(row) if isinstance(row, dict) else {}
    except Exception:
        pass
    return {}


def _stored_info(module: Any, key: str) -> dict[str, Any]:
    try:
        fn = getattr(module, "sb_get_token_info", None)
        if callable(fn):
            row = fn(str(key)) or {}
            return dict(row) if isinstance(row, dict) else {}
    except Exception:
        pass
    return {}


def _row_int(row: dict[str, Any], *names: str) -> int:
    for name in names:
        value = _safe_int(row.get(name), 0)
        if value > 0:
            return value
    return 0


async def _resolve_saved_vc(
    guild: discord.Guild,
    key: str,
    vc_verify: Any,
    vc_flow: Any,
) -> Optional[discord.abc.GuildChannel]:
    for row in (
        _session_row(vc_verify, key),
        _module_row(vc_verify, key),
        _module_row(vc_flow, key),
    ):
        channel = await _guild_voice(guild, _row_int(row, "vc_channel_id"))
        if channel is not None:
            return channel

    cfg = await _fresh_cfg(guild)
    channel = await _guild_voice(guild, _vc_id_from_cfg(cfg))
    if channel is not None:
        _patch_rows(
            key,
            {"vc_channel_id": int(channel.id), "vc_channel_fixed_at": _utc_iso()},
            vc_verify,
            vc_flow,
        )
        return channel

    # Legacy fallback is allowed only when it is actually voice in this same guild.
    for module in (vc_verify, vc_flow):
        for name in ("VC_VERIFY_CHANNEL_ID", "VC_VERIFY_VC_ID"):
            channel = await _guild_voice(guild, _safe_int(getattr(module, name, 0), 0))
            if channel is not None:
                return channel

    return None


def _resolve_saved_vc_cached(
    guild: discord.Guild,
    vc_verify: Any,
    vc_flow: Any,
) -> Optional[discord.abc.GuildChannel]:
    cfg = _cached_cfg(guild)
    channel = _guild_voice_cached(guild, _vc_id_from_cfg(cfg))
    if channel is not None:
        return channel

    for module in (vc_verify, vc_flow):
        for name in ("VC_VERIFY_CHANNEL_ID", "VC_VERIFY_VC_ID"):
            channel = _guild_voice_cached(guild, _safe_int(getattr(module, name, 0), 0))
            if channel is not None:
                return channel

    return None


def _can_manage(me: Optional[discord.Member], channel: discord.abc.GuildChannel) -> bool:
    try:
        if not isinstance(me, discord.Member):
            return False
        perms = channel.permissions_for(me)
        return bool(perms.administrator or (perms.view_channel and perms.manage_channels))
    except Exception:
        return False


async def _grant(
    guild: discord.Guild,
    member: discord.Member,
    key: str,
    vc_verify: Any,
    vc_flow: Any,
) -> Tuple[bool, str]:
    vc = await _resolve_saved_vc(guild, str(key), vc_verify, vc_flow)
    if not _is_voice(vc):
        return False, "VC verification channel is not saved as a real voice channel. Run `/stoney setup` → Health Check."

    if not _can_manage(guild.me, vc):
        return False, f"Stoney needs **View Channel** and **Manage Channels** on {getattr(vc, 'mention', '#voice')}."

    try:
        ow = vc.overwrites_for(member)  # type: ignore[union-attr]
        ow.view_channel = True
        ow.connect = True
        ow.speak = True
        ow.use_voice_activation = True
        await vc.set_permissions(member, overwrite=ow, reason=f"VC verify access key={key}")  # type: ignore[union-attr]
    except discord.Forbidden:
        return False, "Discord denied the VC permission edit. Move Stoney's bot role higher and rerun the one-press VC fix."
    except Exception as e:
        return False, f"Failed to edit VC permissions: {type(e).__name__}: {str(e)[:180]}"

    _patch_rows(
        str(key),
        {
            "vc_channel_id": int(getattr(vc, "id", 0) or 0),
            "status": "READY",
            "last_grant_at": _utc_iso(),
        },
        vc_verify,
        vc_flow,
    )
    return True, "Temporary VC access granted."


async def _revoke(
    guild: discord.Guild,
    member: discord.Member,
    key: str,
    reason: str,
    vc_verify: Any,
    vc_flow: Any,
) -> None:
    vc = await _resolve_saved_vc(guild, str(key), vc_verify, vc_flow)
    if not _is_voice(vc) or not _can_manage(guild.me, vc):
        return
    try:
        await vc.set_permissions(member, overwrite=None, reason=f"VC verify revoke ({reason}) key={key}")  # type: ignore[union-attr]
    except Exception:
        pass


def _owner_id(key: str, vc_verify: Any, vc_flow: Any, fallback: discord.Member) -> int:
    for row in (
        _session_row(vc_verify, key),
        _module_row(vc_verify, key),
        _module_row(vc_flow, key),
        _stored_info(vc_verify, key),
        _stored_info(vc_flow, key),
    ):
        value = _row_int(row, "owner_id", "requester_id", "user_id")
        if value > 0:
            return value
    return int(fallback.id)


def _ticket_channel_id(key: str, vc_verify: Any, vc_flow: Any) -> int:
    for row in (
        _session_row(vc_verify, key),
        _module_row(vc_verify, key),
        _module_row(vc_flow, key),
        _stored_info(vc_verify, key),
        _stored_info(vc_flow, key),
    ):
        value = _row_int(row, "ticket_channel_id", "channel_id")
        if value > 0:
            return value
    return 0


def _assigned_staff_id(key: str, vc_verify: Any, vc_flow: Any) -> int:
    for row in (
        _session_row(vc_verify, key),
        _module_row(vc_verify, key),
        _module_row(vc_flow, key),
    ):
        meta = row.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        for source in (meta, row):
            value = _row_int(source, "assigned_staff_id", "accepted_staff_id", "accepted_by")
            if value > 0:
                return value
    return 0


async def _unlock(
    *,
    guild: discord.Guild,
    token: str,
    owner: discord.Member,
    staff_member: discord.Member,
    vc_verify: Any,
    vc_flow: Any,
) -> Tuple[bool, str]:
    key = str(token or "").strip()
    if not key:
        return False, "Missing VC session key."

    if _owner_id(key, vc_verify, vc_flow, owner) != int(owner.id):
        return False, "Owner does not match this VC session."

    assigned = _assigned_staff_id(key, vc_verify, vc_flow)
    if assigned > 0 and assigned != int(staff_member.id):
        return False, "Only the assigned staff member can unlock this VC session."

    ok_owner, msg_owner = await _grant(guild, owner, key, vc_verify, vc_flow)
    if not ok_owner:
        return False, msg_owner

    ok_staff, msg_staff = await _grant(guild, staff_member, key, vc_verify, vc_flow)
    if not ok_staff:
        await _revoke(guild, owner, key, "staff grant failed rollback", vc_verify, vc_flow)
        return False, msg_staff

    vc = await _resolve_saved_vc(guild, key, vc_verify, vc_flow)
    ticket_id = _ticket_channel_id(key, vc_verify, vc_flow)

    _patch_rows(
        key,
        {
            "status": "READY",
            "vc_channel_id": int(getattr(vc, "id", 0) or 0),
            "unlocked_at": _utc_iso(),
        },
        vc_verify,
        vc_flow,
    )

    try:
        sessions = getattr(vc_verify, "vc_sessions", None)
        if sessions is not None:
            cfg = await _fresh_cfg(guild)
            if hasattr(sessions, "ensure_session") and ticket_id > 0 and vc is not None:
                sessions.ensure_session(
                    token=key,
                    guild_id=int(guild.id),
                    ticket_channel_id=int(ticket_id),
                    requester_id=int(owner.id),
                    owner_id=int(owner.id),
                    vc_channel_id=int(getattr(vc, "id", 0) or 0),
                    queue_channel_id=int(_queue_id_from_cfg(cfg) or 0),
                    access_minutes=30,
                    meta={
                        "ticket_required": True,
                        "vc_locked_by_default": True,
                        "assigned_staff_id": int(staff_member.id),
                        "per_guild_vc_fix": True,
                    },
                )
            if hasattr(sessions, "mark_unlocked"):
                sessions.mark_unlocked(
                    token=key,
                    by_staff_id=int(staff_member.id),
                    guard_reason="per-guild VC resolved",
                )
    except Exception as e:
        _warn(f"session update skipped: {e!r}")

    return True, "Owner and assigned staff now have private VC access."


def patch_per_guild_vc_access() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify import vc_verify
    except Exception as e:
        _warn(f"vc_verify import failed: {e!r}")
        return False

    try:
        from stoney_verify.commands_ext import vc_flow
    except Exception:
        vc_flow = None  # type: ignore[assignment]

    async def resolve_session_vc_channel(
        guild: discord.Guild,
        *,
        token: str,
        session_row: Optional[dict[str, Any]] = None,
    ) -> Optional[discord.abc.GuildChannel]:
        if session_row:
            channel = await _guild_voice(guild, _row_int(session_row, "vc_channel_id"))
            if channel is not None:
                return channel
        return await _resolve_saved_vc(guild, str(token), vc_verify, vc_flow)

    async def resolve_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
        return await _resolve_saved_vc(guild, "", vc_verify, vc_flow)

    def get_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
        return _resolve_saved_vc_cached(guild, vc_verify, vc_flow)

    async def grant_access(
        guild: discord.Guild,
        member: discord.Member,
        token: str,
    ) -> Tuple[bool, str]:
        return await _grant(guild, member, str(token), vc_verify, vc_flow)

    async def revoke_access(
        guild: discord.Guild,
        member: discord.Member,
        token: str,
        reason: str = "manual",
    ) -> None:
        await _revoke(guild, member, str(token), reason, vc_verify, vc_flow)

    async def unlock_session_participants(
        *,
        guild: discord.Guild,
        token: str,
        owner: discord.Member,
        staff_member: discord.Member,
    ) -> Tuple[bool, str]:
        return await _unlock(
            guild=guild,
            token=str(token),
            owner=owner,
            staff_member=staff_member,
            vc_verify=vc_verify,
            vc_flow=vc_flow,
        )

    for module in (vc_verify, vc_flow):
        if module is None:
            continue
        try:
            setattr(module, "_resolve_session_vc_channel", resolve_session_vc_channel)
            setattr(module, "_resolve_vc_channel", resolve_vc_channel)
            setattr(module, "_get_vc_channel", get_vc_channel)
            setattr(module, "_vc_grant_access", grant_access)
            setattr(module, "_vc_revoke_access", revoke_access)
            setattr(module, "vc_unlock_session_participants", unlock_session_participants)
        except Exception:
            pass

    try:
        if vc_flow is not None:
            setattr(vc_flow, "_vc_verify_mod", vc_verify)
    except Exception:
        pass

    try:
        from stoney_verify.verification_new import voice_verify

        setattr(voice_verify, "_get_vc_channel", get_vc_channel)
        setattr(voice_verify, "_vc_grant_access", grant_access)
        setattr(voice_verify, "_vc_revoke_access", revoke_access)
        setattr(voice_verify, "vc_unlock_session_participants", unlock_session_participants)
    except Exception:
        pass

    _PATCHED = True
    _log("VC staff Accept now uses the server's saved voice channel")
    return True


patch_per_guild_vc_access()


__all__ = ["patch_per_guild_vc_access"]
