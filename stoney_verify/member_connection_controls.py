from __future__ import annotations

"""One guild-scoped, evidence-only policy for member connection review.

Connection signals never authorize punishment. Channel protection changes only
permissions selected and confirmed by a human administrator.
"""

import asyncio
import uuid
from typing import Any, Callable, Mapping

import discord

from .guild_config import compare_and_swap_guild_config_key, get_guild_config

CONFIG_KEY = "member_connection_controls_v1"
MAX_PROTECTED_CHANNELS = 10
_DEFAULT = {"monitor": True, "review": True, "protected": {}}
_LOCKS: dict[int, asyncio.Lock] = {}


class ConnectionControlError(RuntimeError):
    pass


def _valid_id(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(str(value))
        return number if number > 0 else 0
    except (TypeError, ValueError):
        return 0


def parse_controls(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"monitor": True, "review": True, "protected": {}}
    if not isinstance(raw, Mapping):
        raise ConnectionControlError("Saved connection controls are invalid.")
    if type(raw.get("monitor", True)) is not bool or type(raw.get("review", True)) is not bool:
        raise ConnectionControlError("Saved connection switches are invalid.")
    protected = raw.get("protected", {})
    if not isinstance(protected, dict) or len(protected) > MAX_PROTECTED_CHANNELS:
        raise ConnectionControlError("Saved protected channels are invalid.")
    entries: dict[str, dict[str, Any]] = {}
    for channel_id, record in protected.items():
        if not _valid_id(channel_id) or not isinstance(record, dict):
            raise ConnectionControlError("A protected channel record is invalid.")
        role_id = _valid_id(record.get("role_id"))
        if not role_id or record.get("state") not in {"pending", "active"}:
            raise ConnectionControlError("A protected channel role or state is invalid.")
        original = record.get("original")
        if not isinstance(original, dict) or set(original) != {"everyone", "role"}:
            raise ConnectionControlError("A permission restoration record is invalid.")
        if any(type(v) not in {bool, type(None)} for v in original.values()):
            raise ConnectionControlError("A permission restoration value is invalid.")
        if not isinstance(record.get("token"), str) or not record["token"]:
            raise ConnectionControlError("A protected channel operation is incomplete.")
        entries[str(_valid_id(channel_id))] = {
            "role_id": str(role_id), "state": record["state"],
            "original": dict(original), "token": record["token"],
        }
    return {
        "monitor": raw.get("monitor", True),
        "review": raw.get("review", True),
        "protected": entries,
    }


async def load_controls(guild_id: int, *, refresh: bool = True) -> tuple[Any, dict[str, Any]]:
    cfg = await get_guild_config(guild_id, force_refresh=refresh)
    if str(cfg.get("source", "")).startswith("unavailable:"):
        raise ConnectionControlError("Server configuration storage is unavailable.")
    raw = cfg.get(CONFIG_KEY)
    return raw, parse_controls(raw)


async def auto_review_enabled(guild_id: int, action: str) -> bool:
    """A failed config read must not override an opted-out server."""
    if action not in {"monitor", "review"}:
        return False
    try:
        _, policy = await load_controls(guild_id, refresh=False)
        return policy[action] is True
    except Exception:
        return False


async def _cas(guild_id: int, old: Any, new: dict[str, Any]) -> bool:
    applied, _ = await compare_and_swap_guild_config_key(
        guild_id, CONFIG_KEY, expected=old, value=new,
        source="member_connection_controls",
    )
    return bool(applied)


async def _change(guild_id: int, update: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    for _ in range(4):
        previous, policy = await load_controls(guild_id)
        update(policy)
        if await _cas(guild_id, previous, policy):
            return policy
    raise ConnectionControlError("Settings changed elsewhere. Reopen and try again.")


async def toggle_review(guild_id: int, which: str) -> dict[str, Any]:
    if which not in {"monitor", "review"}:
        raise ValueError("Unknown connection control.")
    return await _change(guild_id, lambda policy: policy.__setitem__(which, not policy[which]))


def _permission_value(channel: discord.TextChannel, target: Any) -> bool | None:
    return channel.overwrites_for(target).view_channel


def _check_owner_and_bot(guild: discord.Guild, channel: discord.TextChannel, actor: discord.Member) -> None:
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        raise ConnectionControlError("Choose a text channel in this server.")
    if not isinstance(actor, discord.Member) or actor.guild.id != guild.id:
        raise ConnectionControlError("Only a current server administrator can change channel access.")
    me = guild.me
    if me is None:
        raise ConnectionControlError("Dank Shield's server member permissions are unavailable.")
    if not (actor.guild_permissions.administrator or actor.guild_permissions.manage_roles):
        raise ConnectionControlError("You need Manage Roles to change channel permissions.")
    if not (me.guild_permissions.administrator or me.guild_permissions.manage_roles):
        raise ConnectionControlError("Dank Shield needs Manage Roles to protect channels.")
    if not channel.permissions_for(me).manage_roles or not channel.permissions_for(actor).manage_roles:
        raise ConnectionControlError("You and Dank Shield need Manage Roles in this channel.")


def _check_grants(channel: discord.TextChannel, trusted: discord.Role) -> None:
    # Discord administrators bypass channel visibility overwrites entirely.
    # Role/member overwrite allows would bypass an @everyone deny.
    for principal, overwrite in channel.overwrites.items():
        if principal.id in {channel.guild.default_role.id, trusted.id}:
            continue
        if overwrite.view_channel is True:
            raise ConnectionControlError(
                "Another role or member has explicit channel access. Remove that allowance "
                "in Discord first; no permissions were changed."
            )


def _check_role(guild: discord.Guild, role: discord.Role) -> None:
    if not isinstance(role, discord.Role) or role.guild.id != guild.id:
        raise ConnectionControlError("Choose a trusted role from this server.")
    if role.id == guild.default_role.id or role.is_bot_managed() or role.managed:
        raise ConnectionControlError("Choose a normal trusted role, not @everyone or a managed role.")
    if role.permissions.administrator:
        raise ConnectionControlError("Do not use an Administrator role as the channel trust boundary.")


async def _set_view(channel: discord.TextChannel, target: Any, value: bool | None, reason: str) -> None:
    current = channel.overwrites_for(target)
    current.view_channel = value
    await channel.set_permissions(
        target, overwrite=current if not current.is_empty() else None, reason=reason,
    )


def channel_protection_status(guild: discord.Guild, channel_id: str, entry: Mapping[str, Any]) -> str:
    channel = guild.get_channel(_valid_id(channel_id))
    role = guild.get_role(_valid_id(entry.get("role_id")))
    if not isinstance(channel, discord.TextChannel) or role is None:
        return "missing channel or trusted role"
    if entry.get("state") != "active":
        return "pending; administrator review required"
    if _permission_value(channel, guild.default_role) is not False:
        return "drift: @everyone can access"
    if _permission_value(channel, role) is not True:
        return "drift: trusted role not allowed"
    try:
        _check_grants(channel, role)
    except ConnectionControlError:
        return "drift: another explicit access grant"
    return "active (administrators still bypass overwrites)"


async def protect_channel(
    guild: discord.Guild, actor: discord.Member, channel: discord.TextChannel, role: discord.Role,
) -> None:
    _check_owner_and_bot(guild, channel, actor)
    _check_role(guild, role)
    _check_grants(channel, role)
    gid, cid = guild.id, str(channel.id)
    lock = _LOCKS.setdefault(gid, asyncio.Lock())
    async with lock:
        original = {
            "everyone": _permission_value(channel, guild.default_role),
            "role": _permission_value(channel, role),
        }
        token = uuid.uuid4().hex

        def reserve(policy: dict[str, Any]) -> None:
            entries = policy["protected"]
            if cid in entries or len(entries) >= MAX_PROTECTED_CHANNELS:
                raise ConnectionControlError(
                    "This channel is already tracked or the ten-channel limit was reached."
                )
            entries[cid] = {
                "role_id": str(role.id), "state": "pending",
                "original": original, "token": token,
            }

        # Persist a recovery record BEFORE touching Discord permissions.
        await _change(gid, reserve)
        try:
            _check_owner_and_bot(guild, channel, actor)
            _check_grants(channel, role)
            if (_permission_value(channel, guild.default_role) != original["everyone"]
                    or _permission_value(channel, role) != original["role"]):
                raise ConnectionControlError("Channel permissions changed during confirmation.")
            reason = f"Dank Shield Connection Intelligence: protected by {actor.id}"
            await _set_view(channel, guild.default_role, False, reason)
            await _set_view(channel, role, True, reason)
            if channel_protection_status(guild, cid, {
                "role_id": str(role.id), "state": "active",
            }).startswith("drift:"):
                raise ConnectionControlError("Channel protection could not be verified.")

            def activate(policy: dict[str, Any]) -> None:
                item = policy["protected"].get(cid)
                if not item or item["token"] != token or item["state"] != "pending":
                    raise ConnectionControlError("The pending channel operation was changed.")
                item["state"] = "active"

            await _change(gid, activate)
        except Exception:
            # A pending durable record remains until a safe restoration is confirmed.
            try:
                await _restore_pair(guild, actor, channel, role, original)
                def clear_pending(policy: dict[str, Any]) -> None:
                    item = policy["protected"].get(cid)
                    if item and item["token"] == token and item["state"] == "pending":
                        del policy["protected"][cid]
                await _change(gid, clear_pending)
            except Exception:
                pass
            raise


async def _restore_pair(
    guild: discord.Guild, actor: discord.Member, channel: discord.TextChannel,
    role: discord.Role, original: Mapping[str, Any],
) -> None:
    _check_owner_and_bot(guild, channel, actor)
    current_e = _permission_value(channel, guild.default_role)
    current_r = _permission_value(channel, role)
    if current_e not in (original["everyone"], False) or current_r not in (original["role"], True):
        raise ConnectionControlError(
            "Permissions changed outside Dank Shield. Review manually before restoring."
        )
    reason = f"Dank Shield Connection Intelligence: restored by {actor.id}"
    if current_r != original["role"]:
        await _set_view(channel, role, original["role"], reason)
    if current_e != original["everyone"]:
        await _set_view(channel, guild.default_role, original["everyone"], reason)


async def remove_protection(guild: discord.Guild, actor: discord.Member, channel_id: str) -> None:
    gid, cid = guild.id, str(_valid_id(channel_id))
    lock = _LOCKS.setdefault(gid, asyncio.Lock())
    async with lock:
        _, policy = await load_controls(gid)
        item = policy["protected"].get(cid)
        if not item:
            raise ConnectionControlError("That channel is not in the protection list.")
        channel = guild.get_channel(int(cid))
        role = guild.get_role(int(item["role_id"]))
        if not isinstance(channel, discord.TextChannel) or role is None:
            raise ConnectionControlError(
                "Channel or role was deleted. Staff must resolve the saved record manually."
            )
        await _restore_pair(guild, actor, channel, role, item["original"])
        token = item["token"]
        def clear(policy: dict[str, Any]) -> None:
            entry = policy["protected"].get(cid)
            if not entry or entry["token"] != token:
                raise ConnectionControlError("Protection changed during restoration.")
            del policy["protected"][cid]
        await _change(gid, clear)

async def post_private_connection_alert(
    guild: discord.Guild, embed: discord.Embed, *, event_key: str,
) -> bool:
    """Never publish relationship leads to a member-visible or unknown modlog.

    Ordinary modlog delivery keeps its existing behavior; this additional
    privacy gate applies only to the sensitive Connection Intelligence alerts.
    """
    from .modlog import _get_modlog_channel_async, _post_modlog

    try:
        channel = await _get_modlog_channel_async(guild)
        if not isinstance(channel, discord.TextChannel):
            return False
        if channel.permissions_for(guild.default_role).view_channel:
            return False
        _, _ = await load_controls(guild.id, refresh=False)
        cfg = await get_guild_config(guild.id)
        if str(cfg.get("source", "")).startswith("unavailable:"):
            return False
        allowed_staff = {
            _valid_id(cfg.get(key))
            for key in ("staff_role_id", "vc_staff_role_id", "server_control_role_id")
        }
        for role in guild.roles:
            if role.id == guild.default_role.id:
                continue
            overwrite = channel.overwrites_for(role)
            if overwrite.view_channel is not True:
                continue
            p = role.permissions
            elevated = (p.administrator or p.manage_guild or p.manage_roles
                        or p.moderate_members or p.kick_members or p.ban_members)
            if role.id not in allowed_staff and not elevated:
                return False
        # Explicit member allowances cannot be classified safely from cached
        # member roles. Refuse those for sensitive account-relationship alerts.
        for principal, overwrite in channel.overwrites.items():
            if isinstance(principal, discord.Member) and overwrite.view_channel is True:
                return False
        await _post_modlog(
            guild, embed, event_key=event_key, dedupe_window_seconds=900,
        )
        return True
    except Exception:
        return False
