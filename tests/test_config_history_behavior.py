from __future__ import annotations

from typing import Any

import pytest

from stoney_verify import config_history


class FakeResponse:
    def __init__(self, data: list[dict[str, Any]] | None = None) -> None:
        self.data = data or []


class FakeUpdateQuery:
    def __init__(self, table_name: str, recorder: dict[str, Any]) -> None:
        self.table_name = table_name
        self.recorder = recorder

    def update(self, payload: dict[str, Any]) -> "FakeUpdateQuery":
        self.recorder["table"] = self.table_name
        self.recorder["payload"] = dict(payload)
        return self

    def eq(self, key: str, value: Any) -> "FakeUpdateQuery":
        self.recorder.setdefault("filters", []).append((key, value))
        return self

    def execute(self) -> FakeResponse:
        restored = {
            "guild_id": "123",
            **dict(self.recorder.get("payload") or {}),
        }
        return FakeResponse([restored])


class FakeSupabase:
    def __init__(self, recorder: dict[str, Any]) -> None:
        self.recorder = recorder

    def table(self, table_name: str) -> FakeUpdateQuery:
        return FakeUpdateQuery(table_name, self.recorder)


class FakeRpcCall:
    def __init__(
        self,
        events: list[dict[str, Any]],
        name: str,
        params: dict[str, Any],
    ) -> None:
        self.events = events
        self.name = name
        self.params = dict(params)

    def execute(self) -> FakeResponse:
        self.events.append(
            {
                "rpc": self.name,
                "params": dict(self.params),
            }
        )
        return FakeResponse([])


class FakeRpcSupabase:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = events

    def rpc(self, name: str, params: dict[str, Any]) -> FakeRpcCall:
        return FakeRpcCall(self.events, name, params)


def test_changed_config_keys_ignores_write_audit_metadata() -> None:
    before = {
        "guild_id": "123",
        "ticket_prefix": "ticket",
        "settings": {
            "spam_guard_enabled": True,
            "config_last_write_source": "setup",
            "config_last_write_at": "2026-07-20T10:00:00+00:00",
        },
    }
    after = {
        "guild_id": "123",
        "ticket_prefix": "ticket",
        "settings": {
            "spam_guard_enabled": True,
            "config_last_write_source": "config_history_restore",
            "config_last_write_at": "2026-07-20T11:00:00+00:00",
            "config_restored_from_version_id": "9",
        },
    }

    assert config_history.changed_config_keys(before, after) == []


def test_changed_config_keys_reports_real_functional_change() -> None:
    before = {
        "guild_id": "123",
        "settings": {"spam_guard_enabled": True},
    }
    after = {
        "guild_id": "123",
        "settings": {"spam_guard_enabled": False},
    }

    assert config_history.changed_config_keys(before, after) == ["spam_guard_enabled"]


def test_changed_ticket_category_slugs_ignore_ids_and_timestamps() -> None:
    snapshot = {
        "guild_id": "123",
        "rows": [
            {
                "id": "old-id",
                "guild_id": "123",
                "slug": "support",
                "name": "Support",
                "sort_order": 10,
                "updated_at": "old",
            }
        ],
    }
    current = [
        {
            "id": "new-id",
            "guild_id": "123",
            "slug": "support",
            "name": "Support",
            "sort_order": 10,
            "updated_at": "new",
        }
    ]

    assert config_history.changed_ticket_category_slugs(snapshot, current) == []


def test_changed_ticket_category_slugs_reports_real_choice_change() -> None:
    snapshot = {
        "guild_id": "123",
        "rows": [{"slug": "support", "name": "Support"}],
    }
    current = [
        {"slug": "support", "name": "General Support"},
        {"slug": "partnership", "name": "Partnerships"},
    ]

    assert config_history.changed_ticket_category_slugs(snapshot, current) == [
        "partnership",
        "support",
    ]


