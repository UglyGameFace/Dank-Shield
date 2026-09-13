from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from stoney_verify import anti_nuke


def _run(coro):
    return asyncio.run(coro)


def test_delegated_long_horizon_covers_known_low_and_slow_evasion_paths() -> None:
    required = {
        "channel_create", "channel_update", "channel_delete", "role_create", "role_update", "role_delete",
        "member_role_remove", "member_timeout", "ban", "kick", "member_prune", "webhook_create", "webhook_update", "webhook_delete",
    }
    assert required.issubset(anti_nuke._SLOW_BURN_ACTIONS)


def test_delegated_long_horizon_has_hard_emergency_ceiling() -> None:
    settings = anti_nuke.normalize_antinuke_settings({
        "antinuke_channel_delete_threshold": 25,
        "antinuke_role_delete_threshold": 25,
        "antinuke_ban_threshold": 50,
        "antinuke_kick_threshold": 50,
        "antinuke_webhook_create_threshold": 25,
    })
    assert anti_nuke._slow_burn_threshold(settings) == anti_nuke._DELEGATED_SLOW_BURN_CEILING
    assert anti_nuke._slow_burn_threshold(settings) <= 8


def test_channel_create_is_counted_as_destructive_activity(monkeypatch) -> None:
    calls: list[dict] = []
    guild = SimpleNamespace(id=10)
    channel = SimpleNamespace(id=20, name="raid-room", guild=guild)

    async def fake_handle(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "_handle_threshold_event", fake_handle)
    _run(anti_nuke.antinuke_on_guild_channel_create(channel))
    assert len(calls) == 1
    assert calls[0]["audit_action"] == "channel_create"
    assert calls[0]["action_key"] == "channel_create"


def test_channel_overwrite_mutation_is_counted(monkeypatch) -> None:
    calls: list[dict] = []
    guild = SimpleNamespace(id=10)
    before = SimpleNamespace(id=20, name="staff", guild=guild, overwrites={"a": 1})
    after = SimpleNamespace(id=20, name="staff", guild=guild, overwrites={"a": 2})

    async def fake_handle(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "_handle_threshold_event", fake_handle)
    _run(anti_nuke.antinuke_on_guild_channel_update(before, after))
    assert len(calls) == 1
    assert calls[0]["audit_action"] == "channel_update"
    assert calls[0]["action_key"] == "channel_update"


def test_role_permission_reduction_is_counted(monkeypatch) -> None:
    calls: list[dict] = []
    guild = SimpleNamespace(id=11)
    before_permissions = SimpleNamespace(administrator=False, manage_guild=False, manage_roles=True, manage_channels=False, ban_members=False, kick_members=False, manage_webhooks=False, moderate_members=False)
    after_permissions = SimpleNamespace(administrator=False, manage_guild=False, manage_roles=False, manage_channels=False, ban_members=False, kick_members=False, manage_webhooks=False, moderate_members=False)
    before = SimpleNamespace(id=30, name="Staff", guild=guild, permissions=before_permissions)
    after = SimpleNamespace(id=30, name="Staff", guild=guild, permissions=after_permissions)

    async def fake_handle(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "_handle_threshold_event", fake_handle)
    _run(anti_nuke.antinuke_on_guild_role_update(before, after))
    assert len(calls) == 1
    assert calls[0]["audit_action"] == "role_update"
    assert calls[0]["action_key"] == "role_update"


def test_member_role_removal_routes_through_canonical_destructive_engine(monkeypatch) -> None:
    calls: list[dict] = []
    role = SimpleNamespace(id=40, name="Staff", is_default=lambda: False)
    guild = SimpleNamespace(id=12)
    before = SimpleNamespace(id=50, roles=[role], guild=guild, mention="<@50>")
    after = SimpleNamespace(id=50, roles=[], guild=guild, mention="<@50>")
    entry = SimpleNamespace(id=60, user=SimpleNamespace(id=70))

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    async def fake_claim(*_args, **_kwargs):
        return entry

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_claim_recent_audit_entry", fake_claim)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    _run(anti_nuke._handle_member_role_removal(before, after))
    assert len(calls) == 1
    assert calls[0]["action_key"] == "member_role_remove"
    assert calls[0]["threshold_key"] == "antinuke_role_delete_threshold"


def test_member_timeout_routes_through_canonical_destructive_engine(monkeypatch) -> None:
    calls: list[dict] = []
    guild = SimpleNamespace(id=13)
    before = SimpleNamespace(id=51, guild=guild, mention="<@51>", timed_out_until=None)
    after = SimpleNamespace(id=51, guild=guild, mention="<@51>", timed_out_until=datetime.now(timezone.utc) + timedelta(minutes=30))
    entry = SimpleNamespace(id=61, user=SimpleNamespace(id=71))

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    async def fake_claim(*_args, **_kwargs):
        return entry

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_claim_recent_audit_entry", fake_claim)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    _run(anti_nuke._handle_member_timeout(before, after))
    assert len(calls) == 1
    assert calls[0]["action_key"] == "member_timeout"
    assert calls[0]["threshold_key"] == "antinuke_kick_threshold"


def test_moderate_members_is_a_containment_risk_permission() -> None:
    assert "moderate_members" in anti_nuke.DANGEROUS_PERMISSION_NAMES
