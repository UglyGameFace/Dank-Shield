from __future__ import annotations

"""Public runtime config isolation for verification.

Production rule: verification must never borrow deployment-level channel/role
IDs from another server. This guard keeps the currently-refactored runtime
paths on the current guild's saved setup config only and makes VC approval
return a visible result instead of silently doing nothing.
"""

import asyncio
import builtins
import sys
from typing import Any, Optional, Tuple

import discord

_PATCHED = False
_IMPORT_PATCHED = False
_ROLE_CONTEXT_LOCK = asyncio.Lock()
_DIRECT_APPROVE_LOCKS: dict[str, asyncio.Lock] = {}

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)
_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")


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


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


async def _run_sync(label: str, fn: Any, *args: Any, timeout: float = 8.0, **kwargs: Any) -> Any:
    if not callable(fn):
        return None

    def _call() -> Any:
        return fn(*args, **kwargs)

    try:
        return await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout)
    except asyncio.TimeoutError:
        _warn(f"{label} timed out after {timeout:.1f}s")
        raise


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
        return False, "VC verification channel is not configured as a real voice channel for this server. Run `/dank setup` → Health Check."
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

    # Keep the in-memory status aligned so Approve (VC) is allowed immediately
    # after Accept without depending on a separate legacy status update.
    for module in (vc_verify, vc_flow):
        try:
            rows = getattr(module, "VC_REQUESTS", None)
            if isinstance(rows, dict):
                row = dict(rows.get(str(token)) or {})
                row.update({
                    "status": "READY",
                    "vc_channel_id": int(getattr(await _resolve_vc(guild, token, vc_verify, vc_flow), "id", 0) or 0),
                    "accepted_by": int(staff_member.id),
                    "accepted_staff_id": int(staff_member.id),
                })
                rows[str(token)] = row
        except Exception:
            pass

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
        # Legacy private-server custom member roles must not leak into public installs.
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


def _interaction_action_token(interaction: discord.Interaction) -> tuple[str, str]:
    try:
        cid = _safe_str(getattr(getattr(interaction, "data", None), "get", lambda *_: "")("custom_id"))
    except Exception:
        try:
            cid = _safe_str((getattr(interaction, "data", None) or {}).get("custom_id"))
        except Exception:
            cid = ""
    if not cid:
        return "", ""

    try:
        from stoney_verify.commands_ext.common import parse_custom_id

        parsed = parse_custom_id(cid)
        if isinstance(parsed, tuple) and len(parsed) >= 2:
            return _safe_str(parsed[0]), _safe_str(parsed[1])
        if isinstance(parsed, dict):
            return _safe_str(parsed.get("action")), _safe_str(parsed.get("token"))
    except Exception:
        pass

    if ":" in cid:
        action, token = cid.split(":", 1)
        return _safe_str(action), _safe_str(token)
    return cid, ""


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass


async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    content = _safe_str(content)[:1900] or "Done."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except Exception:
        try:
            await interaction.followup.send(content, ephemeral=True)
        except Exception:
            pass


def _lock_for_direct_approve(guild_id: int, token: str) -> asyncio.Lock:
    key = f"{int(guild_id)}:{str(token)}"
    lock = _DIRECT_APPROVE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DIRECT_APPROVE_LOCKS[key] = lock
    return lock


async def _resolve_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    if int(user_id or 0) <= 0:
        return None
    try:
        member = guild.get_member(int(user_id))
        if isinstance(member, discord.Member):
            return member
    except Exception:
        pass
    try:
        member = await guild.fetch_member(int(user_id))
        return member if isinstance(member, discord.Member) else None
    except Exception:
        return None


