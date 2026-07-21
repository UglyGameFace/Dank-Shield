from __future__ import annotations

from typing import Any

import pytest

from stoney_verify import config_history as history
from stoney_verify import config_history_selective as selective


class FakeResponse:
    def __init__(self, data: list[dict[str, Any]] | None = None) -> None:
        self.data = data or []


class FakeUpdateQuery:
    def __init__(self, recorder: dict[str, Any]) -> None:
        self.recorder = recorder

    def update(self, payload: dict[str, Any]) -> "FakeUpdateQuery":
        self.recorder["update_payload"] = dict(payload)
        return self

    def eq(self, key: str, value: Any) -> "FakeUpdateQuery":
        self.recorder.setdefault("filters", []).append((key, value))
        return self

    def execute(self) -> FakeResponse:
        return FakeResponse(
            [
                {
                    "guild_id": "123",
                    "ticket_prefix": "current-prefix",
                    "settings": {"spam_guard_enabled": True},
                    **dict(self.recorder.get("update_payload") or {}),
                }
            ]
        )


class FakeRpcCall:
    def __init__(self, recorder: dict[str, Any], name: str, params: dict[str, Any]) -> None:
        self.recorder = recorder
        self.name = name
        self.params = dict(params)

    def execute(self) -> FakeResponse:
        self.recorder["rpc_name"] = self.name
        self.recorder["rpc_params"] = dict(self.params)
        return FakeResponse([])


class FakeSupabase:
    def __init__(self, recorder: dict[str, Any]) -> None:
        self.recorder = recorder

    def table(self, _name: str) -> FakeUpdateQuery:
        return FakeUpdateQuery(self.recorder)

    def rpc(self, name: str, params: dict[str, Any]) -> FakeRpcCall:
        return FakeRpcCall(self.recorder, name, params)


