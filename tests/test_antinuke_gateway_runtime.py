from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_gateway_runtime as gateway
from stoney_verify import anti_nuke_guardian_runtime as guardian
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
        self.user_id = getattr(self.user, "id", None)
        self.target = target or SimpleNamespace(id=555, name="target")


def _reset_runtime_state() -> None:
    anti_nuke._ACTION_WINDOWS.clear()
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    anti_nuke._AUDIT_CLAIM_LOCKS.clear()
    anti_nuke._CONTAINMENT_LOCKS.clear()
    guardian._PANIC_EVENTS.clear()
    guardian._PANIC_UNTIL.clear()


def test_install_adds_gateway_and_overwrite_fallback_without_mutating_policy() -> None:
    fake_bot = FakeBot()
    original_actions = anti_nuke._SLOW_BURN_ACTIONS

    installed = gateway.install_anti_nuke_gateway_runtime(fake_bot)

    assert installed is True
    names = [name for _cb, name in fake_bot.listeners]
    assert names.count("on_audit_log_entry_create") == 1
    assert names.count("on_guild_channel_update") == 1
    assert anti_nuke._SLOW_BURN_ACTIONS is original_actions
    assert gateway.install_anti_nuke_gateway_runtime(fake_bot) is False
    assert len(fake_bot.listeners) == 2


def test_production_bot_has_moderation_intent_for_audit_gateway() -> None:
    assert bool(getattr(bot.intents, "moderation", False)) is True


def test_gateway_destructive_entry_uses_canonical_engine_without_rest_lookup(
    monkeypatch,
) -> None:
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


def test_bot_add_gateway_removes_new_bot_and_contains_inviter(monkeypatch) -> None:
    _reset_runtime_state()
    kicked: list[int] = []
    contained: list[int] = []
    incidents: list[str] = []

    async def fake_kick(member, *, reason=None):
        kicked.append(int(member.id))

    guild = SimpleNamespace(id=711, owner_id=999, kick=fake_kick)
    actor = SimpleNamespace(id=444, roles=[], mention="<@444>")
    target = SimpleNamespace(id=555, name="raid-bot", bot=True)
    entry = FakeEntry(7006, "bot_add", guild=guild, actor=actor, target=target)

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_mode": "contain"}
        )

    async def fake_contain(_guild, found_actor):
        contained.append(int(found_actor.id))
        return ["removed member from server"], []

    async def fake_incident(_guild, **kwargs):
        incidents.append(kwargs["title"])

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(guardian, "_contain_peer", fake_contain)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_incident)

    asyncio.run(gateway._on_audit_log_entry_create(entry))

    assert kicked == [555]
    assert contained == [444]
    assert incidents == ["🚨 AntiNuke Unauthorized Bot Added"]
    assert anti_nuke._audit_entry_seen(entry) is True
    _reset_runtime_state()


def test_overwrite_fallback_uses_explicit_actions_and_channel_target(monkeypatch) -> None:
    _reset_runtime_state()
    calls: list[tuple[tuple[str, ...], int | None]] = []
    guild = SimpleNamespace(id=812)
    before = SimpleNamespace(id=91, guild=guild, overwrites={"role": "old"})
    after = SimpleNamespace(id=91, guild=guild, overwrites={"role": "new"})

    async def fake_settings(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    async def fake_claim(_guild, action_names, *, target_id=None, retries=3):
        calls.append((tuple(action_names), target_id))
        return None

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(guardian, "_claim_priority_entry", fake_claim)
    monkeypatch.setattr(guardian.asyncio, "sleep", lambda *_args, **_kwargs: _noop())

    asyncio.run(guardian._on_guild_channel_update_fallback(before, after))

    assert calls == [(guardian._OVERWRITE_ACTIONS, 91)]
    _reset_runtime_state()


async def _noop() -> None:
    return None


def test_benign_member_facing_creation_actions_are_not_first_strike_guarded() -> None:
    for action_name in (
        "invite_create",
        "invite_update",
        "emoji_create",
        "emoji_update",
        "sticker_create",
        "sticker_update",
        "scheduled_event_create",
        "scheduled_event_update",
    ):
        assert action_name not in guardian._ACTIONS


def test_panic_covers_high_risk_creation_and_authority_paths() -> None:
    expected = {
        "bot_add",
        "channel_create",
        "role_create",
        "webhook_create",
        "automod_rule_create",
        "overwrite_update",
        "role_update",
    }
    assert expected.issubset(guardian._PANIC_ACTIONS)


def test_main_installs_gateway_runtime_through_pre_app_coordinator() -> None:
    main_source = Path("main.py").read_text(encoding="utf-8")
    coordinator_source = Path(
        "stoney_verify/anti_nuke_runtime_coordinator.py"
    ).read_text(encoding="utf-8")

    assert '"gateway"' in coordinator_source
    assert '"install_anti_nuke_gateway_runtime"' in coordinator_source
    assert "def _install_anti_nuke_gateway_runtime" not in main_source
    assert main_source.index("    install_anti_nuke_pre_app(bot)") < main_source.index(
        "from stoney_verify.app import run as _run_dank_shield"
    )

