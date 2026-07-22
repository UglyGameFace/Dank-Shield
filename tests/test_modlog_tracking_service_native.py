from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import modlog_tracking_service as service
from stoney_verify.commands_ext import (
    public_setup_recommend as recommend,
)


ROOT = Path(__file__).resolve().parents[1]

MODULE_PATH = (
    ROOT
    / "stoney_verify"
    / "modlog_tracking_service.py"
)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def find_button(
    view: discord.ui.View,
    label: str,
) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if str(getattr(child, "label", "") or "")
        == label
    ]

    assert len(matches) == 1
    assert isinstance(
        matches[0],
        discord.ui.Button,
    )

    return matches[0]


def test_service_is_a_normal_non_patching_module() -> None:
    source = MODULE_PATH.read_text(
        encoding="utf-8",
    )
    tree = ast.parse(
        source,
        filename=str(MODULE_PATH),
    )

    top_level_calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
    ]

    assert top_level_calls == []

    for forbidden in (
        "_PATCHED",
        "_patch_feature_button",
        "FeatureCentersView",
        "setup_smart_home_menu_guard",
    ):
        assert forbidden not in source


def test_saved_empty_selection_stays_all_off() -> None:
    assert service._saved({}) == service.DEFAULT_ON
    assert service._saved(
        {
            service.KEY: [],
        }
    ) == set()
    assert service._saved(
        {
            service.KEY: [
                "voice",
                "messages",
            ],
        }
    ) == {
        "voice",
        "messages",
    }


def test_tracking_view_exposes_every_control() -> None:
    view = service.ModlogTrackingView(
        SimpleNamespace(id=123),
        set(),
    )

    control_labels = {
        str(getattr(child, "label", "") or "")
        for child in view.children
    }

    assert {
        "All On",
        "All Off",
        "Health",
        "Send Test",
        "Back",
    } <= control_labels

    assert len(view.children) == (
        len(service.CATEGORIES) + 5
    )


def test_back_button_returns_to_logs_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def open_logs(
        interaction: Any,
    ) -> None:
        events.append("logs")

    monkeypatch.setattr(
        recommend,
        "_open_advanced_logs_activity",
        open_logs,
    )

    view = service.ModlogTrackingView(
        SimpleNamespace(id=123),
        set(),
    )
    button = find_button(
        view,
        "Back",
    )

    run(button.callback(object()))

    assert events == ["logs"]
