from __future__ import annotations

"""Use staff display names and avatar metadata for ticket claim actions.

Ticket ownership should read like a staff action, not a raw Discord ID or username
fallback.  This guard keeps the canonical ticket lifecycle functions but enriches
claim/transfer writes and activity events with display names plus avatar metadata
when the staff member object is available.
"""

from typing import Any, Dict, Optional

import discord

_IDENTITY_CACHE: Dict[str, Dict[str, Any]] = {}


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_staff_identity_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_staff_identity_guard: {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _actor_id(actor: Optional[discord.Member | discord.User]) -> Optional[str]:
    try:
        if actor is None:
            return None
        value = getattr(actor, "id", None)
        return str(value).strip() if value is not None else None
    except Exception:
        return None


def _display_name(actor: Optional[discord.Member | discord.User]) -> Optional[str]:
    if actor is None:
        return None
    for attr in ("display_name", "global_name", "name"):
        try:
            value = _safe_str(getattr(actor, attr, None))
            if value:
                return value[:160]
        except Exception:
            continue
    value = _safe_str(actor)
    return value[:160] if value else None


def _username(actor: Optional[discord.Member | discord.User]) -> Optional[str]:
    if actor is None:
        return None
    for attr in ("name", "global_name", "display_name"):
        try:
            value = _safe_str(getattr(actor, attr, None))
            if value:
                return value[:160]
        except Exception:
            continue
    value = _safe_str(actor)
    return value[:160] if value else None


def _avatar_url(actor: Optional[discord.Member | discord.User]) -> Optional[str]:
    if actor is None:
        return None
    for attr in ("display_avatar", "avatar"):
        try:
            asset = getattr(actor, attr, None)
            url = _safe_str(getattr(asset, "url", None))
            if url:
                return url[:500]
        except Exception:
            continue
    return None


def _identity(actor: Optional[discord.Member | discord.User], *, prefix: str = "actor") -> Dict[str, Any]:
    actor_id = _actor_id(actor)
    display = _display_name(actor)
    username = _username(actor)
    avatar = _avatar_url(actor)
    data: Dict[str, Any] = {}
    if actor_id:
        data[f"{prefix}_user_id"] = actor_id
    if display:
        data[f"{prefix}_display_name"] = display
    if username:
        data[f"{prefix}_username"] = username
    if avatar:
        data[f"{prefix}_avatar_url"] = avatar
    return data


def _cache_identity(actor: Optional[discord.Member | discord.User], *, prefix: str = "actor") -> None:
    actor_id = _actor_id(actor)
    if not actor_id:
        return
    existing = dict(_IDENTITY_CACHE.get(actor_id) or {})
    existing.update(_identity(actor, prefix=prefix))
    # Common aliases used by dashboard/activity readers.
    display = _display_name(actor)
    avatar = _avatar_url(actor)
    if display:
        existing.setdefault("actor_name", display)
        existing.setdefault("staff_display_name", display)
        existing.setdefault("claimed_by_display_name", display)
    if avatar:
        existing.setdefault("staff_avatar_url", avatar)
        existing.setdefault("claimed_by_avatar_url", avatar)
    _IDENTITY_CACHE[actor_id] = existing


async def _repair_claim_identity(repo_mod: Any, channel_id: int | str, staff_member: discord.Member | discord.User) -> None:
    display = _display_name(staff_member)
    if not display:
        return
    try:
        await repo_mod.safe_optional_update_by_channel_id(
            channel_id,
            {
                "claimed_by_name": display,
                "assigned_to_name": display,
            },
        )
    except Exception as exc:
        _warn(f"claim identity repair failed channel={channel_id}: {type(exc).__name__}: {exc!r}")


def _install_repository_identity_patch(repo_mod: Any) -> bool:
    if getattr(repo_mod, "_TICKET_STAFF_IDENTITY_REPO_PATCHED", False):
        return True

    original_assign = getattr(repo_mod, "assign_ticket", None)
    original_transfer = getattr(repo_mod, "transfer_ticket", None)
    if not callable(original_assign) or not callable(original_transfer):
        _warn("repository assign/transfer functions unavailable")
        return False

    async def assign_ticket(*, channel_id: int | str, staff_member: discord.Member | discord.User) -> bool:
        _cache_identity(staff_member)
        ok = bool(await original_assign(channel_id=channel_id, staff_member=staff_member))
        if ok:
            await _repair_claim_identity(repo_mod, channel_id, staff_member)
        return ok

    async def transfer_ticket(*, channel_id: int | str, to_staff_member: discord.Member | discord.User) -> bool:
        _cache_identity(to_staff_member, prefix="transfer_target")
        ok = bool(await original_transfer(channel_id=channel_id, to_staff_member=to_staff_member))
        if ok:
            await _repair_claim_identity(repo_mod, channel_id, to_staff_member)
        return ok

    try:
        setattr(assign_ticket, "_ticket_staff_identity_wrapped", True)
        setattr(transfer_ticket, "_ticket_staff_identity_wrapped", True)
        repo_mod.assign_ticket = assign_ticket
        repo_mod.transfer_ticket = transfer_ticket
        repo_mod._TICKET_STAFF_IDENTITY_REPO_PATCHED = True
        _log("patched repository claim/transfer staff display names")
        return True
    except Exception as exc:
        _warn(f"repository identity patch failed: {exc!r}")
        return False


def _install_service_identity_patch(service_mod: Any, repo_mod: Any) -> bool:
    if getattr(service_mod, "_TICKET_STAFF_IDENTITY_SERVICE_PATCHED", False):
        return True

    original_actor_name = getattr(service_mod, "_actor_name", None)
    original_assign = getattr(service_mod, "assign_ticket", None)
    original_transfer = getattr(service_mod, "transfer_ticket", None)
    original_log_claimed = getattr(service_mod, "log_ticket_claimed", None)
    original_log_transferred = getattr(service_mod, "log_ticket_transferred", None)

    if not callable(original_actor_name) or not callable(original_assign) or not callable(original_transfer):
        _warn("service actor/assign/transfer functions unavailable")
        return False

    def actor_name(actor: Optional[discord.Member | discord.User]) -> Optional[str]:
        display = _display_name(actor)
        if display:
            return display
        try:
            return original_actor_name(actor)
        except Exception:
            return None

    async def log_ticket_claimed(*args: Any, **kwargs: Any) -> bool:
        if callable(original_log_claimed):
            metadata = dict(kwargs.get("metadata") or {})
            actor_id = _safe_str(kwargs.get("actor_user_id"))
            if actor_id and actor_id in _IDENTITY_CACHE:
                metadata.update(_IDENTITY_CACHE[actor_id])
            actor_name_value = _safe_str(kwargs.get("actor_name"))
            if actor_name_value:
                metadata.setdefault("actor_display_name", actor_name_value)
                metadata.setdefault("claimed_by_display_name", actor_name_value)
            metadata.setdefault("claim_identity_source", "ticket_staff_identity_guard")
            kwargs["metadata"] = metadata
            return bool(await original_log_claimed(*args, **kwargs))
        return False

    async def log_ticket_transferred(*args: Any, **kwargs: Any) -> bool:
        if callable(original_log_transferred):
            metadata = dict(kwargs.get("metadata") or {})
            actor_id = _safe_str(kwargs.get("actor_user_id"))
            if actor_id and actor_id in _IDENTITY_CACHE:
                metadata.update(_IDENTITY_CACHE[actor_id])
            target_id = _safe_str(metadata.get("transfer_to_user_id"))
            if target_id and target_id in _IDENTITY_CACHE:
                metadata.update(_IDENTITY_CACHE[target_id])
            metadata.setdefault("transfer_identity_source", "ticket_staff_identity_guard")
            kwargs["metadata"] = metadata
            return bool(await original_log_transferred(*args, **kwargs))
        return False

    async def assign_ticket(*, channel_id: int | str, staff_member: discord.Member | discord.User) -> bool:
        _cache_identity(staff_member)
        ok = bool(await original_assign(channel_id=channel_id, staff_member=staff_member))
        if ok:
            await _repair_claim_identity(repo_mod, channel_id, staff_member)
        return ok

    async def transfer_ticket(
        *,
        channel_id: int | str,
        to_staff_member: discord.Member | discord.User,
        actor: Optional[discord.Member | discord.User] = None,
    ) -> bool:
        _cache_identity(actor)
        _cache_identity(to_staff_member, prefix="transfer_target")
        ok = bool(await original_transfer(channel_id=channel_id, to_staff_member=to_staff_member, actor=actor))
        if ok:
            await _repair_claim_identity(repo_mod, channel_id, to_staff_member)
        return ok

    try:
        service_mod._actor_name = actor_name
        service_mod.repo_assign_ticket = getattr(repo_mod, "assign_ticket")
        service_mod.repo_transfer_ticket = getattr(repo_mod, "transfer_ticket")
        service_mod.assign_ticket = assign_ticket
        service_mod.transfer_ticket = transfer_ticket
        if callable(original_log_claimed):
            service_mod.log_ticket_claimed = log_ticket_claimed
        if callable(original_log_transferred):
            service_mod.log_ticket_transferred = log_ticket_transferred
        service_mod._TICKET_STAFF_IDENTITY_SERVICE_PATCHED = True
        _log("patched service claim/transfer activity identity")
        return True
    except Exception as exc:
        _warn(f"service identity patch failed: {exc!r}")
        return False


def apply() -> bool:
    try:
        from ..tickets_new import repository as repo_mod
        from ..tickets_new import service as service_mod
    except Exception as exc:
        _warn(f"could not import ticket modules: {exc!r}")
        return False

    ok = True
    ok = _install_repository_identity_patch(repo_mod) and ok
    ok = _install_service_identity_patch(service_mod, repo_mod) and ok
    return bool(ok)


apply()

__all__ = ["apply"]
