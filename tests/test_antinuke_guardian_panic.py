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

    async def get_settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)
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


def test_alert_only_panic_never_contain_peers(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    incidents: list[str] = []

    async def get_settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "alert"}

    async def forbidden_containment(_guild, _actor):
        raise AssertionError("Alert Only must not kick, ban, or remove roles")

    async def incident(_guild, **kwargs):
        incidents.append(str(kwargs["response_label"]))

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)
    monkeypatch.setattr(guardian, "_contain_peer", forbidden_containment)
    monkeypatch.setattr(anti_nuke, "_post_incident", incident)

    asyncio.run(
        guardian._post_panic_incident(
            guild,
            actor=_actor(501),
            action_label="Channel deletion",
            target_label="general",
            observed=[_actor(501), _actor(502)],
        )
    )

    assert len(incidents) == 1
    assert "no peer containment attempted" in incidents[0]
    _reset()


def test_panic_does_not_contain_when_policy_unavailable_or_disabled(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    responses: list[str] = []

    async def forbidden_containment(_guild, _actor):
        raise AssertionError("Unavailable/disabled AntiNuke cannot contain peers")

    async def incident(_guild, **kwargs):
        responses.append(str(kwargs["response_label"]))

    monkeypatch.setattr(guardian, "_contain_peer", forbidden_containment)
    monkeypatch.setattr(anti_nuke, "_post_incident", incident)

    async def disabled(_guild_id: int):
        return {"antinuke_enabled": False, "antinuke_mode": "contain"}

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", disabled)
    kwargs = dict(
        guild=guild, actor=_actor(501), action_label="Role deletion",
        target_label="staff", observed=[_actor(501), _actor(502)],
    )
    asyncio.run(guardian._post_panic_incident(**kwargs))

    async def unavailable(_guild_id: int):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", unavailable)
    asyncio.run(guardian._post_panic_incident(**kwargs))

    assert len(responses) == 2
    assert all("no peer containment attempted" in text for text in responses)
    _reset()


def test_panic_peer_containment_excludes_trusted_staff_and_bots(monkeypatch) -> None:
    _reset()
    guild = FakeGuild()
    contained: list[int] = []
    responses: list[str] = []

    async def get_settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_trusted_user_ids": [502],
            "antinuke_trusted_role_ids": [77],
        }

    async def contain(_guild, actor):
        contained.append(int(actor.id))
        return ["removed member from server"], []

    async def incident(_guild, **kwargs):
        responses.append(str(kwargs["response_label"]))

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", get_settings)
    monkeypatch.setattr(guardian, "_contain_peer", contain)
    monkeypatch.setattr(anti_nuke, "_post_incident", incident)

    def peer(user_id: int, *, roles=(), bot=False):
        return SimpleNamespace(
            id=user_id, roles=[SimpleNamespace(id=r) for r in roles], bot=bot
        )

    # A configured trusted user and trusted-role member are not collateral
    # damage. The operational bot is handled through bounded canonical
    # enforcement. An untrusted participant remains eligible for containment.
    observed = [
        peer(501), peer(502), peer(503, roles=(77,)), peer(504, bot=True),
        peer(505),
    ]
    asyncio.run(
        guardian._post_panic_incident(
            guild, actor=observed[0], action_label="Channel deletion",
            target_label="general", observed=observed,
        )
    )
    assert contained == [505]
    assert "Peer actors contained: 505" in responses[0]
    _reset()


def test_panic_keeps_trusted_actor_thresholds_and_explicit_strict_policy(monkeypatch) -> None:
    import time

    _reset()
    guild = FakeGuild()
    overrides: list[tuple[int, object]] = []

    async def settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            "antinuke_trusted_user_ids": [502],
            "antinuke_trusted_role_ids": [],
        }

    async def process(_guild, **kwargs):
        overrides.append((int(kwargs["entry"].user.id), kwargs["threshold_override"]))
        return True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", process)
    guardian._PANIC_UNTIL[guild.id] = time.monotonic() + 60

    def run(actor, override=None):
        asyncio.run(
            guardian._process(
                guild, SimpleNamespace(user=actor, target=None),
                actor, "role_delete",
                ("Role deleted", "antinuke_role_delete_threshold", "role_delete", override),
            )
        )

    run(SimpleNamespace(id=502, bot=False, roles=[]))
    run(SimpleNamespace(id=503, bot=False, roles=[]))
    run(SimpleNamespace(id=504, bot=True, roles=[]))
    run(SimpleNamespace(id=502, bot=False, roles=[]), override=1)

    assert overrides == [(502, None), (503, 1), (504, None), (502, 1)]
    _reset()
