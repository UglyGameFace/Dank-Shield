from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify.commands_ext import public_ticket_panel_clean as panel


def _reset_ticket_state() -> None:
    panel._CONFIRM_LOCKS.clear()
    panel._CREATE_LOCKS.clear()
    panel._MENU_SESSIONS.clear()


def test_confirm_acknowledges_before_slow_ticket_creation(monkeypatch) -> None:
    async def scenario() -> None:
        _reset_ticket_state()
        events: list[str] = []

        class FakeResponse:
            def __init__(self) -> None:
                self.done = False

            def is_done(self) -> bool:
                return self.done

            async def edit_message(self, **_kwargs) -> None:
                events.append("ack")
                self.done = True

        owner = SimpleNamespace(id=222)
        guild = SimpleNamespace(id=111)
        interaction = SimpleNamespace(guild=guild, user=owner, response=FakeResponse())

        monkeypatch.setattr(panel, "_member_from_interaction", lambda _i: owner)
        monkeypatch.setattr(panel, "_menu_session_current", lambda *_args: True)
        monkeypatch.setattr(panel, "_consume_menu_session", lambda *_args: True)
        monkeypatch.setattr(panel, "_form_questions", lambda _row: [])

        async def forbidden_preflight(_guild):
            raise AssertionError("Confirm must not do slow setup I/O before acknowledging Discord")

        async def fake_create(_interaction, _row) -> None:
            assert interaction.response.is_done() is True
            events.append("create")

        monkeypatch.setattr(panel, "_ticket_setup_preflight", forbidden_preflight)
        monkeypatch.setattr(panel, "_create_ticket", fake_create)

        view = panel.TicketConfirmView(
            [{"slug": "support", "name": "Support"}],
            {"slug": "support", "name": "Support"},
            owner.id,
            "session-1",
        )
        await panel.TicketConfirmView.confirm(view, interaction, None)

        assert events == ["ack", "create"]

    asyncio.run(scenario())


def test_form_confirm_opens_modal_before_any_slow_preflight(monkeypatch) -> None:
    async def scenario() -> None:
        _reset_ticket_state()
        events: list[str] = []
        owner = SimpleNamespace(id=444)
        guild = SimpleNamespace(id=333)
        interaction = SimpleNamespace(guild=guild, user=owner)

        monkeypatch.setattr(panel, "_member_from_interaction", lambda _i: owner)
        monkeypatch.setattr(panel, "_menu_session_current", lambda *_args: True)
        monkeypatch.setattr(panel, "_consume_menu_session", lambda *_args: True)
        monkeypatch.setattr(panel, "_form_questions", lambda _row: [{"label": "Reason"}])

        async def fake_modal(_interaction, _row, _questions) -> bool:
            events.append("modal")
            return True

        async def forbidden_preflight(_guild):
            raise AssertionError("Form Confirm must open the modal before slow setup I/O")

        async def forbidden_create(_interaction, _row) -> None:
            raise AssertionError("Ticket creation should wait for modal submission")

        monkeypatch.setattr(panel, "_open_form_modal", fake_modal)
        monkeypatch.setattr(panel, "_ticket_setup_preflight", forbidden_preflight)
        monkeypatch.setattr(panel, "_create_ticket", forbidden_create)

        view = panel.TicketConfirmView(
            [{"slug": "appeal", "name": "Appeal"}],
            {"slug": "appeal", "name": "Appeal"},
            owner.id,
            "session-2",
        )
        await panel.TicketConfirmView.confirm(view, interaction, None)

        assert events == ["modal"]

    asyncio.run(scenario())


def test_counter_allocator_failure_is_user_visible_and_creates_no_channel(monkeypatch) -> None:
    async def scenario() -> None:
        _reset_ticket_state()
        replies: list[str] = []
        channel_create_calls = 0

        class FakeMember:
            def __init__(self, member_id: int) -> None:
                self.id = member_id

            def __str__(self) -> str:
                return "TicketUser"

        monkeypatch.setattr(panel.discord, "Member", FakeMember)

        owner = FakeMember(666)
        guild = SimpleNamespace(id=555, me=object())
        interaction = SimpleNamespace(guild=guild, user=owner)
        parent = SimpleNamespace(name="ACTIVE TICKETS")
        staff = object()

        async def noop_defer(_interaction, _thinking=False) -> None:
            return None

        async def active_category(_guild):
            return parent

        async def staff_role(_guild):
            return staff

        async def existing_open(_guild, _owner):
            return None

        async def broken_number(_guild, _parent):
            raise RuntimeError("Ticket numbering database unavailable; refusing duplicate number")

        async def create_channel(*_args, **_kwargs):
            nonlocal channel_create_calls
            channel_create_calls += 1
            raise AssertionError("channel creation must not run without a reserved number")

        async def capture_reply(_interaction, content: str, **_kwargs) -> None:
            replies.append(content)

        monkeypatch.setattr(panel, "_defer", noop_defer)
        monkeypatch.setattr(panel, "_active_category", active_category)
        monkeypatch.setattr(panel, "_staff_role", staff_role)
        monkeypatch.setattr(panel, "_missing_category_perms", lambda *_args: [])
        monkeypatch.setattr(panel, "_ticket_category_shape_blockers", lambda *_args: [])
        monkeypatch.setattr(panel, "_existing_open", existing_open)
        monkeypatch.setattr(panel, "_next_number", broken_number)
        monkeypatch.setattr(panel, "_create_synced_ticket_channel", create_channel)
        monkeypatch.setattr(panel, "_ephemeral", capture_reply)

        await panel._create_ticket(interaction, {"slug": "support", "name": "Support"})

        assert channel_create_calls == 0
        assert replies
        assert "number" in replies[-1].lower()
        assert "nothing was created" in replies[-1].lower()

    asyncio.run(scenario())
