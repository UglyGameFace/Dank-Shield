from __future__ import annotations

import asyncio

from stoney_verify.setup_ui import public_setup_guided_test as guided


def _state(**updates):
    state = {
        "tickets": False,
        "verification": False,
        "basic_verify": False,
        "voice_verify": False,
        "id_verify": False,
        "spam_guard": False,
        "logs": False,
        "completed": False,
    }
    state.update(updates)
    return state


def _children_by_id(view):
    return {
        str(getattr(item, "custom_id", "")): item
        for item in view.children
        if getattr(item, "custom_id", None)
    }


def test_next_pending_test_follows_enabled_setup_order() -> None:
    state = _state(tickets=True, basic_verify=True, spam_guard=True, logs=True)

    assert guided.next_pending_test_key(state, frozenset()) == "tickets"
    assert guided.next_pending_test_key(state, frozenset({"tickets"})) == "simple_verify"
    assert (
        guided.next_pending_test_key(
            state,
            frozenset({"tickets", "simple_verify", "spam_guard"}),
        )
        == "logs"
    )
    assert guided.next_pending_test_key(
        state,
        frozenset({"tickets", "simple_verify", "spam_guard", "logs"}),
    ) is None


def test_ready_setup_home_uses_obvious_guided_test_action() -> None:
    async def scenario() -> None:
        view = guided.GuidedSetupHomeView(ready=True, started=True, completed=False)
        items = _children_by_id(view)
        primary = items["dank_setup_home:continue"]
        assert primary.label == "Start Guided Test"
        assert str(primary.emoji) == "🧪"

    asyncio.run(scenario())


def test_test_center_uses_one_green_next_action_until_all_tests_pass() -> None:
    async def scenario() -> None:
        state = _state(tickets=True, basic_verify=True, spam_guard=True)
        first = guided.GuidedTestView(state, confirmed=frozenset())
        first_items = _children_by_id(first)
        assert first_items["dank_setup_guided_test:next"].label == "Start Next Test"
        assert "dank_setup_test:finish" not in first_items
        assert "dank_setup_test:check" not in first_items

        middle = guided.GuidedTestView(state, confirmed=frozenset({"tickets"}))
        middle_items = _children_by_id(middle)
        assert middle_items["dank_setup_guided_test:next"].label == "Continue Guided Test"
        assert "dank_setup_test:finish" not in middle_items

        complete = guided.GuidedTestView(
            state,
            confirmed=frozenset({"tickets", "simple_verify", "spam_guard"}),
        )
        complete_items = _children_by_id(complete)
        assert "dank_setup_guided_test:next" not in complete_items
        assert complete_items["dank_setup_test:finish"].label == "Finish Setup"
        assert complete_items["dank_setup_test:finish"].disabled is False

    asyncio.run(scenario())


def test_feature_test_marks_passed_and_continues_without_returning_to_list() -> None:
    async def scenario() -> None:
        state = _state(tickets=True, basic_verify=True)
        pending = guided.GuidedFeatureTestView(state, frozenset(), "tickets")
        pending_items = _children_by_id(pending)
        assert pending_items["dank_setup_test:mark"].label == "Mark Passed & Continue"
        assert "dank_setup_test:ticket_panel" in pending_items
        assert "dank_setup_test:create_ticket" in pending_items

        passed = guided.GuidedFeatureTestView(state, frozenset({"tickets"}), "tickets")
        passed_items = _children_by_id(passed)
        assert passed_items["dank_setup_test:mark"].label == "Continue to Next Test"

    asyncio.run(scenario())


def test_guided_module_reuses_compact_session_and_finish_owners() -> None:
    source = __import__(
        "pathlib"
    ).Path("stoney_verify/setup_ui/public_setup_guided_test.py").read_text(encoding="utf-8")

    assert "compact._save_test_session" in source
    assert "await compact._finish(interaction, self.confirmed)" in source
    assert "setup._open_test_launch = open_guided_tests" in source
    assert "Mark Passed & Continue" in source
