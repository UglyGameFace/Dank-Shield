from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import anti_nuke_lockdown_runtime as lockdown


def test_security_snapshot_preserves_live_antinuke_and_control_values() -> None:
    snapshot = {
        "guild_id": "1",
        "server_control_role_id": "111",
        "settings": {
            "antinuke_enabled": False,
            "antinuke_mode": "alert",
            "antinuke_trusted_user_ids": [9],
            "server_control_role_id": "111",
            "welcome_title": "old",
        },
        "ordinary": "old",
    }
    current = {
        "guild_id": "1",
        "server_control_role_id": "222",
        "settings": {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_trusted_user_ids": [7],
            "server_control_role_id": "222",
            "welcome_title": "live",
        },
        "ordinary": "live",
    }

    safe = lockdown._sanitize_security_snapshot(snapshot, current)  # noqa: SLF001

    assert safe["server_control_role_id"] == "222"
    assert safe["settings"]["antinuke_enabled"] is True
    assert safe["settings"]["antinuke_mode"] == "contain"
    assert safe["settings"]["antinuke_trusted_user_ids"] == [7]
    assert safe["settings"]["server_control_role_id"] == "222"
    assert safe["settings"]["welcome_title"] == "old"
    assert safe["ordinary"] == "old"


def test_security_snapshot_does_not_reintroduce_removed_security_keys() -> None:
    snapshot = {
        "settings": {
            "antinuke_enabled": False,
            "bot_owner_role_id": "123",
            "ordinary": "saved",
        }
    }
    current = {"settings": {"ordinary": "live"}}

    safe = lockdown._sanitize_security_snapshot(snapshot, current)  # noqa: SLF001

    assert "antinuke_enabled" not in safe["settings"]
    assert "bot_owner_role_id" not in safe["settings"]
    assert safe["settings"]["ordinary"] == "saved"


def test_restore_item_filter_blocks_security_roots() -> None:
    allowed, blocked = lockdown._filter_restore_items(  # noqa: SLF001
        [
            "welcome_title",
            "antinuke_enabled",
            "anti_nuke_mode",
            "server_control_role_id",
            "ticket_channel_id",
        ]
    )

    assert allowed == ["welcome_title", "ticket_channel_id"]
    assert blocked == [
        "antinuke_enabled",
        "anti_nuke_mode",
        "server_control_role_id",
    ]


