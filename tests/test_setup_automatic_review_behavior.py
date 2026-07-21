from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import (
    public_setup_recommend as recommend,
)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class FakeGuild:
    def __init__(self) -> None:
        self.id = 4040
        self.me = None


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = FakeGuild()
        self.user = SimpleNamespace(id=77)
        self.response = SimpleNamespace()


def button_labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
    }


def test_review_shows_fix_only_when_work_remains():
    view = recommend.SetupReviewView(ready=False)
    labels = button_labels(view)

    assert "Fix Next Problem" in labels
    assert "Test Your Setup" not in labels
    assert "All Features & Settings" not in labels
    assert "Change Setup Plan" not in labels
    assert "Setup Home" in labels


def test_review_shows_launch_only_when_ready():
    view = recommend.SetupReviewView(ready=True)
    labels = button_labels(view)

    assert "Test Your Setup" in labels
    assert "Fix Next Problem" not in labels
    assert "All Features & Settings" not in labels
    assert "Change Setup Plan" not in labels
    assert "Setup Home" in labels


def test_ready_dispatch_opens_review_not_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def health(*args: Any, **kwargs: Any) -> None:
        events.append("health")

    async def forbidden_launch(
        *args: Any,
        **kwargs: Any,
    ) -> None:
        raise AssertionError(
            "ready route bypassed automatic Setup Check"
        )

    monkeypatch.setattr(
        recommend,
        "_open_health_check",
        health,
    )
    monkeypatch.setattr(
        recommend,
        "_open_test_launch",
        forbidden_launch,
    )

    run(
        recommend._open_guided_target(
            FakeInteraction(),
            "ready",
            "ready",
        )
    )

    assert events == ["health"]


def test_finished_guided_flow_auto_opens_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[tuple[str, Any]] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def defer(*args: Any, **kwargs: Any) -> None:
        events.append(("defer", None))

    async def target(*args: Any, **kwargs: Any):
        return (
            "ready",
            "Setup Is Ready to Test",
            "Everything required is configured.",
            "ready",
        )

    async def forbidden_progress(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        raise AssertionError(
            "completed setup rendered another guided page"
        )

    async def health(
        interaction_arg: Any,
        **kwargs: Any,
    ) -> None:
        events.append(("health", kwargs))

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_safe_defer_update",
        defer,
    )
    monkeypatch.setattr(
        recommend,
        "_guided_setup_target",
        target,
    )
    monkeypatch.setattr(
        recommend,
        "_setup_progress",
        forbidden_progress,
    )
    monkeypatch.setattr(
        recommend,
        "_open_health_check",
        health,
    )

    run(
        recommend._open_guided_setup(
            interaction,
            saved_message="Saved the final item.",
        )
    )

    assert events == [
        ("defer", None),
        (
            "health",
            {
                "saved_message": "Saved the final item.",
                "already_deferred": True,
            },
        ),
    ]


def test_launch_is_blocked_until_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[str] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def target(*args: Any, **kwargs: Any):
        return (
            "channels",
            "Choose the Verification Channel",
            "Pick where members press Verify.",
            "verification_channel",
        )

    async def health(*args: Any, **kwargs: Any) -> None:
        events.append("health")

    async def forbidden_state(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        raise AssertionError(
            "launch state loaded before Setup Check passed"
        )

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        recommend,
        "_guided_setup_target",
        target,
    )
    monkeypatch.setattr(
        recommend,
        "_open_health_check",
        health,
    )
    monkeypatch.setattr(
        recommend,
        "_launch_state",
        forbidden_state,
    )

    run(recommend._open_test_launch(interaction))

    assert events == ["health"]


@pytest.mark.parametrize(
    ("target_name", "main_label", "hidden_label"),
    (
        ("ready", "Test Your Setup", "Fix Next Problem"),
        ("roles", "Fix Next Problem", "Test Your Setup"),
    ),
)
def test_health_check_builds_correct_review_view(
    monkeypatch: pytest.MonkeyPatch,
    target_name: str,
    main_label: str,
    hidden_label: str,
) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def defer(*args: Any, **kwargs: Any) -> None:
        captured["deferred"] = True

    async def build(*args: Any, **kwargs: Any):
        return discord.Embed(title="🩺 Setup Check")

    async def target(*args: Any, **kwargs: Any):
        if target_name == "ready":
            return (
                "ready",
                "Setup Is Ready to Test",
                "Everything is configured.",
                "ready",
            )

        return (
            "roles",
            "Choose the Ticket Staff Role",
            "Pick the ticket staff role.",
            "ticket_staff_role",
        )

    async def edit(
        interaction_arg: Any,
        *,
        embed: discord.Embed,
        view: discord.ui.View,
    ) -> None:
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(
        recommend.solid,
        "_require_setup_permission",
        allow,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_safe_defer_update",
        defer,
    )
    monkeypatch.setattr(
        recommend,
        "_build_plain_setup_health_embed",
        build,
    )
    monkeypatch.setattr(
        recommend,
        "_guided_setup_target",
        target,
    )
    monkeypatch.setattr(
        recommend.solid,
        "_edit_or_followup",
        edit,
    )

    run(
        recommend._open_health_check(
            interaction,
            saved_message="Automatic review opened.",
        )
    )

    labels = button_labels(captured["view"])

    assert captured["deferred"] is True
    assert isinstance(
        captured["view"],
        recommend.SetupReviewView,
    )
    assert main_label in labels
    assert hidden_label not in labels

    field_names = {
        field.name
        for field in captured["embed"].fields
    }
    assert "Last Step Finished" in field_names


def test_plain_health_uses_canonical_review_route():
    path = Path(
        "stoney_verify/commands_ext/"
        "public_setup_fresh_choice.py"
    )
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))

    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
        and node.name == "_open_plain_health"
    ]

    assert len(matches) == 1

    source = (
        ast.get_source_segment(text, matches[0])
        or ""
    )

    assert "recommend._open_health_check" in source
    assert "_build_plain_setup_health_embed" not in source
    assert "SetupHealthHelpView" not in source
