from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.tickets_new import open_notifications as alerts


ROOT = Path(__file__).resolve().parents[1]
CLEAN = (ROOT / "stoney_verify/commands_ext/public_ticket_panel_clean.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/tickets_new/service.py").read_text(encoding="utf-8")
CENTER = (ROOT / "stoney_verify/commands_ext/public_ticket_command_center.py").read_text(encoding="utf-8")


def test_category_override_fallback_and_mute_are_isolated() -> None:
    settings = alerts.normalize_settings({
        "enabled": True,
        "role_ids": ["42", "42", "43", "invalid"],
        "category_roles": {
            "verification_issue": ["90"],
            "sensitive": [],
        },
    })
    assert settings["role_ids"] == [42, 43]
    assert alerts.selected_role_ids(settings, "verification-issue", [8]) == [90]
    assert alerts.selected_role_ids(settings, "sensitive", [8]) == []
    assert alerts.selected_role_ids(settings, "support", [8]) == [42, 43]
    assert alerts.selected_role_ids(alerts.normalize_settings({}), "support", [8]) == [8]
    assert alerts.normalize_settings({"enabled": False})["enabled"] is False


def test_roles_cannot_target_everyone_or_unbounded_roles() -> None:
    settings = alerts.normalize_settings({"role_ids": list(range(1, 500))})
    assert len(settings["role_ids"]) == alerts.MAX_ROLES
    assert alerts.normalize_settings({"category_roles": {"custom": "not-an-array"}})["category_roles"] == {}


def test_atomic_staff_alert_settings_retry_conflict(monkeypatch) -> None:
    async def scenario() -> None:
        current = [
            {"source": "supabase:guild_configs", alerts.SETTINGS_KEY: None},
            {"source": "supabase:guild_configs", alerts.SETTINGS_KEY: {"enabled": False, "role_ids": [5]}},
        ]
        expected_values = []

        async def get_cfg(_guild_id, refresh=False):
            assert refresh is True
            return current.pop(0)

        async def cas(_guild_id, key, *, expected, value, source):
            assert key == alerts.SETTINGS_KEY
            expected_values.append(expected)
            return len(expected_values) == 2, {}

        monkeypatch.setattr(alerts, "get_guild_config", get_cfg)
        monkeypatch.setattr(alerts, "compare_and_swap_guild_config_key", cas)
        settings = await alerts.save_settings(100, {"enabled": True})
        assert settings["enabled"] is True
        assert settings["role_ids"] == [5]
        assert expected_values == [None, {"enabled": False, "role_ids": [5]}]

    asyncio.run(scenario())


def test_unavailable_server_config_fails_closed(monkeypatch) -> None:
    async def scenario() -> None:
        async def get_cfg(_guild_id, refresh=False):
            return {"source": "unavailable:supabase_none", "staff_role_id": "999"}

        async def forbidden(*args, **kwargs):
            raise AssertionError("must not persist without per-guild database")

        monkeypatch.setattr(alerts, "get_guild_config", get_cfg)
        monkeypatch.setattr(alerts, "compare_and_swap_guild_config_key", forbidden)
        settings, available = await alerts.load_settings(33)
        assert available is False
        assert settings["enabled"] is True

        import pytest
        with pytest.raises(RuntimeError, match="unavailable"):
            await alerts.save_settings(33, {"enabled": True})

    asyncio.run(scenario())


def test_new_ticket_ping_is_single_role_scoped_and_does_not_ping_users(monkeypatch) -> None:
    async def scenario() -> None:
        messages = []
        staff_role = SimpleNamespace(
            id=33, mention="<@&33>", mentionable=True, is_default=lambda: False,
        )
        everyone = SimpleNamespace(id=1)
        bot = SimpleNamespace(id=7)
        guild = SimpleNamespace(
            id=5, default_role=everyone, me=bot,
            get_role=lambda rid: staff_role if rid == 33 else None,
        )
        perms = SimpleNamespace(
            view_channel=True, send_messages=True, mention_everyone=False,
        )

        class FakeChannel:
            id = 500
            mention = "<#500>"
            guild = guild

            def permissions_for(self, member):
                return perms

            async def send(self, content, *, allowed_mentions):
                messages.append((content, allowed_mentions))

        async def settings(_guild_id):
            return alerts.normalize_settings({}), True

        async def no_external_channel(_guild):
            return None

        monkeypatch.setattr(alerts, "load_settings", settings)
        monkeypatch.setattr(alerts, "alert_channel_for_guild", no_external_channel)
        channel = FakeChannel()
        owner = SimpleNamespace(id=89)

        assert await alerts.notify_new_ticket(
            channel, owner=owner, category="support", default_staff_role_ids=[33],
        ) is True
        assert len(messages) == 1
        body, allowed = messages[0]
        assert "<@&33>" in body and "<#500>" in body
        assert allowed.everyone is False
        assert allowed.users is False
        assert allowed.roles == [staff_role]
        assert await alerts.notify_new_ticket(
            channel, owner=owner, category="support", default_staff_role_ids=[33],
            is_ghost=True,
        ) is False
        assert len(messages) == 1

    asyncio.run(scenario())


def test_disabled_role_ping_never_contacts_discord(monkeypatch) -> None:
    async def scenario() -> None:
        async def settings(_guild_id):
            return {"enabled": False}, True

        monkeypatch.setattr(alerts, "load_settings", settings)
        channel = SimpleNamespace(guild=SimpleNamespace(id=123))
        assert await alerts.notify_new_ticket(
            channel, owner=SimpleNamespace(id=20), category="support",
        ) is False

    asyncio.run(scenario())


def test_both_real_creation_paths_alert_once_without_reopen_or_reuse() -> None:
    clean_create = CLEAN.split("async def _create_ticket(", 1)[1].split("\ndef _category_embed(", 1)[0]
    service_create = SERVICE.split("async def create_ticket_channel(", 1)[1].split("\nasync def sync_existing_ticket_channel(", 1)[0]
    assert clean_create.count("await notify_new_ticket(") == 1
    assert service_create.count("await notify_new_ticket(") == 1
    assert clean_create.index("_create_synced_ticket_channel(") < clean_create.index("await notify_new_ticket(")
    assert service_create.index("repo_note_ticket_channel_opened(channel.id)") < service_create.index("await notify_new_ticket(")
    assert "return existing_channel" in service_create
    assert service_create.index("return existing_channel") < service_create.index("await notify_new_ticket(")
    assert "is_ghost=is_ghost" in service_create
    assert "roles=False" in CLEAN and "roles=False" in SERVICE


def test_alerts_are_managed_in_existing_mobile_ticket_operations() -> None:
    assert 'label="Staff Alerts"' in CENTER
    assert "class TicketNotificationSettingsView(_OwnedView):" in CENTER
    assert "class TicketNotificationRoleSelect(discord.ui.RoleSelect):" in CENTER
    assert "class TicketNotificationChannelSelect(discord.ui.ChannelSelect):" in CENTER
    assert "class TicketNotificationCategorySelect(discord.ui.Select):" in CENTER
    assert "_require_ticket_notification_manager" in CENTER
    assert "staff_alert_channel_id" in CENTER