def test_config_history_patch_filters_security_items_and_sanitizes_full_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stoney_verify import config_history as history
    from stoney_verify import config_history_selective as selective

    captured: dict[str, object] = {}

    def fake_full(
        guild_id,
        version_id,
        version,
        *,
        table_name,
        current,
        actor_id,
        reason,
    ):
        _ = guild_id, version_id, table_name, current, actor_id, reason
        captured["full_snapshot"] = dict(version["snapshot"])
        return {"restored": True}

    def fake_plan(guild_id, version_id):
        _ = guild_id, version_id
        return {
            "domain": selective.CORE_DOMAIN,
            "changed_items": [
                "antinuke_enabled",
                "server_control_role_id",
                "welcome_title",
            ],
            "missing_items": ["antinuke_enabled", "welcome_title"],
            "item_labels": {
                "antinuke_enabled": "AntiNuke Enabled",
                "server_control_role_id": "Server Control Role",
                "welcome_title": "Welcome Title",
            },
            "core_sections": {
                "Protection & Moderation": ["antinuke_enabled"],
                "Roles": ["server_control_role_id"],
                "Welcome": ["welcome_title"],
            },
        }

    def fake_selected(
        guild_id,
        version_id,
        *,
        version,
        table_name,
        current,
        selected,
        actor_id,
        reason,
        mode,
    ):
        _ = (
            guild_id,
            version_id,
            version,
            table_name,
            current,
            actor_id,
            reason,
            mode,
        )
        captured["selected"] = list(selected)
        return {"restored_items": list(selected)}

    monkeypatch.setattr(history, "_restore_core_config_version_sync", fake_full)
    monkeypatch.setattr(selective, "plan_selective_restore_sync", fake_plan)
    monkeypatch.setattr(selective, "_restore_core_selected_sync", fake_selected)
    monkeypatch.setattr(
        history,
        lockdown._HISTORY_PATCH_FLAG,  # noqa: SLF001
        False,
        raising=False,
    )
    monkeypatch.setattr(
        selective,
        lockdown._HISTORY_PATCH_FLAG,  # noqa: SLF001
        False,
        raising=False,
    )

    assert lockdown._patch_config_history_restore() is True  # noqa: SLF001

    result = history._restore_core_config_version_sync(  # noqa: SLF001
        1,
        2,
        {
            "snapshot": {
                "server_control_role_id": "old-control",
                "settings": {
                    "antinuke_enabled": False,
                    "welcome_title": "saved-title",
                },
            }
        },
        table_name="guild_configs",
        current={
            "server_control_role_id": "live-control",
            "settings": {
                "antinuke_enabled": True,
                "welcome_title": "live-title",
            },
        },
        actor_id=9,
        reason="test",
    )
    assert result["protected_security_values_preserved"] is True
    full_snapshot = captured["full_snapshot"]
    assert isinstance(full_snapshot, dict)
    assert full_snapshot["server_control_role_id"] == "live-control"
    assert full_snapshot["settings"]["antinuke_enabled"] is True
    assert full_snapshot["settings"]["welcome_title"] == "saved-title"

    plan = selective.plan_selective_restore_sync(1, 2)
    assert plan["changed_items"] == ["welcome_title"]
    assert plan["missing_items"] == ["welcome_title"]
    assert plan["protected_security_items"] == [
        "antinuke_enabled",
        "server_control_role_id",
    ]

    selected_result = selective._restore_core_selected_sync(  # noqa: SLF001
        1,
        2,
        version={},
        table_name="guild_configs",
        current={},
        selected=["antinuke_enabled", "welcome_title"],
        actor_id=9,
        reason="test",
        mode="selected",
    )
    assert captured["selected"] == ["welcome_title"]
    assert selected_result["blocked_security_items"] == ["antinuke_enabled"]

    with pytest.raises(ValueError, match="cannot be restored"):
        selective._restore_core_selected_sync(  # noqa: SLF001
            1,
            2,
            version={},
            table_name="guild_configs",
            current={},
            selected=["antinuke_enabled"],
            actor_id=9,
            reason="test",
            mode="selected",
        )


def _configured_trust(actor, settings, *, ignore_role_ids=None) -> bool:
    ignored = {int(value) for value in (ignore_role_ids or set())}
    trusted = {
        int(value)
        for value in settings.get("antinuke_trusted_role_ids", [])
    } - ignored
    actor_roles = {
        int(role.id)
        for role in list(getattr(actor, "roles", []) or [])
    }
    return bool(trusted.intersection(actor_roles))


