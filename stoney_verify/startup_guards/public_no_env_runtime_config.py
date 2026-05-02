from __future__ import annotations

"""Public runtime config isolation for verification.

Production rule: verification must never borrow deployment-level channel/role
IDs from another server. This guard keeps the currently-refactored runtime
paths on the current guild's saved setup config only.
"""

import asyncio
from typing import Any, Optional, Tuple

import discord

_PATCHED = False
_LOCK = asyncio.Lock()


def _log(msg: str) -> None:
    try:
        print(f"✅ public_no_env_runtime_config: {msg}")
    except Exception:
        pass


def _warn(msg: str) -> None:
    try:
        print(f"⚠️ public_no_env_runtime_config: {msg}")
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
        from stoney_verify import guild_config

        cache = getattr(guild_config, "_CONFIG_CACHE", None)
        if isinstance(cache, dict):
            row = cache.get(str(int(guild.id)))
            if isinstance(row, dict):
                return dict(row)
    except Exception:
        pass
    return None


async def _cfg(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import discover_runtime_guild_config

        return await discover_runtime_guild_config(guild)
    except Exception:
        try:
            from stoney_verify.guild_config import get_guild_config

            return await get_guild_config(int(guild.id), refresh=True)
        except Exception:
            return None


def _vc_id(cfg: Any) -> int:
    return _cfg_get(cfg, "vc_verify_channel_id", "vc_verify_vc_id", "voice_verify_channel_id", "voice_verification_channel_id")


def _queue_id(cfg: Any) -> int:
    return _cfg_get(cfg, "vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_verify_requests_channel_id", "vc_requests_channel_id")


def _voice_cached(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    if int(channel_id or 0) <= 0:
        return None
    try:
        ch = guild.get_channel(int(channel_id))
        return ch if _is_voice(ch) else None
    except Exception:
        return None


async def _voice(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    ch = _voice_cached(guild, channel_id)
    if ch is not None:
        return ch
    try:
        fetched = await guild.fetch_channel(int(channel_id))
        return fetched if _is_voice(fetched) else None
    except Exception:
        return None


def _name_score(channel: Any) -> int:
    try:
        name = str(getattr(channel, "name", "") or "").lower().replace("-", " ").replace("_", " ").replace("•", " ")
    except Exception:
        return 0
    if "voice verification" in name:
        return 100
    if "vc verification" in name:
        return 95
    if "voice verify" in name:
        return 90
    if "vc verify" in name:
        return 85
    if "verification" in name and ("voice" in name or "vc" in name):
        return 80
    if "verify" in name and ("voice" in name or "vc" in name):
        return 70
    return 0


def _discover_voice_cached(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    try:
        candidates = []
        for ch in list(getattr(guild, "channels", []) or []):
            if not _is_voice(ch):
                continue
            score = _name_score(ch)
            if score > 0:
                candidates.append((score, int(getattr(ch, "position", 0) or 0), ch))
        if not candidates:
            return None
        candidates.sort(key=lambda row: (-row[0], row[1]))
        return candidates[0][2]
    except Exception:
        return None


async def _discover_voice(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    cached = _discover_voice_cached(guild)
    if cached is not None:
        return cached
    try:
        fetched = await guild.fetch_channels()
    except Exception:
        fetched = []
    candidates = []
    for ch in list(fetched or []):
        if not _is_voice(ch):
            continue
        score = _name_score(ch)
        if score > 0:
            candidates.append((score, int(getattr(ch, "position", 0) or 0), ch))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[0], row[1]))
    return candidates[0][2]


async def _resolve_vc(guild: discord.Guild, token: str = "", *modules: Any) -> Optional[discord.abc.GuildChannel]:
    for module in modules:
        try:
            rows = getattr(module, "VC_REQUESTS", None)
            row = rows.get(str(token)) if isinstance(rows, dict) else None
            if isinstance(row, dict):
                ch = await _voice(guild, _cfg_get(row, "vc_channel_id"))
                if ch is not None:
                    return ch
        except Exception:
            pass

    config = await _cfg(guild)
    ch = await _voice(guild, _vc_id(config))
    if ch is not None:
        return ch

    # Current-guild-only discovery. No deployment ID fallback.
    return await _discover_voice(guild)


def _resolve_vc_cached(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
    ch = _voice_cached(guild, _vc_id(_cached_cfg(guild)))
    if ch is not None:
        return ch
    return _discover_voice_cached(guild)


def _can_manage(me: Optional[discord.Member], channel: discord.abc.GuildChannel) -> bool:
    try:
        if not isinstance(me, discord.Member):
            return False
        perms = channel.permissions_for(me)
        return bool(perms.administrator or (perms.view_channel and perms.manage_channels))
    except Exception:
        return False


async def _grant(guild: discord.Guild, member: discord.Member, token: str, *modules: Any) -> Tuple[bool, str]:
    vc = await _resolve_vc(guild, str(token), *modules)
    if not _is_voice(vc):
        return False, "VC verification channel is not configured as a real voice channel for this server. Run `/stoney setup` → Health Check."
    if not _can_manage(guild.me, vc):
        return False, f"Stoney needs View Channel + Manage Channels on {getattr(vc, 'mention', '#voice')}."
    try:
        ow = vc.overwrites_for(member)  # type: ignore[union-attr]
        ow.view_channel = True
        ow.connect = True
        ow.speak = True
        ow.use_voice_activation = True
        await vc.set_permissions(member, overwrite=ow, reason=f"VC verify access token={token}")  # type: ignore[union-attr]
        return True, "Temporary VC access granted."
    except discord.Forbidden:
        return False, "Discord denied the VC permission edit. Move Stoney's bot role higher and run the one-press setup fix."
    except Exception as e:
        return False, f"Failed to edit VC permissions: {type(e).__name__}: {str(e)[:180]}"


async def _revoke(guild: discord.Guild, member: discord.Member, token: str, reason: str, *modules: Any) -> None:
    vc = await _resolve_vc(guild, str(token), *modules)
    if not _is_voice(vc) or not _can_manage(guild.me, vc):
        return
    try:
        await vc.set_permissions(member, overwrite=None, reason=f"VC verify revoke ({reason}) token={token}")  # type: ignore[union-attr]
    except Exception:
        pass


def _row_int(row: dict[str, Any], *names: str) -> int:
    for name in names:
        value = _safe_int(row.get(name), 0)
        if value > 0:
            return value
    return 0


def _module_row(module: Any, token: str) -> dict[str, Any]:
    try:
        rows = getattr(module, "VC_REQUESTS", None)
        row = rows.get(str(token)) if isinstance(rows, dict) else None
        return dict(row) if isinstance(row, dict) else {}
    except Exception:
        return {}


def _session_row(vc_verify: Any, token: str) -> dict[str, Any]:
    try:
        sessions = getattr(vc_verify, "vc_sessions", None)
        if sessions is not None and hasattr(sessions, "get_session"):
            row = sessions.get_session(str(token)) or {}
            return dict(row) if isinstance(row, dict) else {}
    except Exception:
        pass
    return {}


def _owner_id(token: str, vc_verify: Any, vc_flow: Any, fallback: discord.Member) -> int:
    for row in (_session_row(vc_verify, token), _module_row(vc_verify, token), _module_row(vc_flow, token)):
        value = _row_int(row, "owner_id", "requester_id", "user_id")
        if value > 0:
            return value
    return int(fallback.id)


def _assigned_staff_id(token: str, vc_verify: Any, vc_flow: Any) -> int:
    for row in (_session_row(vc_verify, token), _module_row(vc_verify, token), _module_row(vc_flow, token)):
        meta = row.get("meta") if isinstance(row, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        for source in (row, meta):
            value = _row_int(source, "assigned_staff_id", "accepted_staff_id", "accepted_by")
            if value > 0:
                return value
    return 0


async def _unlock(*, guild: discord.Guild, token: str, owner: discord.Member, staff_member: discord.Member, vc_verify: Any, vc_flow: Any) -> Tuple[bool, str]:
    token = str(token or "").strip()
    if not token:
        return False, "Missing VC session key."
    if _owner_id(token, vc_verify, vc_flow, owner) != int(owner.id):
        return False, "Owner does not match this VC session."
    assigned = _assigned_staff_id(token, vc_verify, vc_flow)
    if assigned > 0 and assigned != int(staff_member.id):
        return False, "Only the assigned staff member can unlock this VC session."

    ok_owner, msg_owner = await _grant(guild, owner, token, vc_verify, vc_flow)
    if not ok_owner:
        return False, msg_owner
    ok_staff, msg_staff = await _grant(guild, staff_member, token, vc_verify, vc_flow)
    if not ok_staff:
        await _revoke(guild, owner, token, "staff grant rollback", vc_verify, vc_flow)
        return False, msg_staff
    return True, "Owner and assigned staff now have private VC access."


def _guild_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[discord.Guild]:
    direct = kwargs.get("guild")
    if isinstance(direct, discord.Guild):
        return direct
    for value in list(args) + list(kwargs.values()):
        if isinstance(value, discord.Guild):
            return value
        guild = getattr(value, "guild", None)
        if isinstance(guild, discord.Guild):
            return guild
    return None


def _role_id(guild: discord.Guild, cfg: Any, *keys: str) -> int:
    rid = _cfg_get(cfg, *keys)
    return int(rid) if rid > 0 and guild.get_role(int(rid)) is not None else 0


async def _role_values(guild: discord.Guild) -> dict[str, int]:
    config = await _cfg(guild)
    return {
        "UNVERIFIED_ROLE_ID": _role_id(guild, config, "unverified_role_id"),
        "VERIFIED_ROLE_ID": _role_id(guild, config, "verified_role_id"),
        "RESIDENT_ROLE_ID": _role_id(guild, config, "resident_role_id"),
        "STAFF_ROLE_ID": _role_id(guild, config, "staff_role_id"),
        "VC_STAFF_ROLE_ID": _role_id(guild, config, "vc_staff_role_id", "staff_role_id"),
        "STONER_ROLE_ID": 0,
        "DRUNKEN_ROLE_ID": 0,
    }


async def _push_roles(guild: discord.Guild, *modules: Any):
    values = await _role_values(guild)
    old: list[tuple[Any, str, Any]] = []
    for module in modules:
        if module is None:
            continue
        for name, value in values.items():
            try:
                old.append((module, name, getattr(module, name, None)))
                setattr(module, name, int(value or 0))
            except Exception:
                pass

    def restore() -> None:
        for module, name, value in reversed(old):
            try:
                setattr(module, name, value)
            except Exception:
                pass
    return restore


def patch_public_no_env_runtime_config() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify import vc_verify
    except Exception:
        vc_verify = None  # type: ignore[assignment]
    try:
        from stoney_verify.commands_ext import vc_flow
    except Exception:
        vc_flow = None  # type: ignore[assignment]
    try:
        from stoney_verify.verification_new import voice_verify
    except Exception:
        voice_verify = None  # type: ignore[assignment]
    try:
        from stoney_verify.verification_new import service as verify_service
    except Exception as e:
        _warn(f"verification service import failed: {e!r}")
        verify_service = None  # type: ignore[assignment]
    try:
        from stoney_verify import transcripts
    except Exception:
        transcripts = None  # type: ignore[assignment]

    async def resolve_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
        return await _resolve_vc(guild, "", vc_verify, vc_flow)

    async def resolve_session_vc_channel(guild: discord.Guild, *, token: str, session_row: Optional[dict[str, Any]] = None) -> Optional[discord.abc.GuildChannel]:
        if isinstance(session_row, dict):
            ch = await _voice(guild, _cfg_get(session_row, "vc_channel_id"))
            if ch is not None:
                return ch
        return await _resolve_vc(guild, str(token), vc_verify, vc_flow)

    def get_vc_channel(guild: discord.Guild) -> Optional[discord.abc.GuildChannel]:
        return _resolve_vc_cached(guild)

    async def grant_access(guild: discord.Guild, member: discord.Member, token: str) -> Tuple[bool, str]:
        return await _grant(guild, member, str(token), vc_verify, vc_flow)

    async def revoke_access(guild: discord.Guild, member: discord.Member, token: str, reason: str = "manual") -> None:
        await _revoke(guild, member, str(token), reason, vc_verify, vc_flow)

    async def unlock_session_participants(*, guild: discord.Guild, token: str, owner: discord.Member, staff_member: discord.Member) -> Tuple[bool, str]:
        return await _unlock(guild=guild, token=str(token), owner=owner, staff_member=staff_member, vc_verify=vc_verify, vc_flow=vc_flow)

    for module in (vc_verify, vc_flow, voice_verify):
        if module is None:
            continue
        for name, fn in (
            ("_resolve_vc_channel", resolve_vc_channel),
            ("_resolve_session_vc_channel", resolve_session_vc_channel),
            ("_get_vc_channel", get_vc_channel),
            ("_vc_grant_access", grant_access),
            ("_vc_revoke_access", revoke_access),
            ("vc_unlock_session_participants", unlock_session_participants),
        ):
            try:
                setattr(module, name, fn)
            except Exception:
                pass

    if verify_service is not None:
        def wrap(fn: Any):
            if not callable(fn) or getattr(fn, "_public_no_env_roles", False):
                return fn
            async def wrapped(*args: Any, **kwargs: Any):
                guild = _guild_from_call(args, kwargs)
                if not isinstance(guild, discord.Guild):
                    return await fn(*args, **kwargs)
                async with _LOCK:
                    restore = await _push_roles(guild, verify_service, transcripts, voice_verify)
                    try:
                        return await fn(*args, **kwargs)
                    finally:
                        restore()
            try:
                setattr(wrapped, "_public_no_env_roles", True)
            except Exception:
                pass
            return wrapped

        for fname in ("approve_verification", "approve_vc_verification"):
            try:
                original = getattr(verify_service, fname, None)
                wrapped = wrap(original)
                if wrapped is not original:
                    setattr(verify_service, fname, wrapped)
                    if voice_verify is not None and hasattr(voice_verify, fname):
                        setattr(voice_verify, fname, wrapped)
            except Exception:
                pass

    _PATCHED = True
    _log("public verification runtime now uses per-guild setup only; env IDs are ignored")
    return True


patch_public_no_env_runtime_config()


__all__ = ["patch_public_no_env_runtime_config"]