def test_manual_backup_snapshots_core_and_ticket_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {
        "guild_id": "123",
        "ticket_prefix": "ticket",
        "settings": {"spam_guard_enabled": True},
    }
    categories = [
        {
            "id": "cat-1",
            "guild_id": "123",
            "slug": "support",
            "name": "Support",
        }
    ]
    captured: list[dict[str, Any]] = []

    monkeypatch.setattr(
        config_history,
        "_fetch_current_config_row_sync",
        lambda guild_id: ("guild_configs", dict(current)),
    )
    monkeypatch.setattr(
        config_history,
        "_fetch_ticket_categories_state_sync",
        lambda guild_id: (True, [dict(row) for row in categories]),
    )

    def insert_snapshot(
        guild_id: int,
        snapshot: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        version_id = 55 + len(captured)
        captured.append(
            {
                "guild_id": guild_id,
                "snapshot": dict(snapshot),
                **kwargs,
            }
        )
        return {
            "version_id": version_id,
            "config_table": kwargs["config_table"],
            "snapshot": dict(snapshot),
        }

    monkeypatch.setattr(config_history, "_insert_snapshot_sync", insert_snapshot)

    result = config_history.create_manual_backup_sync(
        123,
        actor_id=77,
        reason="Before changing tickets",
    )

    assert [row["config_table"] for row in captured] == [
        "guild_configs",
        "ticket_categories",
    ]
    assert captured[0]["snapshot"] == current
    assert captured[1]["snapshot"]["rows"] == categories
    assert all(row["source"] == "manual_backup" for row in captured)
    assert all(row["is_manual"] is True for row in captured)
    assert [row["version_id"] for row in result["backup_versions"]] == [55, 56]


def test_restore_creates_safety_backup_then_restores_selected_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {
        "guild_id": "123",
        "ticket_prefix": "current",
        "settings": {
            "spam_guard_enabled": False,
            "config_last_write_source": "current_write",
        },
        "metadata": {"note": "current"},
        "created_at": "2026-07-20T00:00:00+00:00",
        "updated_at": "2026-07-20T12:00:00+00:00",
    }
    snapshot = {
        "guild_id": "123",
        "ticket_prefix": "restored",
        "settings": {
            "spam_guard_enabled": True,
            "config_last_write_source": "old_write",
        },
        "metadata": {"note": "old"},
        "created_at": "2026-07-19T00:00:00+00:00",
        "updated_at": "2026-07-19T12:00:00+00:00",
    }
    recorder: dict[str, Any] = {}
    backups: list[dict[str, Any]] = []
    cache_clears: list[int] = []

    monkeypatch.setattr(
        config_history,
        "_fetch_current_config_row_sync",
        lambda guild_id: ("guild_configs", dict(current)),
    )
    monkeypatch.setattr(
        config_history,
        "_fetch_version_sync",
        lambda guild_id, version_id, config_table=None: {
            "version_id": version_id,
            "guild_id": str(guild_id),
            "config_table": "guild_configs",
            "snapshot": dict(snapshot),
        },
    )

    def insert_snapshot(
        guild_id: int,
        saved_snapshot: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        backups.append(
            {
                "guild_id": guild_id,
                "snapshot": dict(saved_snapshot),
                **kwargs,
            }
        )
        return {
            "version_id": 99,
            "config_table": kwargs["config_table"],
            "snapshot": dict(saved_snapshot),
        }

    monkeypatch.setattr(config_history, "_insert_snapshot_sync", insert_snapshot)
    monkeypatch.setattr(
        config_history,
        "_require_supabase",
        lambda: FakeSupabase(recorder),
    )
    monkeypatch.setattr(
        config_history,
        "clear_guild_config_cache",
        lambda guild_id: cache_clears.append(guild_id),
    )

    result = config_history.restore_config_version_sync(
        123,
        8,
        actor_id=77,
        reason="Rollback bad setup change",
    )

    assert len(backups) == 1
    assert backups[0]["snapshot"] == current
    assert backups[0]["config_table"] == "guild_configs"
    assert backups[0]["source"] == "pre_restore_backup"
    assert backups[0]["mode"] == "restore_guard"
    assert backups[0]["is_manual"] is True

    payload = recorder["payload"]
    assert recorder["table"] == "guild_configs"
    assert recorder["filters"] == [("guild_id", "123")]
    assert payload["ticket_prefix"] == "restored"
    assert "guild_id" not in payload
    assert "created_at" not in payload
    assert "updated_at" not in payload
    assert payload["settings"]["spam_guard_enabled"] is True
    assert payload["settings"]["config_last_write_source"] == "config_history_restore"
    assert payload["settings"]["config_last_write_mode"] == "restore"
    assert payload["settings"]["config_last_write_actor_id"] == "77"
    assert payload["settings"]["config_last_write_reason"] == "Rollback bad setup change"
    assert payload["settings"]["config_restored_from_version_id"] == "8"
    assert cache_clears == [123]
    assert result["config_table"] == "guild_configs"
    assert result["restored_from_version_id"] == 8


def test_ticket_choice_restore_uses_one_atomic_rpc_and_history_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_categories = [
        {
            "id": "support-current-id",
            "guild_id": "123",
            "slug": "support",
            "name": "Current Support",
            "sort_order": 10,
        },
        {
            "id": "report-id",
            "guild_id": "123",
            "slug": "report",
            "name": "Reports",
            "sort_order": 20,
        },
    ]
    saved_categories = [
        {
            "id": "support-old-id",
            "guild_id": "123",
            "slug": "support",
            "name": "Support",
            "sort_order": 10,
            "form_questions": [{"label": "What happened?"}],
        },
        {
            "id": "partner-old-id",
            "guild_id": "123",
            "slug": "partnership",
            "name": "Partnerships",
            "sort_order": 30,
        },
    ]
    restored_categories = [dict(row) for row in saved_categories]
    restored_categories[0]["id"] = "support-current-id"
    fetches = iter(
        [
            (True, [dict(row) for row in current_categories]),
            (True, [dict(row) for row in restored_categories]),
        ]
    )
    rpc_events: list[dict[str, Any]] = []
    history_writes: list[dict[str, Any]] = []

    monkeypatch.setattr(
        config_history,
        "_fetch_current_config_row_sync",
        lambda guild_id: ("guild_configs", {"guild_id": str(guild_id)}),
    )
    monkeypatch.setattr(
        config_history,
        "_fetch_version_sync",
        lambda guild_id, version_id, config_table=None: {
            "version_id": version_id,
            "guild_id": str(guild_id),
            "config_table": "ticket_categories",
            "snapshot": {
                "guild_id": str(guild_id),
                "rows": [dict(row) for row in saved_categories],
            },
        },
    )
    monkeypatch.setattr(
        config_history,
        "_fetch_ticket_categories_state_sync",
        lambda guild_id: next(fetches),
    )

    def insert_snapshot(
        guild_id: int,
        snapshot: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        history_writes.append(
            {
                "guild_id": guild_id,
                "snapshot": snapshot,
                **kwargs,
            }
        )
        return {
            "version_id": 90 + len(history_writes),
            "config_table": kwargs["config_table"],
            "snapshot": snapshot,
        }

    monkeypatch.setattr(config_history, "_insert_snapshot_sync", insert_snapshot)
    monkeypatch.setattr(
        config_history,
        "_require_supabase",
        lambda: FakeRpcSupabase(rpc_events),
    )

    result = config_history.restore_config_version_sync(
        123,
        44,
        actor_id=77,
        reason="Restore ticket choices",
    )

    assert rpc_events == [
        {
            "rpc": "restore_ticket_categories_snapshot",
            "params": {
                "p_guild_id": "123",
                "p_rows": saved_categories,
            },
        }
    ]

    assert len(history_writes) == 2
    assert history_writes[0]["config_table"] == "ticket_categories"
    assert history_writes[0]["source"] == "pre_restore_backup"
    assert history_writes[0]["snapshot"]["rows"] == current_categories
    assert history_writes[1]["config_table"] == "ticket_categories"
    assert history_writes[1]["source"] == "config_history_restore"
    assert history_writes[1]["mode"] == "restore"
    assert history_writes[1]["actor_id"] == 77
    assert history_writes[1]["reason"] == "Restore ticket choices"
    assert history_writes[1]["snapshot"]["rows"] == restored_categories
    assert result["config_table"] == "ticket_categories"
    assert result["restored_from_version_id"] == 44


def test_restore_refuses_snapshot_from_different_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_history,
        "_fetch_current_config_row_sync",
        lambda guild_id: (
            "guild_configs",
            {"guild_id": str(guild_id), "settings": {}},
        ),
    )
    monkeypatch.setattr(
        config_history,
        "_fetch_version_sync",
        lambda guild_id, version_id, config_table=None: {
            "version_id": version_id,
            "guild_id": str(guild_id),
            "config_table": "guild_configs",
            "snapshot": {"guild_id": "999", "settings": {}},
        },
    )

    with pytest.raises(RuntimeError, match="different guild"):
        config_history.restore_config_version_sync(123, 8)


def test_ticket_choice_restore_refuses_row_from_different_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_history,
        "_fetch_current_config_row_sync",
        lambda guild_id: ("guild_configs", {"guild_id": str(guild_id)}),
    )
    monkeypatch.setattr(
        config_history,
        "_fetch_version_sync",
        lambda guild_id, version_id, config_table=None: {
            "version_id": version_id,
            "guild_id": str(guild_id),
            "config_table": "ticket_categories",
            "snapshot": {
                "guild_id": str(guild_id),
                "rows": [
                    {
                        "guild_id": "999",
                        "slug": "support",
                        "name": "Support",
                    }
                ],
            },
        },
    )
    monkeypatch.setattr(
        config_history,
        "_fetch_ticket_categories_state_sync",
        lambda guild_id: (True, []),
    )

    with pytest.raises(RuntimeError, match="different guild"):
        config_history.restore_config_version_sync(123, 8)


def test_restore_refuses_snapshot_from_different_config_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_history,
        "_fetch_current_config_row_sync",
        lambda guild_id: (
            "guild_configs",
            {"guild_id": str(guild_id), "settings": {}},
        ),
    )
    monkeypatch.setattr(
        config_history,
        "_fetch_version_sync",
        lambda guild_id, version_id, config_table=None: {
            "version_id": version_id,
            "guild_id": str(guild_id),
            "config_table": "some_other_config_table",
            "snapshot": {"guild_id": str(guild_id), "settings": {}},
        },
    )

    with pytest.raises(RuntimeError, match="different configuration table"):
        config_history.restore_config_version_sync(123, 8)
