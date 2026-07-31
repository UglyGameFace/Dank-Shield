from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Optional

import discord

from .service import authorize_ticket_action


_INSTALLED_MARKER = "_dank_ticket_api_claim_guard_installed"
_API_ACTOR: ContextVar[Optional[discord.Member]] = ContextVar("dank_ticket_api_actor", default=None)


async def _request_actor(
    api_server: Any,
    request: Any,
    *,
    actor_keys: tuple[str, ...],
) -> tuple[Optional[discord.TextChannel], Optional[discord.Member], Any, Optional[Any]]:
    data = await api_server._request_data(request)
    channel, error = await api_server._get_text_channel(data.get("channel_id"))
    if error is not None:
        return None, None, data, error
    if not isinstance(channel, discord.TextChannel):
        return None, None, data, api_server._json_error("Ticket channel not found", 404)

    actor_id: Any = None
    for key in actor_keys:
        candidate = data.get(key)
        if candidate not in (None, ""):
            actor_id = candidate
            break
    if actor_id in (None, ""):
        return (
            channel,
            None,
            data,
            api_server._json_error(
                f"{actor_keys[0]} required for this human ticket action",
                400,
            ),
        )

    actor, member_error = await api_server._get_member_from_guild(channel.guild, actor_id)
    if member_error is not None:
        return channel, None, data, member_error
    return channel, actor, data, None


async def _authorize_endpoint(
    api_server: Any,
    request: Any,
    *,
    action: str,
    actor_keys: tuple[str, ...],
) -> tuple[Optional[discord.Member], Optional[Any]]:
    channel, actor, _data, error = await _request_actor(
        api_server,
        request,
        actor_keys=actor_keys,
    )
    if error is not None:
        return None, error
    assert channel is not None
    assert actor is not None

    row = await api_server._ticket_row_for_channel(channel)
    decision = await authorize_ticket_action(
        channel_id=channel.id,
        actor=actor,
        action=action,
        row=row,
    )
    if decision.allowed:
        return actor, None

    return (
        actor,
        api_server._json_error(
            decision.message,
            403,
            authorization_code=decision.code,
            action=action,
            actor_id=str(actor.id),
            channel_id=str(channel.id),
        ),
    )


