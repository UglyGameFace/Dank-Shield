from __future__ import annotations

"""Member-role update reconciliation service.

The Discord event listener should call into this service when roles change. This
module owns verification-role reconciliation decisions:
- remove Unverified when a safe access role is granted
- restore Unverified when a human member becomes roleless
- start the verification wait timer after a role-heal restore
"""

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import discord

from .. import role_truth
from .join_verification_service import verification_role_ids_for_guild


@dataclass
class RoleUpdateReconcileResult:
    removed_unverified: bool = False
    restored_unverified: bool = False
    started_timer: bool = False
    suppress_further_processing: bool = False


def _log(message: str) -> None:
    try:
        print(f"🧩 role_update_reconcile {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ role_update_reconcile {message}")
    except Exception:
        pass


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


async def reconcile_member_role_update(
    before: discord.Member,
    after: discord.Member,
    *,
    now_utc: Callable[[], object],
    auto_uv_removal_ts: dict[tuple[int, int], object],
    resolve_unverified_chat_channel: Callable[[discord.Guild], Awaitable[Optional[discord.TextChannel]]],
    start_join_grace_timer: Callable[..., Awaitable[bool]],
) -> RoleUpdateReconcileResult:
    result = RoleUpdateReconcileResult()

    try:
        guild = after.guild
        gid = int(guild.id)
        member_id = int(after.id)

        before_roles = set([int(getattr(role, "id", 0)) for role in (before.roles or [])])
        after_roles = set([int(getattr(role, "id", 0)) for role in (after.roles or [])])
        if before_roles == after_roles:
            return result

        added_ids = after_roles - before_roles
        removed_ids = before_roles - after_roles
        role_ids = await verification_role_ids_for_guild(guild)

        uv_id = int(role_ids.get("unverified") or 0)
        safe_ids = {
            int(role_ids.get("verified") or 0),
            int(role_ids.get("resident") or 0),
            int(role_ids.get("staff") or 0),
            int(role_ids.get("stoner") or 0),
            int(role_ids.get("drunken") or 0),
        }
        safe_ids.discard(0)

        # If we just auto-removed Unverified, suppress the immediately echoed
        # member_update so the bot does not re-process its own role cleanup.
        try:
            key = (gid, member_id)
            if uv_id and not added_ids and removed_ids == {uv_id}:
                ts = auto_uv_removal_ts.get(key)
                current = now_utc()
                delta = (current - ts).total_seconds() if ts is not None else 999999
                if ts and delta <= 15:
                    result.suppress_further_processing = True
                    return result
        except Exception:
            pass

        # If a configured safe access role is added, remove Unverified.
        try:
            if uv_id and bool(safe_ids & added_ids):
                uv_role = guild.get_role(uv_id)
                if uv_role and role_truth.member_has_role_id(after, uv_id):
                    await after.remove_roles(
                        uv_role,
                        reason="Auto-remove Unverified when safe access role is granted",
                    )
                    result.removed_unverified = True
                    auto_uv_removal_ts[(gid, member_id)] = now_utc()
        except Exception as e:
            _warn(f"failed removing Unverified after safe role grant member={member_id}: {e!r}")

        # If a human member becomes roleless, restore configured Unverified.
        try:
            if getattr(after, "bot", False):
                return result
            if not uv_id:
                return result

            has_unverified = role_truth.member_has_role_id(after, uv_id)
            has_safe_role = any(role_truth.member_has_role_id(after, rid) for rid in safe_ids)
            non_default_roles = [role for role in (after.roles or []) if not role.is_default()]
            has_no_real_roles = len(non_default_roles) == 0

            if has_no_real_roles and not has_unverified and not has_safe_role:
                uv_role = guild.get_role(uv_id)
                if uv_role is not None:
                    await after.add_roles(
                        uv_role,
                        reason="Auto-restore Unverified after member became roleless",
                    )
                    result.restored_unverified = True
                    _log(f"restored Unverified to member {member_id} after all roles were removed")

                    try:
                        refreshed = guild.get_member(member_id) or await guild.fetch_member(member_id)
                    except Exception:
                        refreshed = after

                    if isinstance(refreshed, discord.Member) and role_truth.member_is_pending_verification(refreshed):
                        fallback_channel = await resolve_unverified_chat_channel(guild)
                        started = await start_join_grace_timer(
                            refreshed,
                            source_channel=fallback_channel,
                        )
                        result.started_timer = bool(started)
                        _log(
                            f"join grace timer start guild={gid} member={member_id} "
                            f"started={started} fallback_channel={getattr(fallback_channel, 'id', None)}"
                        )
        except discord.Forbidden as e:
            _warn(f"missing permission to restore Unverified to {member_id}: {e!r}")
        except discord.HTTPException as e:
            _warn(f"HTTPException restoring Unverified to {member_id}: {e!r}")
        except Exception as e:
            _warn(f"roleless auto-heal block error for member {member_id}: {e!r}")

        return result
    except Exception as e:
        _warn(f"reconcile_member_role_update fatal error member={getattr(after, 'id', 'unknown')}: {e!r}")
        return result


__all__ = [
    "RoleUpdateReconcileResult",
    "reconcile_member_role_update",
]
