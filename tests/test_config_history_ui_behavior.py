from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import config_history_ui as ui
from stoney_verify.config_history_selective import (
    CORE_DOMAIN,
    RESTORE_ALL,
    RESTORE_MISSING,
    RESTORE_SELECTED,
    TICKET_CHOICES_DOMAIN,
)


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
    def __init__(self) -> None:
        self.messages: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.edits: list[dict[str, Any]] = []

    def is_done(self) -> bool:
        return False

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        self.messages.append((args, kwargs))

    async def edit_message(self, **kwargs: Any) -> None:
        self.edits.append(dict(kwargs))


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = SimpleNamespace(id=123, name="Test Guild")
        self.user = SimpleNamespace(id=77)
        self.response = FakeResponse()


def core_plan() -> dict[str, Any]:
    return {
        "guild_id": "123",
        "version_id": 8,
        "config_table": "guild_configs",
        "domain": CORE_DOMAIN,
        "domain_label": "Core Settings",
        "changed_items": [
            "antinuke_enabled",
            "spam_guard_enabled",
            "ticket_prefix",
            "welcome_title",
        ],
        "missing_items": ["antinuke_enabled", "welcome_title"],
        "item_labels": {
            "antinuke_enabled": "AntiNuke Enabled",
            "spam_guard_enabled": "Spam Guard Enabled",
            "ticket_prefix": "Ticket Prefix",
            "welcome_title": "Welcome Title",
        },
        "saved_count": 30,
        "current_count": 28,
        "core_sections": {
            "Protection & Moderation": [
                "antinuke_enabled",
                "spam_guard_enabled",
            ],
            "Timers & Rules": ["ticket_prefix"],
            "Welcome & Member Experience": ["welcome_title"],
        },
    }


def ticket_plan() -> dict[str, Any]:
    return {
        "guild_id": "123",
        "version_id": 12,
        "config_table": "ticket_categories",
        "domain": TICKET_CHOICES_DOMAIN,
        "domain_label": "Ticket Choices",
        "changed_items": ["billing", "partnerships", "support"],
        "missing_items": ["partnerships"],
        "item_labels": {
            "billing": "Billing",
            "partnerships": "Partnerships",
            "support": "Support",
        },
        "saved_count": 15,
        "current_count": 14,
        "core_sections": {},
    }


def test_history_explains_exactly_what_is_and_is_not_backed_up() -> None:
    embed = ui._history_embed(SimpleNamespace(id=123), [])
    rendered = "\n".join(
        [str(embed.description or "")]
        + [str(field.name) + "\n" + str(field.value) for field in embed.fields]
    )

    assert "Dank Shield's configuration" in rendered
    assert "Core Settings" in rendered
    assert "Ticket Choices" in rendered
    assert "Discord messages" in rendered
    assert "actual roles/channels/categories" in rendered
    assert "does not clone" in rendered


def test_history_view_has_focused_controls_and_mobile_rows() -> None:
    versions = [
        {
            "version_id": 8,
            "config_table": "guild_configs",
            "source": "manual_backup",
            "is_manual": True,
            "created_at": "2026-07-20T20:00:00+00:00",
        }
    ]
    view = ui.ConfigHistoryView(versions)

    assert button_labels(view) == {
        "Choose Backup Contents",
        "Refresh",
        "Back to Other Settings",
        "Back Home",
    }
    selects = [child for child in view.children if isinstance(child, discord.ui.Select)]
    assert len(selects) == 1
    assert selects[0].options[0].value == "8"
    assert "Core Settings" in selects[0].options[0].label

    rows: dict[int, int] = {}
    for child in view.children:
        row = int(getattr(child, "row", 0) or 0)
        rows[row] = rows.get(row, 0) + 1
    assert max(rows.values()) <= 2


def test_backup_contents_defaults_to_both_domains_and_explains_them() -> None:
    view = ui.BackupContentsView()
    selects = [child for child in view.children if isinstance(child, ui.BackupDomainSelect)]
    assert len(selects) == 1
    assert view.selected_domains == {CORE_DOMAIN, TICKET_CHOICES_DOMAIN}
    assert {option.value for option in selects[0].options} == {
        CORE_DOMAIN,
        TICKET_CHOICES_DOMAIN,
    }
    assert all(option.default for option in selects[0].options)

    embed = ui._backup_contents_embed(view.selected_domains)
    rendered = "\n".join(str(field.value) for field in embed.fields)
    assert "protection rules" in rendered
    assert "category-specific form configuration" in rendered
    assert "does not change any setting" in str(embed.description)


def test_create_selected_backup_passes_only_chosen_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[Any] = []

    async def allow(_interaction: Any) -> bool:
        return True

    async def defer(_interaction: Any) -> None:
        events.append("defer")

    async def create(
        guild_id: int,
        *,
        domains: list[str],
        actor_id: int,
        reason: str,
    ) -> dict[str, Any]:
        events.append(("create", guild_id, domains, actor_id, reason))
        return {
            "backup_versions": [
                {
                    "version_id": 44,
                    "config_table": "ticket_categories",
                }
            ]
        }

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
    monkeypatch.setattr(ui, "create_scoped_manual_backup", create)
    monkeypatch.setattr(ui, "open_config_history", open_history)

    view = ui.BackupContentsView([TICKET_CHOICES_DOMAIN])
    run(find_button(view, "Create Selected Backup").callback(interaction))

    assert events[0] == "defer"
    assert events[1][0:4] == (
        "create",
        123,
        [TICKET_CHOICES_DOMAIN],
        77,
    )
    assert events[2][0] == "history"
    assert "Ticket Choices #44" in events[2][1]
    assert events[2][2] is True


