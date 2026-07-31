from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from stoney_verify.tickets_new.explicit_system_action_guard import (
    explicit_ticket_system_action,
    explicit_ticket_system_action_active,
    explicit_ticket_system_reason,
    install_explicit_system_action_guards,
)


def _async_bool(calls: list[str], name: str):
    async def callback(*args: Any, **kwargs: Any) -> bool:
        _ = args, kwargs
        calls.append(name)
        return True

    return callback


def _fake_timer(calls: list[str], name: str) -> SimpleNamespace:
    timer = SimpleNamespace()

    async def original_transcript(*args: Any, **kwargs: Any) -> bool:
        _ = args, kwargs
        calls.append(f"{name}_original_transcript")
        return True

    async def original_timer_job(*args: Any, **kwargs: Any) -> str:
        _ = args, kwargs
        calls.append(f"{name}_timer_scope={explicit_ticket_system_reason()}")
        await timer.send_tickettool_style_transcript(
            object(),
            object(),
            closed_by=object(),
            decision="expired",
        )
        calls.append(f"{name}_timer_scope_after_transcript={explicit_ticket_system_reason()}")
        return name

    timer.send_tickettool_style_transcript = original_transcript
    timer._kick_after_timer = original_timer_job
    return timer


def _fake_modules():
    calls: list[str] = []

    service = SimpleNamespace(
        mark_ticket_closed=_async_bool(calls, "service_close"),
        mark_ticket_deleted=_async_bool(calls, "service_delete"),
        attach_transcript_to_ticket=_async_bool(calls, "service_transcript"),
        unclaim_ticket=_async_bool(calls, "service_unclaim"),
        transfer_ticket=_async_bool(calls, "service_transfer"),
        set_ticket_priority=_async_bool(calls, "service_priority"),
        reopen_ticket=_async_bool(calls, "service_reopen"),
        reopen_ticket_channel=_async_bool(calls, "service_reopen_channel"),
    )

    async def transcript_post(*args: Any, **kwargs: Any):
        _ = args, kwargs
        calls.append("transcript_post")
        return None, "https://example.invalid/transcript"

    async def transcript_delete(*args: Any, **kwargs: Any):
        _ = args, kwargs
        calls.append("transcript_delete")
        return {"ok": True, "deleted": True}

    transcript_service = SimpleNamespace(
        post_transcript_to_channel=transcript_post,
        delete_ticket_with_optional_transcript=transcript_delete,
        attach_transcript_to_ticket=service.attach_transcript_to_ticket,
        mark_ticket_deleted=service.mark_ticket_deleted,
    )

    async def view_transcript(*args: Any, **kwargs: Any):
        _ = args
        calls.append("view_transcript")
        calls.append(f"view_transcript_closed_by={kwargs.get('closed_by')!r}")
        calls.append(f"view_transcript_scope={explicit_ticket_system_reason()}")
        return True

    transcript_views = SimpleNamespace(send_tickettool_style_transcript=view_transcript)

    automation_worker = SimpleNamespace(
        service_mark_ticket_closed=service.mark_ticket_closed,
        transcript_post_to_channel=transcript_post,
    )

    departed_cleanup = SimpleNamespace(
        mark_ticket_closed=_async_bool(calls, "departed_repo_close"),
        mark_ticket_deleted=_async_bool(calls, "departed_repo_delete"),
        close_verification_ticket_for_departed_member=_async_bool(calls, "departed_close"),
        reconcile_stale_open_verification_tickets=_async_bool(calls, "departed_reconcile"),
    )

    ticket_admin = SimpleNamespace(
        send_tickettool_style_transcript=view_transcript,
        transcript_post_to_channel=transcript_post,
        transcript_staff_delete_closed_ticket=transcript_delete,
    )

    legacy_timer = _fake_timer(calls, "legacy")
    modern_timer = _fake_timer(calls, "modern")

    return (
        calls,
        service,
        transcript_service,
        transcript_views,
        automation_worker,
        departed_cleanup,
        ticket_admin,
        legacy_timer,
        modern_timer,
    )