def _role_manage_error(guild: discord.Guild, roles: list[discord.Role]) -> str:
    me = guild.me
    if not isinstance(me, discord.Member):
        return "Bot member is missing in this server."
    try:
        if not (me.guild_permissions.administrator or me.guild_permissions.manage_roles):
            return "Stoney needs **Manage Roles** to approve verification."
    except Exception:
        return "Could not read Stoney's role permissions."

    bad: list[str] = []
    for role in roles:
        try:
            if role >= me.top_role:
                bad.append(f"{role.name} (`{role.id}`)")
        except Exception:
            continue
    if bad:
        return "Move Stoney's bot role above these roles: " + ", ".join(bad)
    return ""


async def _direct_vc_approve(interaction: discord.Interaction, token: str) -> bool:
    guild = interaction.guild
    staff = interaction.user
    channel = interaction.channel

    if not isinstance(guild, discord.Guild) or not isinstance(staff, discord.Member):
        return False
    if not isinstance(channel, discord.TextChannel):
        return False

    token = _safe_str(token)
    if not token:
        return False

    await _defer(interaction)
    lock = _lock_for_direct_approve(guild.id, token)

    async with lock:
        try:
            from stoney_verify.store import sb_get_token_info, sb_mark_decision, sb_set_used
        except Exception:
            await _send_ephemeral(interaction, "❌ Verification store is unavailable. Restart and try again.")
            return True

        try:
            token_info = await _run_sync("vc approve token lookup", sb_get_token_info, token, timeout=8.0)
        except asyncio.TimeoutError:
            await _send_ephemeral(interaction, "❌ Supabase took too long while reading this VC token. Try again once; the bot did not freeze.")
            return True
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ Could not read this VC token: `{type(e).__name__}`")
            return True

        if not isinstance(token_info, dict) or not token_info:
            await _send_ephemeral(interaction, "❌ Invalid or expired VC token.")
            return True

        token_guild = _safe_str(token_info.get("guild_id"))
        if token_guild and token_guild != str(guild.id):
            await _send_ephemeral(interaction, "❌ This VC token belongs to a different server.")
            return True

        token_channel_id = _safe_int(token_info.get("channel_id"), 0)
        if token_channel_id > 0 and token_channel_id != int(channel.id):
            await _send_ephemeral(interaction, "❌ This VC token belongs to a different ticket channel.")
            return True

        owner_id = _safe_int(token_info.get("requester_id") or token_info.get("user_id"), 0)
        owner = await _resolve_member(guild, owner_id)
        if not isinstance(owner, discord.Member):
            await _send_ephemeral(interaction, "❌ Could not resolve the ticket owner in this server.")
            return True

        role_ids = await _role_values(guild)
        verified_role = guild.get_role(_safe_int(role_ids.get("VERIFIED_ROLE_ID"), 0))
        resident_role = guild.get_role(_safe_int(role_ids.get("RESIDENT_ROLE_ID"), 0))
        unverified_role = guild.get_role(_safe_int(role_ids.get("UNVERIFIED_ROLE_ID"), 0))

        grant_roles = [r for r in (verified_role, resident_role) if isinstance(r, discord.Role)]
        if not grant_roles:
            await _send_ephemeral(
                interaction,
                "❌ This server has no valid **Verified** or **Resident/Member** role saved in setup. Run `/dank setup` and save this server's roles again.",
            )
            return True

        manage_error = _role_manage_error(guild, grant_roles + ([unverified_role] if isinstance(unverified_role, discord.Role) else []))
        if manage_error:
            await _send_ephemeral(interaction, f"❌ {manage_error}")
            return True

        try:
            to_add = [role for role in grant_roles if role not in owner.roles]
            if to_add:
                await owner.add_roles(*to_add, reason=f"Stoney VC verification approved by {staff} ({staff.id})")
            removed_unverified = False
            if isinstance(unverified_role, discord.Role) and unverified_role in owner.roles:
                await owner.remove_roles(unverified_role, reason=f"Stoney VC verification cleanup by {staff} ({staff.id})")
                removed_unverified = True
        except discord.Forbidden:
            await _send_ephemeral(interaction, "❌ Discord denied role assignment. Move Stoney's bot role higher and make sure it has Manage Roles.")
            return True
        except Exception as e:
            await _send_ephemeral(interaction, f"❌ Role assignment failed: `{type(e).__name__}: {str(e)[:160]}`")
            return True

        try:
            await _run_sync("vc approve mark decision", sb_mark_decision, token, "APPROVED (VC)", int(staff.id), approved_user_id=int(owner.id), timeout=8.0)
        except Exception as e:
            _warn(f"mark decision failed for vc approval token={token}: {e!r}")
        try:
            await _run_sync("vc approve mark used", sb_set_used, token, True, timeout=8.0)
        except Exception as e:
            _warn(f"set used failed for vc approval token={token}: {e!r}")

        try:
            from stoney_verify.commands_ext.common import VC_REQUESTS

            req = dict(VC_REQUESTS.get(str(token)) or {})
            req.update({
                "status": "APPROVED",
                "approved_by": int(staff.id),
                "approved_user_id": int(owner.id),
            })
            VC_REQUESTS[str(token)] = req
        except Exception:
            pass

        role_names = ", ".join(role.name for role in grant_roles if isinstance(role, discord.Role))
        try:
            await channel.send(
                f"✅ **VC verification approved** by {staff.mention}.\n"
                f"{owner.mention} has been verified. Roles granted/confirmed: **{role_names}**."
                + ("\nRemoved **Unverified**." if removed_unverified else "")
            )
        except Exception:
            pass

        try:
            if interaction.message:
                await interaction.message.edit(view=None)
        except Exception:
            pass

        try:
            from stoney_verify.transcripts import auto_close_after_decision

            await asyncio.wait_for(
                auto_close_after_decision(channel, closer=staff, decision="APPROVED (VC)"),
                timeout=10.0,
            )
        except Exception as e:
            _warn(f"auto close after direct vc approve skipped: {e!r}")

        await _send_ephemeral(interaction, f"✅ Approved {owner.display_name}. Roles granted/confirmed: {role_names}.")
        _log(f"direct VC approve completed guild={guild.id} user={owner.id} staff={staff.id}")
        return True


