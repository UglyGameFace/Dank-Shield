from __future__ import annotations

from pathlib import Path

import pytest

from stoney_verify import settings_registry as registry
from stoney_verify import spam_guard


ROOT = Path(__file__).resolve().parents[1]


def test_registry_declares_protection_invite_ownership_and_defaults() -> None:
    expected = {
        "automod_enabled": ("bool", False, "automod", "guild_config"),
        "automod_block_invites": ("bool", False, "invite_policy", "guild_config"),
        "automod_block_links": ("bool", False, "automod", "guild_config"),
        "automod_link_policy": ("choice", "allow_links", "automod", "guild_config"),
        "automod_bad_words": ("string", "", "automod", "guild_config"),
        "invite_shield_enabled": ("bool", False, "invite_policy", "spam_guard"),
        "invite_hard_block_enabled": ("bool", False, "invite_policy", "spam_guard"),
        "block_invites": ("bool", False, "invite_policy", "spam_guard"),
        "block_external_invites_only": ("bool", True, "invite_policy", "spam_guard"),
        "allow_server_invites": ("bool", True, "invite_policy", "spam_guard"),
        "invite_hard_block_target_all_bots": ("bool", False, "invite_scope", "guild_config"),
        "invite_hard_block_target_bot_ids": ("id_list", (), "invite_scope", "guild_config"),
        "invite_hard_block_target_channel_ids": ("id_list", (), "invite_scope", "guild_config"),
        "invite_protected_poster_rule_enabled": ("bool", False, "invite_scope", "guild_config"),
    }

    assert set(expected) <= set(registry.PROTECTION_SETTING_SPECS)
    for key, (kind, default, owner, persistence) in expected.items():
        spec = registry.setting_spec(key)
        assert spec.kind == kind
        assert spec.default == default
        assert spec.owner == owner
        assert spec.persistence == persistence


def test_registry_is_schema_only_and_does_not_own_storage() -> None:
    source = (ROOT / "stoney_verify" / "settings_registry.py").read_text(encoding="utf-8")
    assert "get_supabase" not in source
    assert "upsert_guild_config" not in source
    assert "get_guild_config" not in source
    assert "save_spam_settings" not in source


def test_canonical_value_wins_over_legacy_alias() -> None:
    source = {
        "invite_hard_block_target_all_bots": False,
        "invite_target_all_bots": True,
        "spam_invite_hard_block_target_all_bots": True,
    }

    assert registry.setting_bool(
        source,
        registry.INVITE_TARGET_ALL_BOTS_KEY,
    ) is False


def test_nested_and_legacy_scope_aliases_normalize_consistently() -> None:
    source = {
        "settings": {
            "invite_target_all_bots": "yes",
            "invite_target_bot_ids": "<@123456789012345678>, 222222222222222222",
            "invite_target_channel_ids": "<#333333333333333333> 444444444444444444",
            "protected_poster_invite_rule_enabled": "on",
        }
    }

    assert registry.invite_scope_values(source) == {
        registry.INVITE_TARGET_ALL_BOTS_KEY: True,
        registry.INVITE_TARGET_BOT_IDS_KEY: [
            "123456789012345678",
            "222222222222222222",
        ],
        registry.INVITE_TARGET_CHANNEL_IDS_KEY: [
            "333333333333333333",
            "444444444444444444",
        ],
        registry.INVITE_PROTECTED_POSTER_RULE_KEY: True,
    }


def test_invite_shield_effective_state_preserves_all_current_sources() -> None:
    assert registry.invite_shield_enabled({"automod_block_invites": True}, {}) is True
    assert registry.invite_shield_enabled({}, {"invite_shield_enabled": True}) is True
    assert registry.invite_shield_enabled({}, {"invite_hard_block_enabled": True}) is True
    assert registry.invite_shield_enabled({}, {"automod_block_invites": True}) is True
    assert registry.invite_shield_enabled({}, {"block_invites": True}) is True
    assert registry.invite_shield_enabled({}, {}) is False


def test_link_shield_effective_state_preserves_config_and_spam_compatibility() -> None:
    assert registry.link_shield_enabled({"automod_block_links": True}, {}) is True
    assert registry.link_shield_enabled({}, {"automod_block_links": True}) is True
    assert registry.link_shield_enabled({}, {}) is False


def test_spam_guard_invite_defaults_and_prefixed_aliases_are_unchanged() -> None:
    defaults = spam_guard._default_settings(123)
    assert defaults["block_external_invites_only"] is True
    assert defaults["allow_server_invites"] is True

    normalized = spam_guard._normalize_settings(
        123,
        {
            "spam_block_external_invites_only": "false",
            "spam_allow_server_invites": "false",
        },
    )
    assert normalized["block_external_invites_only"] is False
    assert normalized["allow_server_invites"] is False

    conflict = spam_guard._normalize_settings(
        123,
        {
            "block_external_invites_only": True,
            "spam_block_external_invites_only": False,
            "allow_server_invites": True,
            "spam_allow_server_invites": False,
        },
    )
    assert conflict["block_external_invites_only"] is False
    assert conflict["allow_server_invites"] is False


def test_registry_wiring_replaces_duplicate_protection_setting_semantics() -> None:
    scope_source = (ROOT / "stoney_verify" / "invite_scope_settings.py").read_text(encoding="utf-8")
    policy_source = (ROOT / "stoney_verify" / "invite_policy_engine.py").read_text(encoding="utf-8")
    recovery_source = (ROOT / "stoney_verify" / "invite_reconciliation_runtime.py").read_text(encoding="utf-8")
    protection_source = (
        ROOT / "stoney_verify" / "commands_ext" / "public_protection_center.py"
    ).read_text(encoding="utf-8")
    spam_source = (ROOT / "stoney_verify" / "spam_guard.py").read_text(encoding="utf-8")

    assert "invite_scope_values" in scope_source
    assert "_registry_invite_shield_enabled" in policy_source
    assert "_registry_setting_ids" in policy_source
    assert "_registry_invite_shield_enabled" in recovery_source
    assert "_registry_invite_shield_enabled" in protection_source
    assert "_registry_link_shield_enabled" in protection_source
    assert "_registry_setting_bool" in spam_source


def test_choice_setting_rejects_unknown_value_to_registered_default() -> None:
    assert (
        registry.setting_value(
            {"automod_link_policy": "something-impossible"},
            registry.AUTOMOD_LINK_POLICY_KEY,
        )
        == "allow_links"
    )


def test_unknown_setting_fails_loudly() -> None:
    with pytest.raises(KeyError):
        registry.setting_spec("definitely_not_a_real_setting")