def test_scoped_backup_can_save_only_core_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inserted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        history,
        "_fetch_current_config_row_sync",
        lambda guild_id: (
            "guild_configs",
            {"guild_id": str(guild_id), "settings": {"spam_guard_enabled": True}},
        ),
    )

    def must_not_fetch_tickets(_guild_id: int) -> tuple[bool, list[dict[str, Any]]]:
        raise AssertionError("Ticket Choices must not be read for a Core-only backup")

    monkeypatch.setattr(history, "_fetch_ticket_categories_state_sync", must_not_fetch_tickets)

    def insert(
        guild_id: int,
        snapshot: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        row = {
            "version_id": 41,
            "guild_id": str(guild_id),
            "snapshot": dict(snapshot),
            **kwargs,
        }
        inserted.append(row)
        return row

    monkeypatch.setattr(history, "_insert_snapshot_sync", insert)

    result = selective.create_scoped_manual_backup_sync(
        123,
        domains=[selective.CORE_DOMAIN],
        actor_id=77,
    )

    assert result["selected_domains"] == [selective.CORE_DOMAIN]
    assert len(result["backup_versions"]) == 1
    assert result["ticket_categories_version"] is None
    assert inserted[0]["config_table"] == "guild_configs"
    assert inserted[0]["mode"] == "selected_domains"


def test_scoped_backup_rejects_empty_selection() -> None:
    with pytest.raises(ValueError, match="Choose Core Settings"):
        selective.create_scoped_manual_backup_sync(123, domains=[])


def test_core_restore_plan_explains_changed_and_missing_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {
        "guild_id": "123",
        "ticket_prefix": "new-prefix",
        "settings": {
            "spam_guard_enabled": True,
            "welcome_title": "",
        },
    }
    version = {
        "version_id": 8,
        "config_table": "guild_configs",
        "snapshot": {
            "guild_id": "123",
            "ticket_prefix": "old-prefix",
            "settings": {
                "spam_guard_enabled": False,
                "welcome_title": "Welcome home",
                "antinuke_enabled": True,
            },
        },
    }

    monkeypatch.setattr(
        history,
        "_fetch_current_config_row_sync",
        lambda _guild_id: ("guild_configs", current),
    )
    monkeypatch.setattr(history, "_fetch_version_sync", lambda *_args, **_kwargs: version)

    plan = selective.plan_selective_restore_sync(123, 8)

    assert plan["domain"] == selective.CORE_DOMAIN
    assert plan["changed_items"] == [
        "antinuke_enabled",
        "spam_guard_enabled",
        "ticket_prefix",
        "welcome_title",
    ]
    assert plan["missing_items"] == ["antinuke_enabled", "welcome_title"]
    assert "Protection & Moderation" in plan["core_sections"]
    assert plan["item_labels"]["ticket_prefix"] == "Ticket Prefix"


def test_selective_core_restore_changes_only_requested_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {
        "guild_id": "123",
        "ticket_prefix": "keep-new-prefix",
        "settings": {
            "spam_guard_enabled": True,
            "welcome_title": "Current title",
        },
    }
    version = {
        "version_id": 8,
        "config_table": "guild_configs",
        "snapshot": {
            "guild_id": "123",
            "ticket_prefix": "old-prefix",
            "settings": {
                "spam_guard_enabled": False,
                "welcome_title": "Saved title",
            },
        },
    }
    recorder: dict[str, Any] = {}
    backups: list[dict[str, Any]] = []

    monkeypatch.setattr(
        history,
        "_fetch_current_config_row_sync",
        lambda _guild_id: ("guild_configs", current),
    )
    monkeypatch.setattr(history, "_fetch_version_sync", lambda *_args, **_kwargs: version)
    monkeypatch.setattr(history, "_require_supabase", lambda: FakeSupabase(recorder))
    monkeypatch.setattr(
        history,
        "_insert_snapshot_sync",
        lambda *args, **kwargs: backups.append({"args": args, "kwargs": kwargs})
        or {"version_id": 99},
    )
    monkeypatch.setattr(selective, "clear_guild_config_cache", lambda _guild_id: None)

    result = selective.restore_config_version_selective_sync(
        123,
        8,
        mode=selective.RESTORE_SELECTED,
        selected_items=["welcome_title"],
        actor_id=77,
    )

    payload = recorder["update_payload"]
    assert "ticket_prefix" not in payload
    assert payload["settings"]["welcome_title"] == "Saved title"
    assert payload["settings"]["spam_guard_enabled"] is True
    assert result["restored_items"] == ["welcome_title"]
    assert len(backups) == 1
    assert backups[0]["kwargs"]["source"] == "pre_restore_backup"


def test_missing_only_core_restore_does_not_overwrite_existing_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {
        "guild_id": "123",
        "settings": {"spam_guard_enabled": True},
    }
    version = {
        "version_id": 8,
        "config_table": "guild_configs",
        "snapshot": {
            "guild_id": "123",
            "settings": {
                "spam_guard_enabled": False,
                "antinuke_enabled": True,
            },
        },
    }
    recorder: dict[str, Any] = {}

    monkeypatch.setattr(
        history,
        "_fetch_current_config_row_sync",
        lambda _guild_id: ("guild_configs", current),
    )
    monkeypatch.setattr(history, "_fetch_version_sync", lambda *_args, **_kwargs: version)
    monkeypatch.setattr(history, "_require_supabase", lambda: FakeSupabase(recorder))
    monkeypatch.setattr(history, "_insert_snapshot_sync", lambda *args, **kwargs: {"version_id": 99})
    monkeypatch.setattr(selective, "clear_guild_config_cache", lambda _guild_id: None)

    result = selective.restore_config_version_selective_sync(
        123,
        8,
        mode=selective.RESTORE_MISSING,
    )

    payload = recorder["update_payload"]["settings"]
    assert payload["antinuke_enabled"] is True
    assert payload["spam_guard_enabled"] is True
    assert result["restored_items"] == ["antinuke_enabled"]


def test_selective_ticket_restore_preserves_unselected_current_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = {
        "version_id": 12,
        "config_table": history.TICKET_CATEGORIES_TABLE,
        "snapshot": {
            "guild_id": "123",
            "rows": [
                {"guild_id": "123", "slug": "support", "name": "Saved Support", "sort_order": 10},
                {"guild_id": "123", "slug": "partnerships", "name": "Partnerships", "sort_order": 20},
            ],
        },
    }
    current_rows = [
        {"guild_id": "123", "slug": "support", "name": "Current Support", "sort_order": 10},
        {"guild_id": "123", "slug": "billing", "name": "Billing", "sort_order": 15},
    ]
    restored_rows = [
        {"guild_id": "123", "slug": "support", "name": "Current Support", "sort_order": 10},
        {"guild_id": "123", "slug": "billing", "name": "Billing", "sort_order": 15},
        {"guild_id": "123", "slug": "partnerships", "name": "Partnerships", "sort_order": 20},
    ]
    fetches = iter(
        [
            (True, current_rows),
            (True, current_rows),
            (True, restored_rows),
        ]
    )
    recorder: dict[str, Any] = {}
    inserted: list[dict[str, Any]] = []

    monkeypatch.setattr(
        history,
        "_fetch_current_config_row_sync",
        lambda _guild_id: ("guild_configs", {"guild_id": "123"}),
    )
    monkeypatch.setattr(history, "_fetch_version_sync", lambda *_args, **_kwargs: version)
    monkeypatch.setattr(
        history,
        "_fetch_ticket_categories_state_sync",
        lambda _guild_id: next(fetches),
    )
    monkeypatch.setattr(history, "_require_supabase", lambda: FakeSupabase(recorder))
    monkeypatch.setattr(
        history,
        "_insert_snapshot_sync",
        lambda *args, **kwargs: inserted.append({"args": args, "kwargs": kwargs})
        or {"version_id": 90 + len(inserted)},
    )

    result = selective.restore_config_version_selective_sync(
        123,
        12,
        mode=selective.RESTORE_SELECTED,
        selected_items=["partnerships"],
    )

    rpc_rows = recorder["rpc_params"]["p_rows"]
    by_slug = {row["slug"]: row for row in rpc_rows}
    assert by_slug["support"]["name"] == "Current Support"
    assert by_slug["billing"]["name"] == "Billing"
    assert by_slug["partnerships"]["name"] == "Partnerships"
    assert result["restored_items"] == ["partnerships"]
    assert len(inserted) == 2


def test_selective_restore_refuses_cross_guild_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        history,
        "_fetch_current_config_row_sync",
        lambda _guild_id: ("guild_configs", {"guild_id": "123"}),
    )
    monkeypatch.setattr(
        history,
        "_fetch_version_sync",
        lambda *_args, **_kwargs: {
            "version_id": 8,
            "config_table": "guild_configs",
            "snapshot": {"guild_id": "999", "settings": {"spam_guard_enabled": True}},
        },
    )

    with pytest.raises(RuntimeError, match="different guild"):
        selective.plan_selective_restore_sync(123, 8)
