from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import action_notification_status as status
from stoney_verify.action_notification_owners import NOTIFICATION_OWNERS
from stoney_verify.commands_ext import public_action_notification_directory as ui


def _sources(monkeypatch, *, verify=True, quiet=True, world=True):
    from stoney_verify import guild_config, community_quiet_notice_service, somewhere_420_schedules

    async def verification(guild_id, *, refresh):
        assert (guild_id, refresh) == (123, True)
        return {"source": "supabase:guild_configs", "unverified_reminders_v1": {"enabled": verify}}

    async def quiet_notice(guild_id):
        assert guild_id == 123
        return SimpleNamespace(guild_id=123, enabled=quiet)

    async def world_schedule(kind, guild_id):
        assert (kind, guild_id) == ("guild", 123)
        return {"enabled": world}

    monkeypatch.setattr(guild_config, "get_guild_config", verification)
    monkeypatch.setattr(community_quiet_notice_service, "get_quiet_notice", quiet_notice)
    monkeypatch.setattr(somewhere_420_schedules, "get_schedule", world_schedule)


def test_statuses_only_read_three_canonical_guild_policies(monkeypatch):
    _sources(monkeypatch, verify=True, quiet=False, world=True)
    statuses = asyncio.run(status.inspect_saved_notification_status(123))
    assert [item.key for item in statuses] == [item.key for item in NOTIFICATION_OWNERS]
    by_key = {item.key: item for item in statuses}
    assert by_key["verification"].state == "saved_enabled"
    assert by_key["quiet"].state == "saved_disabled"
    assert by_key["world420"].state == "saved_enabled"
    assert all(by_key[key].state == "owner_managed" for key in ("activity", "tickets", "toke"))
    assert "not inspected" in by_key["tickets"].detail.lower()
    assert "Personal subscriptions are not inspected" in by_key["world420"].detail

    embed = ui._directory_embed(statuses)
    assert "Saved enabled" in str(embed.to_dict())
    assert "Saved disabled" in str(embed.to_dict())
    assert "Owner-managed" in str(embed.to_dict())
    assert "Not delivery or worker health" in embed.footer.text
    assert len(embed.fields) == 6
    assert all(len(str(field.value)) <= 1024 for field in embed.fields)


def test_missing_schedule_is_not_confused_with_disabled_personal_opt_in(monkeypatch):
    from stoney_verify import community_quiet_notice_service, somewhere_420_schedules
    _sources(monkeypatch, verify=False)
    async def no_quiet(_guild_id):
        return None
    async def no_world(_kind, _guild_id):
        return None
    monkeypatch.setattr(community_quiet_notice_service, "get_quiet_notice", no_quiet)
    monkeypatch.setattr(somewhere_420_schedules, "get_schedule", no_world)
    current = {x.key: x for x in asyncio.run(status.inspect_saved_notification_status(123))}
    assert current["verification"].state == "saved_disabled"
    assert current["quiet"].state == "not_configured"
    assert current["world420"].state == "not_configured"
    assert "Personal member reminders are separate" in current["world420"].detail


def test_database_unavailability_fails_closed_independently_and_hides_secrets(monkeypatch):
    from stoney_verify import guild_config, community_quiet_notice_service
    _sources(monkeypatch)
    async def unavailable(_guild_id, *, refresh):
        return {"source": "unavailable:db_read_failed",
                "unverified_reminders_v1": {"enabled": True}}
    async def leak(_guild_id):
        raise RuntimeError("postgresql://username:secret@private-discloud")
    monkeypatch.setattr(guild_config, "get_guild_config", unavailable)
    monkeypatch.setattr(community_quiet_notice_service, "get_quiet_notice", leak)
    by_key = {x.key: x for x in asyncio.run(status.inspect_saved_notification_status(123))}
    assert by_key["verification"].state == "unavailable"
    assert by_key["quiet"].state == "unavailable"
    assert by_key["world420"].state == "saved_enabled"
    rendered = str(ui._directory_embed(tuple(by_key.values())).to_dict())
    assert "secret" not in rendered
    assert "private-discloud" not in rendered


def test_bad_policy_data_and_timeouts_never_claim_disabled(monkeypatch):
    from stoney_verify import guild_config, community_quiet_notice_service, somewhere_420_schedules
    _sources(monkeypatch)
    async def quiet_bad(_guild_id):
        return SimpleNamespace(guild_id=124, enabled=True)
    async def schedule_bad(_kind, _guild_id):
        return {"enabled": "true"}
    async def verify_bad(_guild_id, *, refresh):
        return {"source": "unconfigured:cache_miss",
                "unverified_reminders_v1": {"enabled": True}}
    monkeypatch.setattr(community_quiet_notice_service, "get_quiet_notice", quiet_bad)
    monkeypatch.setattr(somewhere_420_schedules, "get_schedule", schedule_bad)
    monkeypatch.setattr(guild_config, "get_guild_config", verify_bad)
    results = asyncio.run(status.inspect_saved_notification_status(123))
    assert all(s.state == "unavailable" for s in results if s.key in
               ("verification", "quiet", "world420"))


def test_private_directory_status_button_and_no_new_workflows():
    view = ui.NotificationOwnerDirectoryView(42, 123)
    labels = {c.label for c in view.children if isinstance(c, discord.ui.Button)}
    assert labels == {"Check Saved Status", "Back to Action Center"}
    assert len(view.children) <= 25
    assert all((getattr(c, "row", 0) or 0) <= 4 for c in view.children)

    for module in (status, ui):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("create_task(", "member.send(", "channel.send(",
                          "reserve_member_setup_sync(", ".upsert(", ".insert(", ".rpc("):
            assert forbidden not in source

    assert "scoped_interaction_is_staff(interaction)" in Path(ui.__file__).read_text()
    assert "CHECK_TIMEOUT_SECONDS = 8.0" in Path(status.__file__).read_text()
    assert ui._directory_embed().footer.text.startswith("Staff only")
    with pytest.raises(ValueError):
        asyncio.run(status.inspect_saved_notification_status(0))
