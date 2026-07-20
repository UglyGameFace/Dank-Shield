from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import config_history_ui as ui


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def button_labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    }


def find_button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [
        child
        for child in view.children
        if isinstance(child, discord.ui.Button)
        and str(getattr(child, "label", "") or "") == label
    ]
    assert len(matches) == 1
    return matches[0]


class FakeResponse:
    def is_done(self) -> bool:
        return False

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = SimpleNamespace(id=123, name="Test Guild")
        self.user = SimpleNamespace(id=77)
        self.response = FakeResponse()


def test_history_view_has_focused_backup_controls_and_mobile_rows() -> None:
    versions = [
        {
            "version_id": 8,
            "source": "manual_backup",
            "is_manual": True,
            "created_at": "2026-07-20T20:00:00+00:00",
        }
    ]
    view = ui.ConfigHistoryView(versions)

    assert button_labels(view) == {
        "Create Backup",
        "Refresh",
        "Back to Other Settings",
        "Back Home",
    }
    selects = [child for child in view.children if isinstance(child, discord.ui.Select)]
    assert len(selects) == 1
    assert selects[0].options[0].value == "8"

    rows: dict[int, int] = {}
    for child in view.children:
        row = int(getattr(child, "row", 0) or 0)
        rows[row] = rows.get(row, 0) + 1
    assert max(rows.values()) <= 2


def test_restore_confirmation_has_only_confirm_and_cancel_actions() -> None:
    view = ui.RestoreConfigConfirmView(8)

    assert button_labels(view) == {"Confirm Restore", "Cancel"}
    assert view.version_id == 8


def test_restore_this_version_opens_confirmation_without_restoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[tuple[str, int]] = []

    async def open_confirmation(interaction_arg: Any, version_id: int) -> None:
        assert interaction_arg is interaction
        events.append(("confirm", version_id))

    async def must_not_restore(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Restore service must not run before confirmation")

    monkeypatch.setattr(ui, "open_restore_confirmation", open_confirmation)
    monkeypatch.setattr(ui, "restore_config_version", must_not_restore)

    run(find_button(ui.ConfigVersionDetailView(8), "Restore This Version").callback(interaction))

    assert events == [("confirm", 8)]


def test_confirm_restore_calls_service_then_returns_to_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[Any] = []

    async def allow(_interaction: Any) -> bool:
        return True

    async def defer(_interaction: Any) -> None:
        events.append("defer")

    async def restore(
        guild_id: int,
        version_id: int,
        *,
        actor_id: int,
        reason: str,
    ) -> dict[str, Any]:
        events.append(("restore", guild_id, version_id, actor_id, reason))
        return {"restored_from_version_id": version_id}

    async def open_history(
        interaction_arg: Any,
        *,
        saved_message: str = "",
        already_deferred: bool = False,
    ) -> None:
        assert interaction_arg is interaction
        events.append(("history", saved_message, already_deferred))

    monkeypatch.setattr(ui, "_require_setup_permission", allow)
    monkeypatch.setattr(ui, "_safe_defer_update", defer)
    monkeypatch.setattr(ui, "restore_config_version", restore)
    monkeypatch.setattr(ui, "open_config_history", open_history)

    run(find_button(ui.RestoreConfigConfirmView(8), "Confirm Restore").callback(interaction))

    assert events[0] == "defer"
    assert events[1][0:4] == ("restore", 123, 8, 77)
    assert "Owner confirmed restore" in events[1][4]
    assert events[2][0] == "history"
    assert "Restored configuration version **#8**" in events[2][1]
    assert events[2][2] is True


def test_open_restore_confirmation_is_non_destructive_until_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(_interaction: Any) -> bool:
        return True

    async def defer(_interaction: Any) -> None:
        captured["deferred"] = True

    async def edit(
        interaction_arg: Any,
        *,
        embed: discord.Embed,
        view: discord.ui.View,
    ) -> None:
        assert interaction_arg is interaction
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(ui, "_require_setup_permission", allow)
    monkeypatch.setattr(ui, "_safe_defer_update", defer)
    monkeypatch.setattr(ui, "_edit", edit)

    run(ui.open_restore_confirmation(interaction, 8))

    assert captured["deferred"] is True
    assert captured["embed"].title == "⚠️ Confirm Configuration Restore"
    assert "No Discord roles or channels are deleted" in captured["embed"].description
    assert isinstance(captured["view"], ui.RestoreConfigConfirmView)
    assert button_labels(captured["view"]) == {"Confirm Restore", "Cancel"}
