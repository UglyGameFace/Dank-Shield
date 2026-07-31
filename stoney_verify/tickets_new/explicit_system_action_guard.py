from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Optional


_SYSTEM_REASON: ContextVar[Optional[str]] = ContextVar(
    "dank_explicit_ticket_system_reason",
    default=None,
)
_INSTALLED_MARKER = "_dank_explicit_ticket_system_guard_installed"


def explicit_ticket_system_reason() -> Optional[str]:
    reason = _SYSTEM_REASON.get()
    text = str(reason or "").strip()
    return text or None


def explicit_ticket_system_action_active() -> bool:
    return explicit_ticket_system_reason() is not None


@asynccontextmanager
async def explicit_ticket_system_action(reason: str) -> AsyncIterator[None]:
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("Explicit ticket system actions require a non-empty reason.")
    token = _SYSTEM_REASON.set(clean_reason)
    try:
        yield
    finally:
        _SYSTEM_REASON.reset(token)


def _actor_value(args: tuple[Any, ...], kwargs: dict[str, Any], *, key: str, position: int = -1) -> Any:
    if key in kwargs:
        return kwargs.get(key)
    if position >= 0 and len(args) > position:
        return args[position]
    return None


def _deny_implicit_system(action: str) -> bool:
    if explicit_ticket_system_action_active():
        return False
    try:
        print(
            "🚨 ticket_system_guard blocked implicit actorless mutation "
            f"action={action} reason=explicit-system-scope-required"
        )
    except Exception:
        pass
    return True


