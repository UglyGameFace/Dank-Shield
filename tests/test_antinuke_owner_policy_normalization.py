from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_incident_runtime as incident
from stoney_verify import anti_nuke_gateway_runtime as gateway
from stoney_verify import anti_nuke_guardian_runtime as guardian


def _settings() -> dict:
    return anti_nuke.normalize_antinuke_settings(
        {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_window_seconds": 15,
            "antinuke_channel_delete_threshold": 2,
            "antinuke_role_delete_threshold": 2,
            "antinuke_ban_threshold": 3,
            "antinuke_kick_threshold": 3,
            "antinuke_trusted_role_ids": [9001],
        }
    )


def _entry(action_name: str, owner, **kwargs):
    return SimpleNamespace(
        action=SimpleNamespace(name=action_name),
        user=owner,
        **kwargs,
    )


def _install_owner_test_doubles(monkeypatch):
    incidents: list[dict] = []

    async def fake_settings(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return _settings()

    async def fake_post(_guild, **kwargs):
        incidents.append(dict(kwargs))

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)
    anti_nuke._ACTION_WINDOWS.clear()  # noqa: SLF001
    anti_nuke._TRIGGER_COOLDOWNS.clear()  # noqa: SLF001
    return incidents


def test_guardian_guild_update_fields_match_owner_severity_policy() -> None:
    assert guardian._GUILD_UPDATE_IMMEDIATE_FIELDS == incident._OWNER_GUILD_IMMEDIATE_FIELDS  # noqa: SLF001
    assert guardian._GUILD_UPDATE_BOUNDED_FIELDS == incident._OWNER_GUILD_BOUNDED_FIELDS  # noqa: SLF001
    assert guardian._GUILD_UPDATE_ROUTINE_FIELDS == incident._OWNER_GUILD_ROUTINE_FIELDS  # noqa: SLF001


def test_guardian_role_routine_fields_match_owner_severity_policy() -> None:
    assert guardian._ROLE_UPDATE_ROUTINE_FIELDS == incident._OWNER_ROLE_ROUTINE_FIELDS  # noqa: SLF001


def test_cosmetic_owner_role_edit_is_not_compromise_evidence(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=1, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")
    before = SimpleNamespace(
        permissions=SimpleNamespace(administrator=False),
        name="old",
        position=5,
    )
    after = SimpleNamespace(
        permissions=SimpleNamespace(administrator=False),
        name="new",
        position=5,
    )

    result = asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("role_update", owner, before=before, after=after),
            action_key="role_update",
            action_label="Role hierarchy/settings mutation",
            target_label="@helpers",
            threshold_key="antinuke_role_delete_threshold",
            threshold_override=1,
        )
    )

    assert result is True
    assert incidents == []
    assert anti_nuke._ACTION_WINDOWS == {}  # noqa: SLF001


def test_cosmetic_owner_guild_edit_is_not_compromise_evidence(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=2, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry(
                "guild_update",
                owner,
                before=SimpleNamespace(name="The 420 Lobby"),
                after=SimpleNamespace(name="The 420 Garden"),
            ),
            action_key="channel_update",
            action_label="Server identity/security mutation",
            target_label="Server settings • name",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=1,
        )
    )

    assert incidents == []


def test_owner_channel_creation_is_bounded_even_when_legacy_wrapper_passes_one(
    monkeypatch,
) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=3, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    for index in range(4):
        asyncio.run(
            incident._process_owner_destructive_event(  # noqa: SLF001
                guild,
                entry=_entry("channel_create", owner),
                action_key="channel_create",
                action_label="Channel creation",
                target_label=f"#new-{index}",
                threshold_key="antinuke_channel_delete_threshold",
                threshold_override=1,
            )
        )
    assert incidents == []

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("channel_create", owner),
            action_key="channel_create",
            action_label="Channel creation",
            target_label="#new-5",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=1,
        )
    )

    assert len(incidents) == 1
    assert incidents[0]["title"] == "⚠️ AntiNuke Owner Activity Burst Warning"
    assert "channel_create 5/5" in incidents[0]["count_label"]


