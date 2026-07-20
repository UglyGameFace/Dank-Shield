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


def test_manual_backup_snapshots_current_config_and_source_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = {
        "guild_id": "123",
        "ticket_prefix": "ticket",
        "settings": {"spam_guard_enabled": True},
    }
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        config_history,
        "_fetch_current_config_row_sync",
        lambda guild_id: ("guild_configs", dict(current)),
    )

    def insert_snapshot(
        guild_id: int,
        snapshot: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured["guild_id"] = guild_id
        captured["snapshot"] = dict(snapshot)
        captured.update(kwargs)
        return {
            "version_id": 55,
            "config_table": kwargs["config_table"],
            "snapshot": dict(snapshot),
        }

    monkeypatch.setattr(config_history, "_insert_snapshot_sync", insert_snapshot)

    result = config_history.create_manual_backup_sync(
        123,
        actor_id=77,
        reason="Before changing tickets",
    )

    assert result["version_id"] == 55
    assert captured["guild_id"] == 123
    assert captured["snapshot"] == current
    assert captured["config_table"] == "guild_configs"
    assert captured["source"] == "manual_backup"
    assert captured["mode"] == "manual"
    assert captured["actor_id"] == 77
    assert captured["reason"] == "Before changing tickets"
    assert captured["is_manual"] is True


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
    version_scopes: list[str | None] = []

    monkeypatch.setattr(
        config_history,
        "_fetch_current_config_row_sync",
        lambda guild_id: ("guild_configs", dict(current)),
    )

    def fetch_version(
        guild_id: int,
        version_id: int,
        *,
        config_table: str | None = None,
    ) -> dict[str, Any]:
        version_scopes.append(config_table)
        return {
            "version_id": version_id,
            "guild_id": str(guild_id),
            "config_table": "guild_configs",
            "snapshot": dict(snapshot),
        }

    monkeypatch.setattr(config_history, "_fetch_version_sync", fetch_version)

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

    assert version_scopes == ["guild_configs"]
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
    assert payload["metadata"]["note"] == "old"
    assert payload["metadata"]["config_last_write_source"] == "config_history_restore"

    assert cache_clears == [123]
    assert result["config_table"] == "guild_configs"
    assert result["restored_from_version_id"] == 8
    assert result["pre_restore_backup"]["version_id"] == 99


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
            "config_table": "guild_config",
            "snapshot": {"guild_id": str(guild_id), "settings": {}},
        },
    )

    with pytest.raises(RuntimeError, match="different configuration table"):
        config_history.restore_config_version_sync(123, 8)
