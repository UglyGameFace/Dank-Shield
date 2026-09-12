from __future__ import annotations

import discord

from stoney_verify import config_history_ui, profile_card_setup_ui, welcome_setup_ui
from stoney_verify.commands_ext import public_setup_compact as runtime
from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.setup_ui import public_setup_guided_test as guided
from stoney_verify.startup_guards import ticket_category_setup_guard as category_guard
from stoney_verify.tickets_new import managed_category_service as category_service


# Importing the deterministic runtime is part of the contract under test.
runtime.apply_public_setup_runtime()


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def _custom_ids(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(item, "custom_id", ""))
        for item in view.children
        if getattr(item, "custom_id", None)
    ]


def _assert_unique_ids(view: discord.ui.View) -> None:
    custom_ids = _custom_ids(view)
    assert len(custom_ids) == len(set(custom_ids)), type(view).__name__


def _assert_navigation(view: discord.ui.View, *required: str) -> None:
    labels = _labels(view)
    for label in required:
        assert label in labels, (type(view).__name__, label, sorted(labels))
    _assert_unique_ids(view)


def test_setup_root_plan_and_review_navigation_contract() -> None:
    plan = fresh.SetupTypeChoiceView(guild=None)
    home = recommend.ProductSetupHomeView(ready=True, started=True, completed=False)
    review = recommend.SetupReviewView(ready=True)

    _assert_navigation(plan, "Setup Home", "Close")
    assert "Back" not in _labels(plan)

    _assert_navigation(home, "Start Guided Test", "Change Plan", "Close")
    assert "Setup Home" not in _labels(home)

    _assert_navigation(review, "Start Guided Test", "Setup Home", "Close")


def test_guided_test_navigation_contract() -> None:
    state = {
        "tickets": True,
        "verification": True,
        "basic_verify": True,
        "voice_verify": False,
        "id_verify": False,
        "spam_guard": True,
        "logs": True,
        "completed": False,
    }
    checklist = guided.GuidedTestView(state)
    feature = guided.GuidedFeatureTestView(state, frozenset(), "tickets")

    _assert_navigation(checklist, "Setup Home", "Close")
    _assert_navigation(feature, "Back to Test List", "Close")
    assert "Setup Home" not in _labels(feature)


def test_all_feature_sections_keep_predictable_exit_navigation() -> None:
    section_views = (
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedVerificationView(),
        recommend.AdvancedSecurityView(),
        recommend.AdvancedLogsActivityView(),
        recommend.AdvancedAppearanceView(),
    )

    for view in section_views:
        _assert_navigation(view, "Back to All Features", "Setup Home", "Close")


def test_ticket_choice_manager_keeps_home_and_close_without_duplicate_ids() -> None:
    state = category_service.CategorySetupState(
        rows=[],
        active_rows=[],
        selected_keys=(),
        required=True,
        reason="Confirm ticket choices.",
        version=0,
    )
    view = category_guard.CategorySetupManagerView(state=state)

    _assert_navigation(view, "Setup Home", "Close")
    assert "Back to All Features" not in _labels(view)


def test_welcome_profile_and_history_keep_setup_navigation() -> None:
    welcome = welcome_setup_ui.WelcomeSetupView(owner_id=7, config={})
    profile = profile_card_setup_ui.ProfileCardSetupView(owner_id=7, config={})
    history = config_history_ui.ConfigHistoryView([])

    for view in (welcome, profile, history):
        _assert_navigation(view, "Back to All Features", "Setup Home", "Close")
