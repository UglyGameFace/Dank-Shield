from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify.commands_ext import public_setup_compact as runtime
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.setup_ui import public_setup_compact as compact
from stoney_verify.setup_ui import public_setup_guided_test as guided
from stoney_verify.startup_guards import ticket_category_setup_guard as category_guard


def _custom_ids(view) -> list[str]:
    return [
        str(item.custom_id)
        for item in view.children
        if getattr(item, "custom_id", None)
    ]


def test_final_setup_runtime_has_one_deterministic_owner() -> None:
    runtime.apply_public_setup_runtime()
    runtime.apply_public_setup_runtime()

    assert recommend.ProductSetupHomeView is guided.GuidedSetupHomeView
    assert recommend.SetupReviewView is guided.GuidedReviewView
    assert recommend.LaunchTestView is guided.GuidedTestView
    assert recommend._open_test_launch is guided.open_guided_tests

    assert compact._ORIGINAL_CATEGORY_PAYLOAD is category_guard._build_category_manager_payload
    assert recommend.solid._category_load is category_guard._setup_category_load
    assert (
        recommend.solid._seed_recommended_categories
        is category_guard._seed_catalog_without_enabling_everything
    )
    assert recommend.solid._build_category_manager_payload is compact._category_payload

    # A later normal import/apply of the category service must not steal the
    # presentation callback back from the final runtime owner.
    assert category_guard.apply() is True
    assert recommend.solid._build_category_manager_payload is compact._category_payload


def test_guided_wording_is_the_final_ready_state() -> None:
    home = recommend.ProductSetupHomeView(ready=True, started=True, completed=False)
    review = recommend.SetupReviewView(ready=True)

    home_labels = {str(getattr(item, "label", "")) for item in home.children}
    review_labels = {str(getattr(item, "label", "")) for item in review.children}

    assert "Start Guided Test" in home_labels
    assert "Start Guided Test" in review_labels
    assert "Test Features" not in home_labels
    assert "Test Features" not in review_labels


def test_active_setup_views_do_not_duplicate_custom_ids() -> None:
    views = [
        recommend.ProductSetupHomeView(ready=True, started=True, completed=False),
        compact.CompactManagerView(),
        recommend.SetupReviewView(ready=True),
        recommend.LaunchTestView(
            {
                "tickets": True,
                "verification": True,
                "basic_verify": True,
                "voice_verify": True,
                "id_verify": False,
                "spam_guard": True,
                "logs": True,
                "completed": False,
            }
        ),
        guided.GuidedFeatureTestView(
            {"tickets": True, "completed": False},
            frozenset(),
            "tickets",
        ),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedVerificationView(),
        recommend.AdvancedSecurityView(),
        recommend.AdvancedLogsActivityView(),
        recommend.AdvancedAppearanceView(),
        recommend.AdvancedDangerZoneView(),
    ]

    for view in views:
        custom_ids = _custom_ids(view)
        assert len(custom_ids) == len(set(custom_ids)), type(view).__name__


def test_active_slow_routes_are_acknowledgement_owned() -> None:
    assert compact._open_manager.__name__ == "_ack_then_open_manager"
    assert compact.CompactReviewView._next.__name__ == "_ack_then_review_next"
    assert recommend._open_guided_target.__name__ == "_ack_then_open_guided_target"
    assert recommend._open_timers_behavior.__name__ == "_ack_then_open_timers_behavior"
    assert recommend._open_protection_options.__name__ == "_ack_then_open_protection_options"


class _Response:
    def __init__(self, *, done: bool = True) -> None:
        self.done = done
        self.sent: list[tuple[tuple, dict]] = []

    def is_done(self) -> bool:
        return self.done

    async def edit_message(self, **kwargs) -> None:
        raise RuntimeError("response edit failed")

    async def send_message(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))
        self.done = True

    async def defer(self, **kwargs) -> None:
        self.done = True


class _Followup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send(self, *args, **kwargs) -> None:
        self.sent.append((args, kwargs))


class _Message:
    def __init__(self, *, fail: bool) -> None:
        self.id = 404
        self.fail = fail
        self.edits: list[dict] = []

    async def edit(self, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("message edit failed")
        self.edits.append(kwargs)


class _Interaction:
    def __init__(self, *, message_fail: bool) -> None:
        self.id = 101
        self.guild = SimpleNamespace(id=202)
        self.user = SimpleNamespace(id=303)
        self.data = {"custom_id": "dank_setup_test:example"}
        self.response = _Response(done=True)
        self.followup = _Followup()
        self.message = _Message(fail=message_fail)
        self.original_edits: list[dict] = []

    async def edit_original_response(self, **kwargs) -> None:
        self.original_edits.append(kwargs)
        raise RuntimeError("original response edit failed")


def test_component_edit_failure_recovers_on_exact_clicked_message() -> None:
    interaction = _Interaction(message_fail=False)
    embed = SimpleNamespace()
    view = SimpleNamespace()

    asyncio.run(recommend.solid._edit_or_followup(interaction, embed=embed, view=view))

    assert interaction.message.edits == [{"embed": embed, "view": view}]
    assert interaction.followup.sent == []


def test_component_double_edit_failure_blocks_panel_fork() -> None:
    interaction = _Interaction(message_fail=True)

    asyncio.run(
        recommend.solid._edit_or_followup(
            interaction,
            embed=SimpleNamespace(),
            view=SimpleNamespace(),
        )
    )

    assert len(interaction.followup.sent) == 1
    args, kwargs = interaction.followup.sent[0]
    assert "No second setup panel was opened" in str(args[0])
    assert kwargs == {"ephemeral": True}