def install_explicit_system_action_guards(
    *,
    service_module: Any,
    transcript_service: Any,
    transcript_views: Any,
    automation_worker: Any,
    departed_cleanup: Any,
    ticket_admin: Any,
    legacy_timer_module: Any,
    modern_timer_module: Any,
) -> None:
    """Remove the historical ``actor=None`` implicit-system convention.

    Human-facing code must provide an actor. Only the inactivity automation
    worker, departed-member cleanup, and verification expiry timers are granted
    short, named system scopes. The scope is carried with ``ContextVar`` so
    concurrent ticket work cannot leak authority between tasks.
    """
    if bool(getattr(service_module, _INSTALLED_MARKER, False)):
        return

    service_names = (
        "mark_ticket_closed",
        "mark_ticket_deleted",
        "attach_transcript_to_ticket",
        "unclaim_ticket",
        "transfer_ticket",
        "set_ticket_priority",
        "reopen_ticket",
        "reopen_ticket_channel",
    )
    missing_service = [name for name in service_names if not callable(getattr(service_module, name, None))]
    if missing_service:
        raise RuntimeError(
            "Cannot install explicit system ticket guards; missing service functions: "
            + ", ".join(sorted(missing_service))
        )

    required_transcript = (
        "post_transcript_to_channel",
        "delete_ticket_with_optional_transcript",
        "attach_transcript_to_ticket",
        "mark_ticket_deleted",
    )
    missing_transcript = [
        name for name in required_transcript
        if not callable(getattr(transcript_service, name, None))
    ]
    if missing_transcript:
        raise RuntimeError(
            "Cannot install explicit system ticket guards; missing transcript functions: "
            + ", ".join(sorted(missing_transcript))
        )

    if not callable(getattr(transcript_views, "send_tickettool_style_transcript", None)):
        raise RuntimeError("Cannot install explicit system ticket guards; transcript view sender unavailable.")
    if not callable(getattr(automation_worker, "service_mark_ticket_closed", None)):
        raise RuntimeError("Cannot install explicit system ticket guards; automation close helper unavailable.")
    if not callable(getattr(automation_worker, "transcript_post_to_channel", None)):
        raise RuntimeError("Cannot install explicit system ticket guards; automation transcript helper unavailable.")

    departed_names = (
        "mark_ticket_closed",
        "mark_ticket_deleted",
        "close_verification_ticket_for_departed_member",
        "reconcile_stale_open_verification_tickets",
    )
    missing_departed = [name for name in departed_names if not callable(getattr(departed_cleanup, name, None))]
    if missing_departed:
        raise RuntimeError(
            "Cannot install explicit system ticket guards; departed cleanup functions unavailable: "
            + ", ".join(sorted(missing_departed))
        )

    for label, timer_module in (
        ("legacy verification timer", legacy_timer_module),
        ("public verification timer", modern_timer_module),
    ):
        missing_timer = [
            name
            for name in ("_kick_after_timer", "send_tickettool_style_transcript")
            if not callable(getattr(timer_module, name, None))
        ]
        if missing_timer:
            raise RuntimeError(
                f"Cannot install explicit system ticket guards; {label} missing: "
                + ", ".join(sorted(missing_timer))
            )

    original_close = service_module.mark_ticket_closed
    original_delete = service_module.mark_ticket_deleted
    original_attach = service_module.attach_transcript_to_ticket
    original_unclaim = service_module.unclaim_ticket
    original_transfer = service_module.transfer_ticket
    original_priority = service_module.set_ticket_priority
    original_reopen = service_module.reopen_ticket
    original_reopen_channel = service_module.reopen_ticket_channel

    async def guarded_close(*args: Any, **kwargs: Any) -> bool:
        actor = _actor_value(args, kwargs, key="closed_by")
        if actor is None and _deny_implicit_system("close"):
            return False
        return bool(await original_close(*args, **kwargs))

    async def guarded_delete(*args: Any, **kwargs: Any) -> bool:
        actor = _actor_value(args, kwargs, key="deleted_by")
        if actor is None and _deny_implicit_system("delete"):
            return False
        return bool(await original_delete(*args, **kwargs))

    async def guarded_attach(*args: Any, **kwargs: Any) -> bool:
        actor = _actor_value(args, kwargs, key="actor")
        if actor is None and _deny_implicit_system("transcript"):
            return False
        return bool(await original_attach(*args, **kwargs))

    async def guarded_unclaim(*args: Any, **kwargs: Any) -> bool:
        actor = _actor_value(args, kwargs, key="actor")
        if actor is None and _deny_implicit_system("unclaim"):
            return False
        return bool(await original_unclaim(*args, **kwargs))

    async def guarded_transfer(*args: Any, **kwargs: Any) -> bool:
        actor = _actor_value(args, kwargs, key="actor")
        if actor is None and _deny_implicit_system("transfer"):
            return False
        return bool(await original_transfer(*args, **kwargs))

    async def guarded_priority(*args: Any, **kwargs: Any) -> bool:
        actor = _actor_value(args, kwargs, key="actor")
        if actor is None and _deny_implicit_system("priority"):
            return False
        return bool(await original_priority(*args, **kwargs))

    async def guarded_reopen(*args: Any, **kwargs: Any) -> bool:
        actor = _actor_value(args, kwargs, key="actor")
        if actor is None and _deny_implicit_system("reopen"):
            return False
        return bool(await original_reopen(*args, **kwargs))

    async def guarded_reopen_channel(*args: Any, **kwargs: Any) -> bool:
        actor = _actor_value(args, kwargs, key="actor")
        if actor is None and _deny_implicit_system("reopen_channel"):
            return False
        return bool(await original_reopen_channel(*args, **kwargs))

    service_module.mark_ticket_closed = guarded_close
    service_module.mark_ticket_deleted = guarded_delete
    service_module.attach_transcript_to_ticket = guarded_attach
    service_module.unclaim_ticket = guarded_unclaim
    service_module.transfer_ticket = guarded_transfer
    service_module.set_ticket_priority = guarded_priority
    service_module.reopen_ticket = guarded_reopen
    service_module.reopen_ticket_channel = guarded_reopen_channel

    original_transcript_post = transcript_service.post_transcript_to_channel
    original_transcript_delete = transcript_service.delete_ticket_with_optional_transcript
    original_view_transcript = transcript_views.send_tickettool_style_transcript

    async def guarded_transcript_post(*args: Any, **kwargs: Any) -> Any:
        actor = _actor_value(args, kwargs, key="deleted_by")
        if actor is None and _deny_implicit_system("transcript_post"):
            return None, None
        return await original_transcript_post(*args, **kwargs)

    async def guarded_transcript_delete(*args: Any, **kwargs: Any) -> dict[str, Any]:
        actor = _actor_value(args, kwargs, key="deleted_by")
        if actor is None and _deny_implicit_system("transcript_delete"):
            return {
                "ok": False,
                "deleted": False,
                "reason": "Explicit system scope or human actor required.",
                "authorization_code": "actor_required",
            }
        result = await original_transcript_delete(*args, **kwargs)
        return dict(result or {})

    async def guarded_view_transcript(*args: Any, **kwargs: Any) -> Any:
        actor = _actor_value(args, kwargs, key="closed_by", position=3)
        if actor is None and _deny_implicit_system("tickettool_transcript"):
            return False
        return await original_view_transcript(*args, **kwargs)

    transcript_service.attach_transcript_to_ticket = guarded_attach
    transcript_service.mark_ticket_deleted = guarded_delete
    transcript_service.post_transcript_to_channel = guarded_transcript_post
    transcript_service.delete_ticket_with_optional_transcript = guarded_transcript_delete
    transcript_views.send_tickettool_style_transcript = guarded_view_transcript

    # Refresh stale imported aliases used by legacy command modules.
    if hasattr(ticket_admin, "send_tickettool_style_transcript"):
        ticket_admin.send_tickettool_style_transcript = guarded_view_transcript
    if hasattr(ticket_admin, "transcript_post_to_channel"):
        ticket_admin.transcript_post_to_channel = guarded_transcript_post
    if hasattr(ticket_admin, "transcript_staff_delete_closed_ticket"):
        ticket_admin.transcript_staff_delete_closed_ticket = guarded_transcript_delete

    original_automation_close = automation_worker.service_mark_ticket_closed
    original_automation_transcript = automation_worker.transcript_post_to_channel

    async def automation_close(*args: Any, **kwargs: Any) -> bool:
        kwargs["closed_by"] = None
        async with explicit_ticket_system_action("ticket-automation-close"):
            return bool(await original_automation_close(*args, **kwargs))

    async def automation_transcript(*args: Any, **kwargs: Any) -> Any:
        kwargs["deleted_by"] = None
        async with explicit_ticket_system_action("ticket-automation-transcript"):
            return await original_automation_transcript(*args, **kwargs)

    automation_worker.service_mark_ticket_closed = automation_close
    automation_worker.transcript_post_to_channel = automation_transcript

    original_departed_repo_close = departed_cleanup.mark_ticket_closed
    original_departed_repo_delete = departed_cleanup.mark_ticket_deleted
    original_departed_close = departed_cleanup.close_verification_ticket_for_departed_member
    original_departed_reconcile = departed_cleanup.reconcile_stale_open_verification_tickets

    async def departed_repo_close(*args: Any, **kwargs: Any) -> Any:
        if _deny_implicit_system("departed_repo_close"):
            return False
        return await original_departed_repo_close(*args, **kwargs)

    async def departed_repo_delete(*args: Any, **kwargs: Any) -> Any:
        if _deny_implicit_system("departed_repo_delete"):
            return False
        return await original_departed_repo_delete(*args, **kwargs)

    async def departed_close(*args: Any, **kwargs: Any) -> bool:
        async with explicit_ticket_system_action("departed-member-ticket-cleanup"):
            return bool(await original_departed_close(*args, **kwargs))

    async def departed_reconcile(*args: Any, **kwargs: Any) -> int:
        async with explicit_ticket_system_action("departed-member-startup-reconciliation"):
            return int((await original_departed_reconcile(*args, **kwargs)) or 0)

    departed_cleanup.mark_ticket_closed = departed_repo_close
    departed_cleanup.mark_ticket_deleted = departed_repo_delete
    departed_cleanup.close_verification_ticket_for_departed_member = departed_close
    departed_cleanup.reconcile_stale_open_verification_tickets = departed_reconcile

    def install_timer_scope(timer_module: Any, *, scope_reason: str) -> None:
        original_timer_job = timer_module._kick_after_timer

        async def system_transcript(*args: Any, **kwargs: Any) -> Any:
            # The timer starter is audit metadata, not a claimant. Force system
            # attribution so claim-first cannot mistake that staff member for
            # the actor authorizing transcript/delete behavior.
            kwargs["closed_by"] = None
            return await transcript_views.send_tickettool_style_transcript(*args, **kwargs)

        async def system_timer_job(*args: Any, **kwargs: Any) -> Any:
            async with explicit_ticket_system_action(scope_reason):
                return await original_timer_job(*args, **kwargs)

        timer_module.send_tickettool_style_transcript = system_transcript
        timer_module._kick_after_timer = system_timer_job

    install_timer_scope(
        legacy_timer_module,
        scope_reason="legacy-verification-no-response-timer",
    )
    install_timer_scope(
        modern_timer_module,
        scope_reason="verification-no-response-timer",
    )

    setattr(service_module, _INSTALLED_MARKER, True)


__all__ = [
    "explicit_ticket_system_action",
    "explicit_ticket_system_action_active",
    "explicit_ticket_system_reason",
    "install_explicit_system_action_guards",
]