def _install(fake_modules: tuple[Any, ...]) -> None:
    (
        _calls,
        service,
        transcript_service,
        transcript_views,
        automation_worker,
        departed_cleanup,
        ticket_admin,
        legacy_timer,
        modern_timer,
    ) = fake_modules

    install_explicit_system_action_guards(
        service_module=service,
        transcript_service=transcript_service,
        transcript_views=transcript_views,
        automation_worker=automation_worker,
        departed_cleanup=departed_cleanup,
        ticket_admin=ticket_admin,
        legacy_timer_module=legacy_timer,
        modern_timer_module=modern_timer,
    )


def test_actorless_service_mutations_require_explicit_scope() -> None:
    async def scenario() -> None:
        fake_modules = _fake_modules()
        calls = fake_modules[0]
        service = fake_modules[1]
        _install(fake_modules)

        assert explicit_ticket_system_action_active() is False
        assert explicit_ticket_system_reason() is None

        assert await service.mark_ticket_closed(channel=object(), closed_by=None) is False
        assert await service.mark_ticket_deleted(channel_id=1, deleted_by=None) is False
        assert await service.reopen_ticket(channel_id=1, actor=None) is False
        assert calls == []

        actor = object()
        assert await service.mark_ticket_closed(channel=object(), closed_by=actor) is True
        assert calls == ["service_close"]

        async with explicit_ticket_system_action("unit-test-maintenance"):
            assert explicit_ticket_system_action_active() is True
            assert explicit_ticket_system_reason() == "unit-test-maintenance"
            assert await service.mark_ticket_closed(channel=object(), closed_by=None) is True
            assert await service.mark_ticket_deleted(channel_id=1, deleted_by=None) is True
            assert await service.reopen_ticket(channel_id=1, actor=None) is True

        assert explicit_ticket_system_action_active() is False
        assert explicit_ticket_system_reason() is None
        assert calls[-3:] == ["service_close", "service_delete", "service_reopen"]

    asyncio.run(scenario())


def test_only_named_maintenance_wrappers_gain_actorless_authority() -> None:
    async def scenario() -> None:
        fake_modules = _fake_modules()
        calls = fake_modules[0]
        automation_worker = fake_modules[4]
        departed_cleanup = fake_modules[5]
        legacy_timer = fake_modules[7]
        modern_timer = fake_modules[8]
        _install(fake_modules)

        assert await automation_worker.service_mark_ticket_closed(
            channel=object(),
            closed_by=object(),
            reason="idle",
        ) is True
        assert await automation_worker.transcript_post_to_channel(
            ticket_channel=object(),
            deleted_by=object(),
            reason="idle",
        ) == (None, "https://example.invalid/transcript")
        assert await departed_cleanup.close_verification_ticket_for_departed_member(object()) is True
        assert await departed_cleanup.reconcile_stale_open_verification_tickets([]) == 1

        assert await legacy_timer._kick_after_timer(object(), object(), 24) == "legacy"
        assert explicit_ticket_system_action_active() is False
        assert await modern_timer._kick_after_timer(object(), object(), 24) == "modern"

        assert explicit_ticket_system_action_active() is False
        assert explicit_ticket_system_reason() is None
        assert "service_close" in calls
        assert "transcript_post" in calls
        assert "departed_close" in calls
        assert "departed_reconcile" in calls
        assert "legacy_timer_scope=legacy-verification-no-response-timer" in calls
        assert "legacy_timer_scope_after_transcript=legacy-verification-no-response-timer" in calls
        assert "modern_timer_scope=verification-no-response-timer" in calls
        assert "modern_timer_scope_after_transcript=verification-no-response-timer" in calls
        assert calls.count("view_transcript_closed_by=None") == 2
        assert "view_transcript_scope=legacy-verification-no-response-timer" in calls
        assert "view_transcript_scope=verification-no-response-timer" in calls
        assert "legacy_original_transcript" not in calls
        assert "modern_original_transcript" not in calls

        # Direct repository aliases remain blocked outside the departed cleanup scope.
        assert await departed_cleanup.mark_ticket_deleted(channel_id=1) is False
        assert await departed_cleanup.mark_ticket_closed(channel_id=1) is False

    asyncio.run(scenario())