def _patch_interaction_handler() -> None:
    try:
        module = sys.modules.get("stoney_verify.interaction_handlers")
        if module is None:
            return
        original = getattr(module, "handle_component_interaction", None)
        if not callable(original) or getattr(original, "_public_no_env_vc_approve_safe", False):
            return

        async def handle_component_interaction_safe(interaction: discord.Interaction):
            action, token = _interaction_action_token(interaction)
            if action == "vc_approve" and token:
                handled = await _direct_vc_approve(interaction, token)
                if handled:
                    return None
            return await original(interaction)

        try:
            setattr(handle_component_interaction_safe, "_public_no_env_vc_approve_safe", True)
        except Exception:
            pass
        setattr(module, "handle_component_interaction", handle_component_interaction_safe)
        _log("VC approve interaction now defers immediately and has a direct no-env approval path")
    except Exception as e:
        _warn(f"interaction handler safety patch failed: {e!r}")


def _install_import_hook() -> None:
    global _IMPORT_PATCHED
    if _IMPORT_PATCHED:
        return

    def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
        try:
            if name == "stoney_verify.interaction_handlers" or name.endswith("interaction_handlers"):
                _patch_interaction_handler()
            else:
                _patch_interaction_handler()
        except Exception:
            pass
        return module

    builtins.__import__ = _safe_import
    _IMPORT_PATCHED = True


def patch_public_no_env_runtime_config() -> bool:
    global _PATCHED
    if _PATCHED:
        _patch_interaction_handler()
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
                async with _ROLE_CONTEXT_LOCK:
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

    _install_import_hook()
    _patch_interaction_handler()
    _PATCHED = True
    _log("public verification runtime now uses per-guild setup only; VC approve cannot silently fail")
    return True


patch_public_no_env_runtime_config()


__all__ = ["patch_public_no_env_runtime_config"]
