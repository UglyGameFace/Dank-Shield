from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_guardian_runtime as guardian


class FakeGuild:
    def __init__(self) -> None:
        self.id = 9901
        self.owner_id = 999999
        self.members: dict[int, object] = {}

    def get_member(self, user_id: int):
        return self.members.get(int(user_id))

    async def fetch_member(self, user_id: int):
        return self.members[int(user_id)]


class FakeEntry:
    def __init__(self, entry_id: int, action_name: str, guild: FakeGuild, actor) -> None:
        self.id = entry_id
        self.action = SimpleNamespace(name=action_name)
        self.guild = guild
        self.user = actor
        self.user_id = actor.id
        self.target = SimpleNamespace(id=entry_id + 100, name="target")


def _actor(user_id: int):
    return SimpleNamespace(id=user_id, roles=[], mention=f"<@{user_id}>")


def _reset() -> None:
    anti_nuke._SEEN_AUDIT_ENTRY_IDS.clear()
    guardian._PANIC_EVENTS.clear()
    guardian._PANIC_UNTIL.clear()


def test_multiple_executors_trigger_guild_wide_circuit_breaker(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    first = _actor(501)
    second = _actor(502)
    guild.members[501] = first
    guild.members[502] = second
    overrides: list[int | None] = []
    peers: list[int] = []
    incidents: list[str] = []

    async def fake_process(_guild, **kwargs):
        overrides.append(kwargs.get("threshold_override"))
        return True

    async def fake_contain(_guild, actor):
        peers.append(int(actor.id))
        return ["removed member from server"], []

    async def fake_incident(_guild, **kwargs):
        incidents.append(kwargs["title"])

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_process)
    monkeypatch.setattr(guardian, "_contain_peer", fake_contain)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_incident)

    events = [
        FakeEntry(10, "channel_delete", guild, first),
        FakeEntry(11, "emoji_delete", guild, second),
        FakeEntry(12, "invite_delete", guild, first),
    ]
    for event in events:
        asyncio.run(guardian._on_audit_log_entry_create(event))

    assert overrides[:2] == [None, None]
    assert overrides[2] == 1
    assert peers == [502]
    assert incidents == ["🚨 AntiNuke Coordinated Panic"]
    assert guardian._PANIC_UNTIL[guild.id] > 0
    _reset()


def test_broad_guardian_actions_reuse_canonical_long_horizon_keys() -> None:
    slow = set(anti_nuke._SLOW_BURN_ACTIONS)
    for action_name, (_label, _threshold, counter_key, _override) in guardian._ACTIONS.items():
        assert counter_key in slow, action_name


def test_security_control_and_identity_surfaces_are_present() -> None:
    expected = {
        "guild_update",
        "overwrite_update",
        "invite_delete",
        "emoji_delete",
        "sticker_delete",
        "scheduled_event_delete",
        "automod_rule_update",
        "automod_rule_delete",
        "app_command_permission_update",
    }
    assert expected.issubset(guardian._ACTIONS)
