from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import invite_policy_engine
from stoney_verify import invite_scope_settings as scope
from stoney_verify.commands_ext import public_protection_center as center
from stoney_verify.commands_ext import public_protection_invite_ui as invite_ui


def test_scope_normalizes_legacy_aliases_and_mentions() -> None:
    normalized = scope.normalize_scope(
        {
            "invite_target_all_bots": "yes",
            "invite_target_bot_ids": "<@123456789012345678>, 222222222222222222",
            "invite_target_channel_ids": "<#333333333333333333> 444444444444444444",
            "protected_poster_invite_rule_enabled": "on",
        }
    )

    assert normalized[scope.ALL_BOTS_KEY] is True
    assert normalized[scope.BOT_IDS_KEY] == ["123456789012345678", "222222222222222222"]
    assert normalized[scope.CHANNEL_IDS_KEY] == ["333333333333333333", "444444444444444444"]
    assert normalized[scope.PROTECTED_RULE_KEY] is True


def test_scope_merge_feeds_canonical_policy_settings() -> None:
    merged = scope.merge_scope_settings(
        {"enabled": True},
        {
            scope.ALL_BOTS_KEY: True,
            scope.BOT_IDS_KEY: ["123"],
            scope.CHANNEL_IDS_KEY: ["456"],
            scope.PROTECTED_RULE_KEY: True,
        },
    )

    assert merged["enabled"] is True
    assert merged[scope.ALL_BOTS_KEY] is True
    assert merged[scope.BOT_IDS_KEY] == ["123"]
    assert merged[scope.CHANNEL_IDS_KEY] == ["456"]
    assert merged[scope.PROTECTED_RULE_KEY] is True
    assert merged[f"spam_{scope.CHANNEL_IDS_KEY}"] == ["456"]


def test_scope_save_writes_canonical_and_compat_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[tuple[int, dict[str, Any]]] = []
    invalidated: list[int] = []
    policy_invalidated: list[int] = []

    async def fake_get(guild_id: int, *, refresh: bool = False) -> dict[str, Any]:
        assert guild_id == 42
        assert refresh is True
        return {
            scope.ALL_BOTS_KEY: False,
            scope.BOT_IDS_KEY: [],
            scope.CHANNEL_IDS_KEY: [],
            scope.PROTECTED_RULE_KEY: False,
        }

    async def fake_upsert(guild_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        writes.append((guild_id, dict(payload)))
        return dict(payload)

    monkeypatch.setattr(scope, "get_guild_config", fake_get)
    monkeypatch.setattr(scope, "upsert_guild_config", fake_upsert)
    monkeypatch.setattr(scope, "invalidate_guild_config", lambda guild_id: invalidated.append(int(guild_id)))
    monkeypatch.setattr(invite_policy_engine, "invalidate_invite_policy", lambda guild_id: policy_invalidated.append(int(guild_id)))

    result = asyncio.run(
        scope.save_invite_scope_settings(
            42,
            {
                scope.ALL_BOTS_KEY: True,
                scope.CHANNEL_IDS_KEY: ["987654321098765432"],
                scope.PROTECTED_RULE_KEY: True,
            },
        )
    )

    assert result[scope.ALL_BOTS_KEY] is True
    assert result[scope.CHANNEL_IDS_KEY] == ["987654321098765432"]
    assert writes and writes[0][0] == 42
    payload = writes[0][1]
    for key in (
        scope.ALL_BOTS_KEY,
        scope.BOT_IDS_KEY,
        scope.CHANNEL_IDS_KEY,
        scope.PROTECTED_RULE_KEY,
    ):
        assert key in payload
        assert f"spam_{key}" in payload
        assert payload[key] == payload[f"spam_{key}"]
    assert invalidated == [42]
    assert policy_invalidated == [42]


def test_policy_scope_binding_augments_policy_loader_without_patching_spam_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {
        scope.ALL_BOTS_KEY: True,
        scope.BOT_IDS_KEY: ["111"],
        scope.CHANNEL_IDS_KEY: ["222"],
        scope.PROTECTED_RULE_KEY: True,
    }

    async def fake_load(guild: Any, *, refresh: bool = False):
        _ = guild, refresh
        return cfg, {"enabled": True}

    monkeypatch.setattr(invite_policy_engine, "load_invite_policy", fake_load)
    monkeypatch.setattr(scope, "_POLICY_BOUND", False)
    monkeypatch.setattr(scope, "_ORIGINAL_POLICY_LOAD", None)

    assert scope.install_invite_policy_scope_binding() is True
    loaded_cfg, settings = asyncio.run(invite_policy_engine.load_invite_policy(SimpleNamespace(id=7), refresh=True))

    assert loaded_cfg is cfg
    assert settings["enabled"] is True
    assert settings[scope.ALL_BOTS_KEY] is True
    assert settings[scope.BOT_IDS_KEY] == ["111"]
    assert settings[scope.CHANNEL_IDS_KEY] == ["222"]
    assert settings[scope.PROTECTED_RULE_KEY] is True


def test_native_invite_view_uses_buttons_not_raw_resource_selectors() -> None:
    view = invite_ui.InviteShieldView(
        author_id=123,
        guild=None,
        origin_channel_id=456,
        scope={},
    )

    labels = {str(getattr(child, "label", "") or "") for child in view.children}
    assert {
        "Fix This Channel",
        "Turn Shield On / Off",
        "Watch Every Bot",
        "Choose Watched Channel",
        "All Channels",
        "Advanced IDs",
        "Clean Existing Invites",
        "Back to Protection",
    }.issubset(labels)
    assert not any(isinstance(child, (discord.ui.ChannelSelect, discord.ui.RoleSelect)) for child in view.children)
    assert not any(type(child) is discord.ui.Select for child in view.children)


def test_native_invite_ui_uses_shared_browser_and_central_cleanup_policy() -> None:
    source = Path(invite_ui.__file__).read_text(encoding="utf-8")
    assert "DankGuildResourceBrowserView" in source
    assert 'resource_kinds=("text",)' in source
    assert "scan_channel_invites" in source
    assert "protection-center-native-invite-cleanup" in source
    assert "startup_guards" not in source


def test_native_editor_preserves_original_on_off_action() -> None:
    source = Path(invite_ui.__file__).read_text(encoding="utf-8")
    assert 'label="Turn Shield On / Off"' in source
    assert "await _ORIGINAL_TOGGLE(interaction)" in source
    assert "_ORIGINAL_TOGGLE = original" in source


def test_bootstrap_binds_feature_function_not_component_callback_or_view_init() -> None:
    source = Path(center.__file__).read_text(encoding="utf-8")
    ui_source = Path(invite_ui.__file__).read_text(encoding="utf-8")
    commands_source = (Path(center.__file__).parents[1] / "commands.py").read_text(encoding="utf-8")

    assert "await _toggle_invite_shield(interaction)" in source
    assert "center._toggle_invite_shield = open_invite_shield" in ui_source
    assert "ProtectionCenterView.__init__ =" not in ui_source
    assert ".callback =" not in ui_source
    assert "_install_invite_policy_scope_binding" in commands_source
    assert "_install_native_invite_ui" in commands_source
    assert "startup_guards" not in commands_source[commands_source.index("# Invite target metadata"):commands_source.index("# The public Create Ticket button")]
