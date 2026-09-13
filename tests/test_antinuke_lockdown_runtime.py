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
    monkeypatch.setattr(history, lockdown._HISTORY_PATCH_FLAG, False, raising=False)  # noqa: SLF001
    monkeypatch.setattr(selective, lockdown._HISTORY_PATCH_FLAG, False, raising=False)  # noqa: SLF001

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


def test_contain_mode_removes_configured_trust_grace_and_expands_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_settings(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    fake_antinuke = SimpleNamespace(
        _actor_is_configured_trusted=lambda actor, settings, ignore_role_ids=None: True,
        get_antinuke_settings=get_settings,
        antinuke_permission_health=lambda guild, settings=None: [],
        normalize_antinuke_settings=lambda settings: dict(settings or {}),
        DANGEROUS_PERMISSION_NAMES=("administrator", "manage_channels"),
        _SLOW_BURN_ACTIONS=frozenset({"channel_delete"}),
    )
    fake_bot = SimpleNamespace(intents=SimpleNamespace(moderation=True))

    monkeypatch.setattr(fake_antinuke, lockdown._POLICY_PATCH_FLAG, False, raising=False)  # noqa: SLF001
    assert lockdown._patch_anti_nuke_policy(fake_antinuke, fake_bot) is True  # noqa: SLF001

    assert (
        fake_antinuke._actor_is_configured_trusted(
            object(),
            {"antinuke_enabled": True, "antinuke_mode": "contain"},
        )
        is False
    )
    assert (
        fake_antinuke._actor_is_configured_trusted(
            object(),
            {"antinuke_enabled": True, "antinuke_mode": "alert"},
        )
        is True
    )
    assert "manage_messages" in fake_antinuke.DANGEROUS_PERMISSION_NAMES
    assert "manage_threads" in fake_antinuke.DANGEROUS_PERMISSION_NAMES
    assert "message_delete" in fake_antinuke._SLOW_BURN_ACTIONS


def test_control_roles_are_detected_from_nested_config() -> None:
    cfg = {
        "settings": {
            "server_control_role_id": "111",
            "control_role_ids": [222, "333"],
        },
        "perm_role_id": "444",
    }

    assert lockdown._control_role_ids(cfg) == {111, 222, 333, 444}  # noqa: SLF001


def test_guardian_surface_covers_message_purge_and_authority_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardian = SimpleNamespace(
        _ACTIONS={"channel_delete": ("Channel deletion", "x", "y", None)},
        _PANIC_WEIGHTS={"channel_delete": 3},
        _PANIC_ACTIONS=frozenset({"channel_delete"}),
        _PANIC_SEVERE_ACTIONS=frozenset({"channel_delete"}),
    )
    monkeypatch.setattr(guardian, lockdown._GUARDIAN_PATCH_FLAG, False, raising=False)  # noqa: SLF001

    assert lockdown._patch_guardian_surface(guardian) is True  # noqa: SLF001

    for action in (
        "message_delete",
        "message_bulk_delete",
        "integration_create",
        "integration_update",
        "invite_create",
        "scheduled_event_update",
        "thread_update",
        "member_disconnect",
    ):
        assert action in guardian._ACTIONS
        assert action in guardian._PANIC_ACTIONS
    assert "message_bulk_delete" in guardian._PANIC_SEVERE_ACTIONS


def test_owner_destructive_path_is_forced_to_first_strike(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def original(guild, **kwargs):
        _ = guild
        captured.update(kwargs)
        return True

    incident = SimpleNamespace(_process_owner_destructive_event=original)
    monkeypatch.setattr(incident, lockdown._OWNER_PATCH_FLAG, False, raising=False)  # noqa: SLF001

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


def test_owner_added_unapproved_bot_is_removed_but_trusted_bot_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = {
        "antinuke_enabled": True,
        "antinuke_mode": "contain",
        "antinuke_trusted_user_ids": [],
    }
    original_calls: list[int] = []
    incidents: list[str] = []

    async def original(guild, entry, actor):
        _ = guild, actor
        original_calls.append(int(entry.target.id))

    async def get_settings(_guild_id: int):
        return dict(settings)

    async def post_incident(guild, **kwargs):
        _ = guild
        incidents.append(str(kwargs["title"]))

    async def reputation(_guild_id: int, _user_id: int, *, refresh: bool = False):
        _ = refresh
        return None

    guardian = SimpleNamespace(_handle_bot_add=original)
    anti_nuke = SimpleNamespace(
        get_antinuke_settings=get_settings,
        _post_incident=post_incident,
    )
    hostile = SimpleNamespace(get_actor_reputation=reputation)
    monkeypatch.setattr(guardian, lockdown._BOT_ADD_PATCH_FLAG, False, raising=False)  # noqa: SLF001

    assert lockdown._patch_bot_add_guardian(guardian, anti_nuke, hostile) is True  # noqa: SLF001

    class Guild:
        id = 10
        owner_id = 99

        def __init__(self) -> None:
            self.kicked: list[int] = []

        async def kick(self, target, *, reason: str):
            assert "not pre-approved" in reason
            self.kicked.append(int(target.id))

    guild = Guild()
    owner = SimpleNamespace(id=99)
    target = SimpleNamespace(id=55)
    entry = SimpleNamespace(target=target)

    asyncio.run(guardian._handle_bot_add(guild, entry, owner))
    assert guild.kicked == [55]
    assert original_calls == []
    assert incidents == ["🚨 AntiNuke Unapproved Bot Added By Owner"]

    settings["antinuke_trusted_user_ids"] = [55]
    asyncio.run(guardian._handle_bot_add(guild, entry, owner))
    assert guild.kicked == [55]
    assert original_calls == []


def test_known_hostile_bot_reputation_outranks_trust_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_calls: list[int] = []

    async def original(guild, entry, actor):
        _ = guild, actor
        original_calls.append(int(entry.target.id))

    async def get_settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_trusted_user_ids": [55],
        }

    async def reputation(_guild_id: int, _user_id: int, *, refresh: bool = False):
        _ = refresh
        return {"active": True}

    guardian = SimpleNamespace(_handle_bot_add=original)
    anti_nuke = SimpleNamespace(get_antinuke_settings=get_settings)
    hostile = SimpleNamespace(get_actor_reputation=reputation)
    monkeypatch.setattr(guardian, lockdown._BOT_ADD_PATCH_FLAG, False, raising=False)  # noqa: SLF001

    assert lockdown._patch_bot_add_guardian(guardian, anti_nuke, hostile) is True  # noqa: SLF001
    guild = SimpleNamespace(id=10, owner_id=99)
    entry = SimpleNamespace(target=SimpleNamespace(id=55))
    asyncio.run(guardian._handle_bot_add(guild, entry, SimpleNamespace(id=99)))

    assert original_calls == [55]


def test_main_installs_lockdown_inside_hostile_runtime_before_app_import() -> None:
    source = Path("main.py").read_text(encoding="utf-8")

    hostile_block = source.index("def _install_hostile_actor_runtime")
    app_import = source.index("from stoney_verify.app import run as _run_dank_shield")
    assert hostile_block < app_import
    assert "install_anti_nuke_lockdown_runtime" in source[hostile_block:app_import]
