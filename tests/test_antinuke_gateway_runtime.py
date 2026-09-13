from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_gateway_runtime as gateway
from stoney_verify.globals import bot


class FakeBot:
    def __init__(self) -> None:
        self.intents = SimpleNamespace(moderation=True)
        self.listeners: list[tuple[object, str]] = []

    def add_listener(self, callback, name: str) -> None:
        self.listeners.append((callback, name))


class FakeEntry:
    def __init__(
        self,
        entry_id: int,
        action_name: str,
        *,
        guild=None,
        actor=None,
        target=None,
    ) -> None:
        self.id = entry_id
        self.action = SimpleNamespace(name=action_name)
        self.guild = guild or SimpleNamespace(id=123, owner_id=999)
        self.user = actor or SimpleNamespace(id=444, roles=[], mention="<@444>")
        self.target = target or SimpleNamespace(id=555, name="target")


def _reset_runtime_state() -> None:
    anti_nuke._ACTION_WINDOWS.clear()
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    anti_nuke._AUDIT_CLAIM_LOCKS.clear()
    anti_nuke._CONTAINMENT_LOCKS.clear()


def test_install_adds_one_gateway_listener_without_mutating_canonical_policy() -> None:
    fake_bot = FakeBot()
    original_actions = anti_nuke._SLOW_BURN_ACTIONS

    installed = gateway.install_anti_nuke_gateway_runtime(fake_bot)

    assert installed is True
    assert any(name == "on_audit_log_entry_create" for _cb, name in fake_bot.listeners)
    assert anti_nuke._SLOW_BURN_ACTIONS is original_actions
    assert gateway.install_anti_nuke_gateway_runtime(fake_bot) is False
    assert len(fake_bot.listeners) == 1


def test_production_bot_has_moderation_intent_for_audit_gateway() -> None:
    assert bool(getattr(bot.intents, "moderation", False)) is True


def test_gateway_destructive_entry_uses_canonical_engine_without_rest_lookup(monkeypatch) -> None:
    _reset_runtime_state()
    guild = SimpleNamespace(id=321, owner_id=999)
    entry = FakeEntry(
        7001,
        "channel_delete",
        guild=guild,
        target=SimpleNamespace(id=55, name="general"),
    )
    calls: list[dict] = []

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs)
        return True

    async def rest_lookup_must_not_run(*_args, **_kwargs):
        raise AssertionError("gateway fast path must not query REST audit logs")

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    monkeypatch.setattr(anti_nuke, "_find_recent_audit_entry", rest_lookup_must_not_run)

    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert len(calls) == 1
    assert calls[0]["action_key"] == "channel_delete"
    assert calls[0]["threshold_key"] == "antinuke_channel_delete_threshold"
    assert "general" in calls[0]["target_label"]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset_runtime_state()


def test_channel_create_also_uses_gateway_fast_path(monkeypatch) -> None:
    _reset_runtime_state()
    entry = FakeEntry(
        7002,
        "channel_create",
        target=SimpleNamespace(id=77, name="raid-room"),
    )
    calls: list[dict] = []

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)

    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert len(calls) == 1
    assert calls[0]["action_key"] == "channel_create"
    assert calls[0]["threshold_key"] == "antinuke_channel_delete_threshold"
    _reset_runtime_state()


def test_gateway_and_rest_paths_cannot_double_enforce_same_entry(monkeypatch) -> None:
    _reset_runtime_state()
    entry = FakeEntry(7003, "ban")
    calls: list[dict] = []

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)

    asyncio.run(gateway._on_audit_log_entry_create(entry))
    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert len(calls) == 1
    assert calls[0]["action_key"] == "ban"
    _reset_runtime_state()


def test_harmless_role_create_is_not_consumed_and_dropped(monkeypatch) -> None:
    _reset_runtime_state()
    entry = FakeEntry(
        7004,
        "role_create",
        target=SimpleNamespace(id=88, name="Cosmetic", permissions=None),
    )
    calls: list[dict] = []

    async def fake_process(_guild, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)

    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert len(calls) == 1
    assert calls[0]["action_key"] == "role_create"
    assert calls[0]["threshold_key"] == "antinuke_role_delete_threshold"
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset_runtime_state()


def test_dangerous_role_create_routes_to_immediate_gateway_handler(monkeypatch) -> None:
    _reset_runtime_state()
    entry = FakeEntry(7005, "role_create")
    calls: list[int] = []

    async def fake_role_handler(_guild, found_entry):
        calls.append(int(found_entry.id))

    monkeypatch.setattr(gateway, "_handle_role_create", fake_role_handler)

    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert calls == [7005]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset_runtime_state()


def test_main_installs_gateway_runtime_before_app_run() -> None:
    source = Path("main.py").read_text(encoding="utf-8")

    assert "def _install_anti_nuke_gateway_runtime" in source
    assert "_install_anti_nuke_gateway_runtime()" in source
    assert source.index("_install_anti_nuke_gateway_runtime()") < source.index(
        "from stoney_verify.app import run as _run_dank_shield"
    )