def test_structural_actions_become_first_strike_without_breaking_routine_moderation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stoney_verify import guild_config

    captured: list[tuple[str, object]] = []

    async def original_get(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_trusted_role_ids": [7],
        }

    async def original_process(guild, **kwargs):
        _ = guild
        captured.append((kwargs["action_key"], kwargs["threshold_override"]))
        return True

    async def fake_cfg(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return {
            "settings": {
                "server_control_role_id": "111",
                "control_role_ids": [222],
            }
        }

    fake_antinuke = SimpleNamespace(
        get_antinuke_settings=original_get,
        _process_claimed_destructive_event=original_process,
        _actor_is_configured_trusted=_configured_trust,
        antinuke_permission_health=lambda guild, settings=None: [],
        normalize_antinuke_settings=lambda settings: dict(settings or {}),
        DANGEROUS_PERMISSION_NAMES=("administrator", "manage_channels"),
    )
    fake_bot = SimpleNamespace(intents=SimpleNamespace(moderation=True))

    monkeypatch.setattr(guild_config, "get_guild_config", fake_cfg)
    monkeypatch.setattr(
        fake_antinuke,
        lockdown._POLICY_PATCH_FLAG,  # noqa: SLF001
        False,
        raising=False,
    )

    assert lockdown._patch_anti_nuke_policy(fake_antinuke, fake_bot) is True  # noqa: SLF001

    settings = asyncio.run(fake_antinuke.get_antinuke_settings(1))
    assert settings["antinuke_trusted_role_ids"] == [7, 111, 222]
    assert settings[lockdown._PROTECTED_CONTROL_ROLE_SETTINGS_KEY] == [111, 222]  # noqa: SLF001
    assert "manage_messages" in fake_antinuke.DANGEROUS_PERMISSION_NAMES
    assert "manage_threads" in fake_antinuke.DANGEROUS_PERMISSION_NAMES
    assert "move_members" not in fake_antinuke.DANGEROUS_PERMISSION_NAMES

    explicit_actor = SimpleNamespace(roles=[SimpleNamespace(id=7)])
    implicit_control_actor = SimpleNamespace(roles=[SimpleNamespace(id=111)])
    assert fake_antinuke._actor_is_configured_trusted(explicit_actor, settings) is True
    assert (
        fake_antinuke._actor_is_configured_trusted(implicit_control_actor, settings)
        is False
    )

    guild = SimpleNamespace(id=1)
    common = {
        "entry": SimpleNamespace(user=SimpleNamespace(id=9)),
        "action_label": "test",
        "target_label": "target",
        "threshold_key": "antinuke_channel_delete_threshold",
    }
    asyncio.run(
        fake_antinuke._process_claimed_destructive_event(
            guild,
            action_key="channel_delete",
            threshold_override=None,
            **common,
        )
    )
    asyncio.run(
        fake_antinuke._process_claimed_destructive_event(
            guild,
            action_key="ban",
            threshold_override=None,
            **common,
        )
    )

    assert captured == [("channel_delete", 1), ("ban", None)]


def test_control_role_explicitly_trusted_by_owner_remains_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stoney_verify import guild_config

    async def original_get(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_trusted_role_ids": [111],
        }

    async def original_process(_guild, **_kwargs):
        return True

    async def fake_cfg(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return {"settings": {"server_control_role_id": "111"}}

    fake_antinuke = SimpleNamespace(
        get_antinuke_settings=original_get,
        _process_claimed_destructive_event=original_process,
        _actor_is_configured_trusted=_configured_trust,
        antinuke_permission_health=lambda guild, settings=None: [],
        normalize_antinuke_settings=lambda settings: dict(settings or {}),
        DANGEROUS_PERMISSION_NAMES=("administrator",),
    )
    fake_bot = SimpleNamespace(intents=SimpleNamespace(moderation=True))

    monkeypatch.setattr(guild_config, "get_guild_config", fake_cfg)
    monkeypatch.setattr(
        fake_antinuke,
        lockdown._POLICY_PATCH_FLAG,  # noqa: SLF001
        False,
        raising=False,
    )

    assert lockdown._patch_anti_nuke_policy(fake_antinuke, fake_bot) is True  # noqa: SLF001
    settings = asyncio.run(fake_antinuke.get_antinuke_settings(1))

    assert settings["antinuke_trusted_role_ids"] == [111]
    assert lockdown._PROTECTED_CONTROL_ROLE_SETTINGS_KEY not in settings  # noqa: SLF001
    actor = SimpleNamespace(roles=[SimpleNamespace(id=111)])
    assert fake_antinuke._actor_is_configured_trusted(actor, settings) is True


def test_control_roles_are_detected_from_nested_config() -> None:
    cfg = {
        "settings": {
            "server_control_role_id": "111",
            "control_role_ids": [222, "333"],
        },
        "perm_role_id": "444",
    }

    assert lockdown._control_role_ids(cfg) == {111, 222, 333, 444}  # noqa: SLF001


def test_guardian_surface_adds_bulk_purge_and_no_grace_only_to_security_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def rollback(*_args, **_kwargs):
        return "ok"

    guardian = SimpleNamespace(
        _ACTIONS={
            "channel_delete": ("Channel deletion", "channel", "channel_delete", None),
            "ban": ("Member ban", "ban", "ban", None),
            "overwrite_update": ("Overwrite", "channel", "channel_update", None),
        },
        _PANIC_WEIGHTS={"channel_delete": 3},
        _PANIC_ACTIONS=frozenset({"channel_delete"}),
        _PANIC_SEVERE_ACTIONS=frozenset({"channel_delete"}),
        _rollback_untrusted_overwrite=rollback,
        _rollback_untrusted_automod=rollback,
    )
    anti_nuke = SimpleNamespace(
        get_antinuke_settings=lambda *_args, **_kwargs: None,
        _actor_is_owner_or_bot=lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        guardian,
        lockdown._GUARDIAN_PATCH_FLAG,  # noqa: SLF001
        False,
        raising=False,
    )

    assert lockdown._patch_guardian_surface(guardian, anti_nuke) is True  # noqa: SLF001

    assert guardian._ACTIONS["channel_delete"][3] == 1
    assert guardian._ACTIONS["overwrite_update"][3] == 1
    assert guardian._ACTIONS["ban"][3] is None
    assert guardian._ACTIONS["message_bulk_delete"][3] == 1
    assert "message_bulk_delete" in guardian._PANIC_ACTIONS
    assert "message_bulk_delete" in guardian._PANIC_SEVERE_ACTIONS


def test_guardian_overwrite_rollback_does_not_honor_delegated_trust(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_actor_ids: list[int] = []

    async def original_overwrite(_guild, _entry, actor, _action_name):
        seen_actor_ids.append(int(actor.id))
        return "rolled back"

    async def original_automod(_guild, _entry, actor, _action_name):
        return str(actor.id)

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    guardian = SimpleNamespace(
        _ACTIONS={},
        _PANIC_WEIGHTS={},
        _PANIC_ACTIONS=frozenset(),
        _PANIC_SEVERE_ACTIONS=frozenset(),
        _rollback_untrusted_overwrite=original_overwrite,
        _rollback_untrusted_automod=original_automod,
    )
    anti_nuke = SimpleNamespace(
        get_antinuke_settings=settings,
        _actor_is_owner_or_bot=lambda _guild, _actor: False,
    )
    monkeypatch.setattr(
        guardian,
        lockdown._GUARDIAN_PATCH_FLAG,  # noqa: SLF001
        False,
        raising=False,
    )

    lockdown._patch_guardian_surface(guardian, anti_nuke)  # noqa: SLF001
    guild = SimpleNamespace(id=1)
    asyncio.run(
        guardian._rollback_untrusted_overwrite(
            guild,
            object(),
            SimpleNamespace(id=99, roles=[]),
            "overwrite_update",
        )
    )

    assert seen_actor_ids == [0]


def test_owner_destructive_path_is_forced_to_first_strike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def original(guild, **kwargs):
        _ = guild
        captured.update(kwargs)
        return True

    incident = SimpleNamespace(_process_owner_destructive_event=original)
    monkeypatch.setattr(
        incident,
        lockdown._OWNER_PATCH_FLAG,  # noqa: SLF001
        False,
        raising=False,
    )

    assert lockdown._patch_owner_first_strike(incident) is True  # noqa: SLF001
    result = asyncio.run(
        incident._process_owner_destructive_event(
            object(),
            entry=object(),
            action_key="channel_delete",
            action_label="Channel deletion",
            target_label="#general",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=9,
        )
    )

    assert result is True
    assert captured["threshold_override"] == 1


def test_lockdown_runtime_no_longer_owns_bot_add_authorization() -> None:
    source = Path("stoney_verify/anti_nuke_lockdown_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "_patch_bot_add_guardian" not in source
    assert "_BOT_ADD_PATCH_FLAG" not in source


def test_coordinator_keeps_hostile_lockdown_and_self_action_independent() -> None:
    source = Path("stoney_verify/anti_nuke_runtime_coordinator.py").read_text(
        encoding="utf-8"
    )

    incident = source.index('"incident"')
    hostile = source.index('"hostile_actor"', incident)
    lockdown_pos = source.index('"lockdown"', hostile)
    self_action = source.index('"self_action"', lockdown_pos)
    app_boundary = Path("main.py").read_text(encoding="utf-8").index(
        "from stoney_verify.app import run as _run_dank_shield"
    )

    assert incident < hostile < lockdown_pos < self_action
    assert app_boundary > 0
    assert '"install_hostile_actor_runtime"' in source
    assert '"install_anti_nuke_lockdown_runtime"' in source