def test_owner_channel_delete_remains_immediate_compromise_warning(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=4, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("channel_delete", owner),
            action_key="channel_delete",
            action_label="Channel deletion",
            target_label="#general",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=99,
        )
    )

    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Owner-Compromise Warning"
    assert "channel_delete 1/1" in incidents[0]["count_label"]


def test_owner_mfa_mutation_remains_immediate_security_warning(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=5, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry(
                "guild_update",
                owner,
                before=SimpleNamespace(mfa_level=0),
                after=SimpleNamespace(mfa_level=1),
            ),
            action_key="channel_update",
            action_label="Server identity/security mutation",
            target_label="Server settings • mfa_level",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=1,
        )
    )

    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Owner-Compromise Warning"


def test_dangerous_owner_role_create_enters_canonical_owner_policy(monkeypatch) -> None:
    captured: list[dict] = []

    async def fake_process(_guild, **kwargs):
        captured.append(dict(kwargs))
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    monkeypatch.setattr(anti_nuke, "role_has_dangerous_permissions", lambda _role: True)

    guild = SimpleNamespace(id=6, owner_id=42)
    owner = SimpleNamespace(id=42)
    role = SimpleNamespace(id=77, name="Administrator")
    entry = _entry("role_create", owner, target=role)

    handled = asyncio.run(
        incident._process_owner_special_action(  # noqa: SLF001
            guild,
            entry,
            owner,
            "role_create",
            consume_entry=False,
        )
    )

    assert handled is True
    assert len(captured) == 1
    assert captured[0]["action_key"] == "role_create"
    assert captured[0]["threshold_override"] == 1
    assert "Dangerous role created" in captured[0]["action_label"]


def test_dangerous_owner_role_permission_escalation_enters_canonical_policy(
    monkeypatch,
) -> None:
    captured: list[dict] = []

    async def fake_process(_guild, **kwargs):
        captured.append(dict(kwargs))
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    before = SimpleNamespace(permissions=SimpleNamespace(administrator=False))
    after = SimpleNamespace(permissions=SimpleNamespace(administrator=True))
    guild = SimpleNamespace(id=7, owner_id=42)
    owner = SimpleNamespace(id=42)
    entry = _entry(
        "role_update",
        owner,
        before=before,
        after=after,
        target=SimpleNamespace(id=88, name="Mods"),
    )

    handled = asyncio.run(
        incident._process_owner_special_action(  # noqa: SLF001
            guild,
            entry,
            owner,
            "role_update",
            consume_entry=False,
        )
    )

    assert handled is True
    assert captured[0]["action_key"] == "role_update"
    assert captured[0]["threshold_override"] == 1
    assert "administrator" in captured[0]["action_label"]


def test_owner_sensitive_member_role_grant_is_not_silently_skipped(monkeypatch) -> None:
    captured: list[dict] = []

    async def fake_process(_guild, **kwargs):
        captured.append(dict(kwargs))
        return True

    async def fake_settings(_guild_id: int, *, refresh: bool = False):
        _ = refresh
        return _settings()

    async def fake_target(_guild, _entry):
        return SimpleNamespace(id=100, bot=False)

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(gateway, "_resolve_target_member", fake_target)
    monkeypatch.setattr(
        anti_nuke,
        "role_has_dangerous_permissions",
        lambda role: int(getattr(role, "id", 0)) == 55,
    )

    guild = SimpleNamespace(id=8, owner_id=42)
    owner = SimpleNamespace(id=42)
    role = SimpleNamespace(id=55, name="Danger")
    entry = _entry(
        "member_role_update",
        owner,
        before=SimpleNamespace(roles=[]),
        after=SimpleNamespace(roles=[role]),
        target=SimpleNamespace(id=100, name="member", mention="<@100>"),
    )

    handled = asyncio.run(
        incident._process_owner_special_action(  # noqa: SLF001
            guild,
            entry,
            owner,
            "member_role_update",
            consume_entry=False,
        )
    )

    assert handled is True
    assert captured[0]["action_key"] == "role_update"
    assert captured[0]["threshold_override"] == 1
    assert "security-sensitive role" in captured[0]["action_label"]