def install_api_claim_runtime_guards(api_server: Any) -> None:
    """Require a real current claimant for dashboard lifecycle mutations."""
    if bool(getattr(api_server, _INSTALLED_MARKER, False)):
        return

    required = (
        "start_api",
        "close_ticket",
        "reopen_ticket_endpoint",
        "delete_ticket",
        "unclaim_ticket_endpoint",
        "transfer_ticket_endpoint",
        "_request_data",
        "_get_text_channel",
        "_get_member_from_guild",
        "_ticket_row_for_channel",
        "_json_error",
        "delete_ticket_with_optional_transcript",
        "post_transcript_to_channel",
        "unclaim_ticket",
        "transfer_ticket",
    )
    missing = [name for name in required if not hasattr(api_server, name)]
    if missing:
        raise RuntimeError(
            "Cannot install ticket API claim guards; missing: " + ", ".join(sorted(missing))
        )

    original_start_api = api_server.start_api
    original_close = api_server.close_ticket
    original_reopen = api_server.reopen_ticket_endpoint
    original_delete_endpoint = api_server.delete_ticket
    original_unclaim_endpoint = api_server.unclaim_ticket_endpoint
    original_transfer_endpoint = api_server.transfer_ticket_endpoint
    original_delete_service = api_server.delete_ticket_with_optional_transcript
    original_transcript_service = api_server.post_transcript_to_channel
    original_unclaim_service = api_server.unclaim_ticket
    original_transfer_service = api_server.transfer_ticket

    callables = {
        "start_api": original_start_api,
        "close_ticket": original_close,
        "reopen_ticket_endpoint": original_reopen,
        "delete_ticket": original_delete_endpoint,
        "unclaim_ticket_endpoint": original_unclaim_endpoint,
        "transfer_ticket_endpoint": original_transfer_endpoint,
        "delete_ticket_with_optional_transcript": original_delete_service,
        "post_transcript_to_channel": original_transcript_service,
        "unclaim_ticket": original_unclaim_service,
        "transfer_ticket": original_transfer_service,
    }
    unavailable = sorted(name for name, value in callables.items() if not callable(value))
    if unavailable:
        raise RuntimeError(
            "Cannot install ticket API claim guards; unavailable: " + ", ".join(unavailable)
        )

    async def guarded_close(request: Any) -> Any:
        _actor, error = await _authorize_endpoint(
            api_server,
            request,
            action="close",
            actor_keys=("staff_id",),
        )
        if error is not None:
            return error
        return await original_close(request)

    async def guarded_reopen(request: Any) -> Any:
        _actor, error = await _authorize_endpoint(
            api_server,
            request,
            action="reopen",
            actor_keys=("actor_id", "staff_id"),
        )
        if error is not None:
            return error
        return await original_reopen(request)

    async def guarded_delete_endpoint(request: Any) -> Any:
        actor, error = await _authorize_endpoint(
            api_server,
            request,
            action="delete",
            actor_keys=("staff_id",),
        )
        if error is not None:
            return error
        token = _API_ACTOR.set(actor)
        try:
            return await original_delete_endpoint(request)
        finally:
            _API_ACTOR.reset(token)

    async def guarded_unclaim_endpoint(request: Any) -> Any:
        actor, error = await _authorize_endpoint(
            api_server,
            request,
            action="unclaim",
            actor_keys=("actor_id", "staff_id"),
        )
        if error is not None:
            return error
        token = _API_ACTOR.set(actor)
        try:
            return await original_unclaim_endpoint(request)
        finally:
            _API_ACTOR.reset(token)

    async def guarded_transfer_endpoint(request: Any) -> Any:
        actor, error = await _authorize_endpoint(
            api_server,
            request,
            action="transfer",
            actor_keys=("actor_id",),
        )
        if error is not None:
            return error
        token = _API_ACTOR.set(actor)
        try:
            return await original_transfer_endpoint(request)
        finally:
            _API_ACTOR.reset(token)

    async def guarded_delete_service(*args: Any, **kwargs: Any) -> dict[str, Any]:
        channel = kwargs.get("channel")
        actor = kwargs.get("deleted_by") or _API_ACTOR.get()
        if not isinstance(channel, discord.TextChannel) or not isinstance(actor, discord.Member):
            return {
                "ok": False,
                "deleted": False,
                "reason": "A resolved staff actor is required for dashboard ticket deletion.",
                "authorization_code": "actor_required",
            }
        decision = await authorize_ticket_action(
            channel_id=channel.id,
            actor=actor,
            action="delete",
        )
        if not decision.allowed:
            return {
                "ok": False,
                "deleted": False,
                "reason": decision.message,
                "authorization_code": decision.code,
            }
        kwargs["deleted_by"] = actor
        result = await original_delete_service(*args, **kwargs)
        if isinstance(result, dict):
            normalized = dict(result)
            normalized.setdefault(
                "ok",
                bool(normalized.get("deleted") or normalized.get("channel_deleted")),
            )
            return normalized
        return {"ok": bool(result), "deleted": bool(result)}

    async def guarded_transcript_service(*args: Any, **kwargs: Any) -> Any:
        channel = kwargs.get("ticket_channel")
        actor = kwargs.get("deleted_by") or _API_ACTOR.get()
        if not isinstance(channel, discord.TextChannel) or not isinstance(actor, discord.Member):
            return None, None
        decision = await authorize_ticket_action(
            channel_id=channel.id,
            actor=actor,
            action="transcript",
        )
        if not decision.allowed:
            return None, None
        kwargs["deleted_by"] = actor
        return await original_transcript_service(*args, **kwargs)

    async def guarded_unclaim_service(*args: Any, **kwargs: Any) -> bool:
        actor = kwargs.get("actor") or _API_ACTOR.get()
        if not isinstance(actor, discord.Member):
            return False
        kwargs["actor"] = actor
        return bool(await original_unclaim_service(*args, **kwargs))

    async def guarded_transfer_service(*args: Any, **kwargs: Any) -> bool:
        actor = kwargs.get("actor") or _API_ACTOR.get()
        if not isinstance(actor, discord.Member):
            return False
        kwargs["actor"] = actor
        return bool(await original_transfer_service(*args, **kwargs))

    async def guarded_start_api(bot_instance: discord.Client) -> Any:
        # Handler globals are already patched below. Keeping this wrapper makes
        # the security contract explicit at route-registration time too.
        return await original_start_api(bot_instance)

    api_server.close_ticket = guarded_close
    api_server.reopen_ticket_endpoint = guarded_reopen
    api_server.delete_ticket = guarded_delete_endpoint
    api_server.unclaim_ticket_endpoint = guarded_unclaim_endpoint
    api_server.transfer_ticket_endpoint = guarded_transfer_endpoint
    api_server.delete_ticket_with_optional_transcript = guarded_delete_service
    api_server.post_transcript_to_channel = guarded_transcript_service
    api_server.unclaim_ticket = guarded_unclaim_service
    api_server.transfer_ticket = guarded_transfer_service
    api_server.start_api = guarded_start_api
    setattr(api_server, _INSTALLED_MARKER, True)


__all__ = ["install_api_claim_runtime_guards"]
