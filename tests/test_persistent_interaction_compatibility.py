from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from stoney_verify import spam_guard, transcripts
from stoney_verify.commands_ext import public_members_group as members
from stoney_verify.tickets_new import panel as ticket_panel


def _in_loop(callable_obj, *args):
    async def _run():
        return callable_obj(*args)

    return asyncio.run(_run())


class _FailOnceBot:
    def __init__(
        self,
        *,
        fail_class: str = "",
        fail_page: str = "",
        fail_view_once: bool = False,
        fail_listener_once: bool = False,
    ) -> None:
        self.fail_class = fail_class
        self.fail_page = fail_page
        self.fail_view_once = fail_view_once
        self.fail_listener_once = fail_listener_once
        self.views: list[tuple[str, str, bool | None]] = []
        self.listeners: list[tuple[Any, str]] = []
        self._failed_class = False
        self._failed_page = False
        self._failed_generic_view = False
        self._failed_listener = False

    def add_view(self, view: Any) -> None:
        class_name = type(view).__name__
        page = str(getattr(view, "page", "") or "")
        disabled = None
        children = list(getattr(view, "children", []) or [])
        if children:
            disabled = bool(getattr(children[0], "disabled", False))

        self.views.append((class_name, page, disabled))

        if (
            self.fail_class
            and class_name == self.fail_class
            and not self._failed_class
        ):
            self._failed_class = True
            raise RuntimeError("class registration failed once")

        if self.fail_page and page == self.fail_page and not self._failed_page:
            self._failed_page = True
            raise RuntimeError("page registration failed once")

        if self.fail_view_once and not self._failed_generic_view:
            self._failed_generic_view = True
            raise RuntimeError("view registration failed once")

    def add_listener(self, callback: Any, name: str) -> None:
        if self.fail_listener_once and not self._failed_listener:
            self._failed_listener = True
            raise RuntimeError("listener registration failed once")
        self.listeners.append((callback, name))


def test_transcript_registry_retries_only_missing_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transcripts, "_TRANSCRIPT_VIEWS_REGISTERED", False)
    monkeypatch.setattr(transcripts, "_TRANSCRIPT_REGISTERED_VIEW_KEYS", set())
    fake = _FailOnceBot(fail_class="ConfirmCloseTicketView")

    assert _in_loop(transcripts.register_transcript_persistent_views, fake) is False
    assert _in_loop(transcripts.register_transcript_persistent_views, fake) is True

    names = [name for name, _page, _disabled in fake.views]
    assert names.count("TicketOpenActionsView") == 1
    assert names.count("ConfirmCloseTicketView") == 2
    assert names.count("StaffClosedTicketView") == 1
    assert names.count("VerificationStaffReviewView") == 1


def test_ticket_panel_registry_retries_only_missing_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ticket_panel, "_PERSISTENT_VIEWS_REGISTERED", False)
    monkeypatch.setattr(ticket_panel, "_PERSISTENT_VIEW_KEYS", set())
    fake = _FailOnceBot(fail_class="StaffGhostTicketView")

    assert _in_loop(ticket_panel.register_ticket_persistent_views, fake) is False
    assert _in_loop(ticket_panel.register_ticket_persistent_views, fake) is True

    names = [name for name, _page, _disabled in fake.views]
    # The dormant legacy-panel compatibility guard can replace the module symbol
    # when imported by another test. Either class still occupies the same single
    # legacy-public registration slot; retry behavior must not duplicate it.
    legacy_public_names = {"TicketPanelView", "DisabledLegacyTicketPanelView"}
    assert sum(name in legacy_public_names for name in names) == 1
    assert names.count("StaffGhostTicketView") == 2
    assert names.count("TicketChannelActionsView") == 1
    assert names.count("LegacyTicketChannelCompatibilityView") == 1


def test_spam_guard_registry_retries_only_missing_page_and_has_one_restore_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spam_guard, "_SPAM_GUARD_VIEWS_REGISTERED", False)
    monkeypatch.setattr(spam_guard, "_SPAM_GUARD_REGISTERED_VIEW_KEYS", set())
    fake = _FailOnceBot(fail_page="detection")

    assert _in_loop(spam_guard.register_spam_guard_persistent_views, fake) is False
    assert _in_loop(spam_guard.register_spam_guard_persistent_views, fake) is True

    pages = [page for name, page, _disabled in fake.views if name == "SpamGuardPanelView"]
    assert pages.count("overview") == 1
    assert pages.count("detection") == 2
    assert pages.count("enforcement") == 1
    assert pages.count("access") == 1

    restore_views = [
        (name, disabled)
        for name, _page, disabled in fake.views
        if name == "SpamIncidentRestoreView"
    ]
    assert restore_views == [("SpamIncidentRestoreView", False)]


def test_member_notice_lookup_binds_to_clicked_dm_not_latest_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    rows = [
        {
            "notice_id": "newer",
            "user_id": "55",
            "dm_message_id": "222",
            "status": members._NOTICE_STATUS_DELIVERED,
            "deadline_at": future,
            "created_at": "2026-09-14T04:00:00+00:00",
        },
        {
            "notice_id": "older",
            "user_id": "55",
            "dm_message_id": "111",
            "status": members._NOTICE_STATUS_DELIVERED,
            "deadline_at": future,
            "created_at": "2026-09-13T04:00:00+00:00",
        },
    ]
    monkeypatch.setattr(
        members,
        "_select_notice_rows",
        lambda **_kwargs: (list(rows), ""),
    )

    notice, state = members._pending_notice_for_dm_message(55, 111)
    assert state == "pending"
    assert notice is not None
    assert notice["notice_id"] == "older"
    assert members._notice_for_dm_message(55, 999) is None


def test_member_notice_lookup_rejects_resolved_and_expired_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    rows = [
        {
            "notice_id": "resolved",
            "user_id": "55",
            "dm_message_id": "333",
            "status": members._NOTICE_STATUS_RESPONDED_STAYING,
            "deadline_at": future,
        },
        {
            "notice_id": "expired",
            "user_id": "55",
            "dm_message_id": "444",
            "status": members._NOTICE_STATUS_DELIVERED,
            "deadline_at": past,
        },
    ]
    monkeypatch.setattr(
        members,
        "_select_notice_rows",
        lambda **_kwargs: (list(rows), ""),
    )

    resolved, resolved_state = members._pending_notice_for_dm_message(55, 333)
    expired, expired_state = members._pending_notice_for_dm_message(55, 444)
    assert resolved is not None and resolved_state == "resolved"
    assert expired is not None and expired_state == "expired"


def test_member_notice_runtime_does_not_mark_failed_registrations_successful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(members, "_NOTICE_DM_VIEW_REGISTERED", False)
    monkeypatch.setattr(members, "_NOTICE_WORKER_LISTENER_ATTACHED", False)

    fake = _FailOnceBot(fail_view_once=True, fail_listener_once=True)
    with pytest.raises(RuntimeError, match="on_ready listener"):
        members._start_member_notice_worker(fake)

    assert members._NOTICE_DM_VIEW_REGISTERED is False
    assert members._NOTICE_WORKER_LISTENER_ATTACHED is False

    members._start_member_notice_worker(fake)
    assert members._NOTICE_DM_VIEW_REGISTERED is True
    assert members._NOTICE_WORKER_LISTENER_ATTACHED is True
    assert len(fake.listeners) == 1