def test_owner_role_color_edit_is_cosmetic_not_sparse_authority(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=9, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")
    before = SimpleNamespace(
        permissions=SimpleNamespace(administrator=False),
        colour=1,
        position=5,
    )
    after = SimpleNamespace(
        permissions=SimpleNamespace(administrator=False),
        colour=2,
        position=5,
    )

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("role_update", owner, before=before, after=after),
            action_key="role_update",
            action_label="Role settings mutation",
            target_label="@helpers",
            threshold_key="antinuke_role_delete_threshold",
            threshold_override=1,
        )
    )

    assert incidents == []
    assert anti_nuke._ACTION_WINDOWS == {}  # noqa: SLF001


def test_owner_single_message_delete_is_bounded_not_one_strike(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=10, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    for index in range(4):
        asyncio.run(
            incident._process_owner_destructive_event(  # noqa: SLF001
                guild,
                entry=_entry("message_delete", owner),
                action_key="message_delete",
                action_label="Message deletion",
                target_label=f"message-{index}",
                threshold_key="antinuke_channel_delete_threshold",
                threshold_override=1,
            )
        )
    assert incidents == []

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("message_delete", owner),
            action_key="message_delete",
            action_label="Message deletion",
            target_label="message-5",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=1,
        )
    )

    assert len(incidents) == 1
    assert incidents[0]["title"] == "⚠️ AntiNuke Owner Activity Burst Warning"
    assert "message_delete 5/5" in incidents[0]["count_label"]


def test_owner_onboarding_update_is_bounded_not_one_strike(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=11, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("onboarding_update", owner),
            action_key="onboarding_update",
            action_label="Guild onboarding mutation",
            target_label="Onboarding",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=1,
        )
    )

    assert incidents == []


def test_owner_stage_delete_is_bounded_not_one_strike(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=12, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("stage_instance_delete", owner),
            action_key="stage_delete",
            action_label="Stage instance deletion",
            target_label="Stage",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=1,
        )
    )

    assert incidents == []


def test_owner_webhook_update_is_bounded_not_one_strike(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=13, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("webhook_update", owner),
            action_key="webhook_update",
            action_label="Webhook mutation",
            target_label="Webhook",
            threshold_key="antinuke_webhook_create_threshold",
            threshold_override=1,
        )
    )

    assert incidents == []
    assert anti_nuke._ACTION_WINDOWS  # noqa: SLF001


def test_owner_webhook_delete_remains_immediate(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=14, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("webhook_delete", owner),
            action_key="webhook_delete",
            action_label="Webhook deletion",
            target_label="Webhook",
            threshold_key="antinuke_webhook_create_threshold",
            threshold_override=None,
        )
    )

    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Owner-Compromise Warning"


def test_owner_integration_create_is_bounded_for_legitimate_oauth_flow(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=15, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("integration_create", owner),
            action_key="integration_create",
            action_label="Integration creation",
            target_label="Integration",
            threshold_key="antinuke_role_delete_threshold",
            threshold_override=1,
        )
    )

    assert incidents == []
    assert anti_nuke._ACTION_WINDOWS  # noqa: SLF001


def test_owner_integration_delete_remains_immediate(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=16, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry("integration_delete", owner),
            action_key="role_update",
            action_label="Integration deletion",
            target_label="Integration",
            threshold_key="antinuke_role_delete_threshold",
            threshold_override=None,
        )
    )

    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Owner-Compromise Warning"


def test_owner_vanity_change_is_bounded_not_cosmetic_or_immediate(monkeypatch) -> None:
    incidents = _install_owner_test_doubles(monkeypatch)
    guild = SimpleNamespace(id=17, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")

    asyncio.run(
        incident._process_owner_destructive_event(  # noqa: SLF001
            guild,
            entry=_entry(
                "guild_update",
                owner,
                before=SimpleNamespace(vanity_url_code="old"),
                after=SimpleNamespace(vanity_url_code="new"),
            ),
            action_key="channel_update",
            action_label="Server identity/security mutation",
            target_label="Server settings • vanity_url_code",
            threshold_key="antinuke_channel_delete_threshold",
            threshold_override=1,
        )
    )

    assert incidents == []
    assert anti_nuke._ACTION_WINDOWS  # noqa: SLF001
