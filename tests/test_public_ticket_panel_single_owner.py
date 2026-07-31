from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.startup_guards import public_ticket_panel_clean_hardening as guard


ROOT = Path(__file__).resolve().parents[1]
HARDENING = ROOT / "stoney_verify" / "startup_guards" / "public_ticket_panel_clean_hardening.py"
PANEL = ROOT / "stoney_verify" / "commands_ext" / "public_ticket_panel_clean.py"


def _reset_guard_state() -> None:
    guard._INTERACTION_LOCKS.clear()
    guard._INTERACTION_DONE_UNTIL.clear()


def test_same_discord_interaction_runs_the_menu_handler_once() -> None:
    async def scenario() -> None:
        _reset_guard_state()
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[int] = []
        interaction = SimpleNamespace(id=123456789)

        async def original(value) -> None:
            calls.append(value.id)
            started.set()
            await release.wait()

        first = asyncio.create_task(guard._handle_once(original, interaction))
        await started.wait()

        # Simulate the fallback listener receiving the same interaction while
        # the persistent view callback is still waiting on Discord/DB work.
        await guard._handle_once(original, interaction)
        release.set()
        await first

        # A completed duplicate delivery is ignored too.
        await guard._handle_once(original, interaction)
        assert calls == [123456789]

    asyncio.run(scenario())


def test_distinct_interactions_from_same_member_are_not_user_rate_limited() -> None:
    async def scenario() -> None:
        _reset_guard_state()
        calls: list[int] = []

        async def original(value) -> None:
            calls.append(value.id)

        await guard._handle_once(original, SimpleNamespace(id=101))
        await guard._handle_once(original, SimpleNamespace(id=102))
        assert calls == [101, 102]

    asyncio.run(scenario())


def test_persistent_view_removes_redundant_fallback_listener() -> None:
    listener = object()
    panel = SimpleNamespace(
        _PANEL_VIEW_REGISTERED=False,
        _PANEL_FALLBACK_LISTENER_REGISTERED=False,
        _component_fallback_listener=listener,
    )

    class Bot:
        def __init__(self) -> None:
            self.removed: list[tuple[object, str]] = []

        def remove_listener(self, func, name: str) -> None:
            self.removed.append((func, name))

    bot = Bot()

    def original_register(_bot, _tree) -> None:
        panel._PANEL_VIEW_REGISTERED = True
        panel._PANEL_FALLBACK_LISTENER_REGISTERED = True

    guard._register_single_owner(panel, original_register, bot, object())

    assert bot.removed == [(listener, "on_interaction")]
    assert panel._PANEL_FALLBACK_LISTENER_REGISTERED is True
    assert panel._PANEL_FALLBACK_SUPPRESSED_BY_VIEW is True


def test_fallback_remains_when_persistent_view_registration_failed() -> None:
    listener = object()
    panel = SimpleNamespace(
        _PANEL_VIEW_REGISTERED=False,
        _PANEL_FALLBACK_LISTENER_REGISTERED=True,
        _component_fallback_listener=listener,
    )

    class Bot:
        removed: list[tuple[object, str]] = []

        def remove_listener(self, func, name: str) -> None:
            self.removed.append((func, name))

    bot = Bot()
    guard._register_single_owner(panel, lambda _bot, _tree: None, bot, object())
    assert bot.removed == []


def test_hardening_does_not_override_categories_or_ticket_numbers() -> None:
    hardening = HARDENING.read_text(encoding="utf-8")
    panel = PANEL.read_text(encoding="utf-8")

    forbidden_runtime_overrides = (
        "panel_mod._next_number =",
        "panel_mod._rows =",
        "panel_mod._load_rows =",
        "panel_mod._ticket_num =",
        "ticket_counters",
    )
    for marker in forbidden_runtime_overrides:
        assert marker not in hardening, f"stale clean-panel override returned: {marker}"

    assert "reserve_persistent_ticket_number" in panel
    assert "return await reserve_persistent_ticket_number" in panel
    assert "_INTERACTION_TTL_SECONDS" in hardening
    assert "_MENU_SESSION_SECONDS" not in hardening
    assert "int(member.id)" not in hardening