def test_version_detail_explains_contents_and_three_restore_modes() -> None:
    version = {
        "version_id": 8,
        "config_table": "guild_configs",
        "source": "manual_backup",
        "is_manual": True,
        "created_at": "2026-07-20T20:00:00+00:00",
    }
    plan = core_plan()
    embed = ui._version_detail_embed(version, plan)
    rendered = "\n".join(
        [str(embed.description or "")]
        + [str(field.name) + "\n" + str(field.value) for field in embed.fields]
    )

    assert "30 saved Core Setting" in rendered
    assert "Protection & Moderation" in rendered
    assert "2 item(s)" in rendered
    assert "Missing Only" in rendered
    assert "Choose Exact Changes" in rendered
    assert "All Differences" in rendered
    assert "unselected newer setting" in rendered

    view = ui.ConfigVersionDetailView(8, plan)
    assert button_labels(view) == {
        "Restore Missing Only",
        "Choose Exact Changes",
        "Restore All Differences",
        "Back to History",
        "Back to Other Settings",
    }
    assert not find_button(view, "Restore Missing Only").disabled
    assert not find_button(view, "Choose Exact Changes").disabled


def test_missing_only_button_opens_preview_without_restoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    interaction = FakeInteraction()
    events: list[Any] = []

    async def open_confirmation(
        interaction_arg: Any,
        version_id: int,
        **kwargs: Any,
    ) -> None:
        assert interaction_arg is interaction
        events.append((version_id, kwargs))

    async def must_not_restore(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Restore service must not run before confirmation")

    monkeypatch.setattr(ui, "open_restore_confirmation", open_confirmation)
    monkeypatch.setattr(ui, "restore_config_version_selective", must_not_restore)

    run(
        find_button(
            ui.ConfigVersionDetailView(8, core_plan()),
            "Restore Missing Only",
        ).callback(interaction)
    )

    assert events == [
        (
            8,
            {
                "mode": RESTORE_MISSING,
                "plan": core_plan(),
            },
        )
    ]


def test_exact_change_picker_tracks_individual_selections() -> None:
    plan = ticket_plan()
    view = ui.SelectiveRestorePickerView(
        12,
        plan,
        selected_items=["partnerships"],
    )

    assert view.selected_items == {"partnerships"}
    selects = [child for child in view.children if isinstance(child, ui.RestoreItemSelect)]
    assert len(selects) == 1
    defaults = {option.value for option in selects[0].options if option.default}
    assert defaults == {"partnerships"}
    assert button_labels(view) == {
        "Previous",
        "Next",
        "Review Selected",
        "Back to Version",
    }


def test_picker_paginates_more_than_twenty_changes() -> None:
    plan = core_plan()
    plan["changed_items"] = [f"setting_{index}" for index in range(45)]
    plan["missing_items"] = []
    plan["item_labels"] = {
        f"setting_{index}": f"Setting {index}" for index in range(45)
    }

    first = ui.SelectiveRestorePickerView(8, plan, page=0)
    middle = ui.SelectiveRestorePickerView(8, plan, page=1)
    last = ui.SelectiveRestorePickerView(8, plan, page=2)

    assert first.previous.disabled
    assert not first.next_page.disabled
    assert not middle.previous.disabled
    assert not middle.next_page.disabled
    assert not last.previous.disabled
    assert last.next_page.disabled


def test_confirmation_lists_only_selected_items() -> None:
    version = {
        "version_id": 12,
        "config_table": "ticket_categories",
    }
    embed = ui._confirmation_embed(
        version,
        ticket_plan(),
        mode=RESTORE_SELECTED,
        selected_items=["partnerships"],
    )
    rendered = "\n".join(
        [str(embed.description or "")]
        + [str(field.name) + "\n" + str(field.value) for field in embed.fields]
    )

    assert "1 Ticket Choices item" in rendered
    assert "Partnerships" in rendered
    assert "Billing" not in rendered
    assert "Every unlisted current setting or ticket choice" in rendered


def test_confirm_restore_calls_selective_service_only_after_confirmation(
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
        mode: str,
        selected_items: list[str],
        actor_id: int,
        reason: str,
    ) -> dict[str, Any]:
        events.append(
            (
                "restore",
                guild_id,
                version_id,
                mode,
                selected_items,
                actor_id,
                reason,
            )
        )
        return {
            "restored_from_version_id": version_id,
            "config_table": "guild_configs",
            "restored_item_count": len(selected_items),
        }

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
    monkeypatch.setattr(ui, "restore_config_version_selective", restore)
    monkeypatch.setattr(ui, "open_config_history", open_history)

    view = ui.RestoreConfigConfirmView(
        8,
        mode=RESTORE_SELECTED,
        selected_items=["welcome_title"],
        plan=core_plan(),
    )
    run(find_button(view, "Confirm Restore").callback(interaction))

    assert events[0] == "defer"
    assert events[1][0:6] == (
        "restore",
        123,
        8,
        RESTORE_SELECTED,
        ["welcome_title"],
        77,
    )
    assert "Exact Selected Changes" in events[1][6]
    assert events[2][0] == "history"
    assert "1 Core Settings item" in events[2][1]
    assert events[2][2] is True


def test_restore_confirmation_has_only_confirm_and_cancel_actions() -> None:
    view = ui.RestoreConfigConfirmView(
        8,
        mode=RESTORE_ALL,
        plan=core_plan(),
    )
    assert button_labels(view) == {"Confirm Restore", "Cancel"}
    assert view.version_id == 8
