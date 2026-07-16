from __future__ import annotations

import asyncio
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import (
    public_setup_recommend as recommend,
)
from stoney_verify.startup_guards import (
    setup_service_modes as modes,
)


NAVIGATION = {
    "Continue Guided Setup": "_open_guided_setup",
    "Setup Check": "_open_health_check",
    "Advanced Options": "_open_manage_setup",
    "Setup Home": "_home_edit",
}


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
    }


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


def service_state() -> modes.ServiceState:
    return modes.ServiceState(
        tickets=False,
        verification=False,
        voice=False,
        spamguard=False,
        moderation=False,
    )


def test_service_pages_own_canonical_navigation() -> None:
    for view in (
        modes.ServiceModeView(service_state()),
        modes.SpamGuardSetupView(),
    ):
        assert set(NAVIGATION) <= labels(view)

        nav_buttons = [
            child
            for child in view.children
            if str(
                getattr(
                    child,
                    "custom_id",
                    "",
                )
                or ""
            ).startswith(
                "dank_setup_service_nav:"
            )
        ]

        assert len(nav_buttons) == 4
        assert {
            int(getattr(child, "row", -1))
            for child in nav_buttons
        } == {4}


@pytest.mark.parametrize(
    ("label", "route_name"),
    tuple(NAVIGATION.items()),
)
def test_native_navigation_calls_canonical_route(
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    route_name: str,
) -> None:
    events: list[str] = []

    async def route(
        interaction: Any,
    ) -> None:
        events.append(route_name)

    monkeypatch.setattr(
        recommend,
        route_name,
        route,
    )

    button = find_button(
        modes.ServiceModeView(service_state()),
        label,
    )

    run(button.callback(object()))

    assert events == [route_name]
