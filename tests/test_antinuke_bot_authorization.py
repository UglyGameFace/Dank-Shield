from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_guardian_runtime as guardian
from stoney_verify import anti_nuke_hostile_actor_runtime as hostile


def _settings(**updates):
    settings = dict(anti_nuke.ANTINUKE_DEFAULTS)
    settings.update(
        {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
        }
    )
    settings.update(updates)
    return anti_nuke.normalize_antinuke_settings(settings)


def test_owner_must_preapprove_bot_target_but_delegated_trust_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_reputation(_guild_id: int, _user_id: int, *, refresh: bool = False):
        _ = refresh
        return None

    monkeypatch.setattr(hostile, "get_actor_reputation", no_reputation)

    guild = SimpleNamespace(id=10, owner_id=99)
    target = SimpleNamespace(id=55)
    owner = SimpleNamespace(id=99, roles=[])
    delegated = SimpleNamespace(id=77, roles=[])

    allowed, reason = asyncio.run(
        anti_nuke.bot_add_authorization(guild, target, owner, _settings())
    )
    assert allowed is False
    assert "pre-approve" in reason

    allowed, reason = asyncio.run(
        anti_nuke.bot_add_authorization(
            guild,
            target,
            owner,
            _settings(antinuke_trusted_bot_ids=[55]),
        )
    )
    assert allowed is True
    assert reason == "bot ID is explicitly trusted"

    allowed, reason = asyncio.run(
        anti_nuke.bot_add_authorization(
            guild,
            target,
            delegated,
            _settings(antinuke_trusted_user_ids=[77]),
        )
    )
    assert allowed is True
    assert reason == "inviter is explicitly trusted"


def test_hostile_bot_reputation_outranks_preapproved_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hostile_reputation(_guild_id: int, user_id: int, *, refresh: bool = False):
        _ = refresh
        assert user_id == 55
        return {"active": True}

    monkeypatch.setattr(hostile, "get_actor_reputation", hostile_reputation)

    allowed, reason = asyncio.run(
        anti_nuke.bot_add_authorization(
            SimpleNamespace(id=10, owner_id=99),
            SimpleNamespace(id=55),
            SimpleNamespace(id=99, roles=[]),
            _settings(antinuke_trusted_bot_ids=[55]),
        )
    )

    assert allowed is False
    assert reason == "bot has active hostile reputation"


def test_guardian_uses_canonical_authorization_and_does_not_contain_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization_calls: list[tuple[int, int, int]] = []
    incidents: list[dict[str, object]] = []

    async def get_settings(_guild_id: int):
        return _settings()

    async def authorize(guild, target, actor, settings):
        assert settings["antinuke_enabled"] is True
        authorization_calls.append((int(guild.id), int(target.id), int(actor.id)))
        return False, "guild owner must pre-approve this bot ID"

    async def post_incident(_guild, **kwargs):
        incidents.append(dict(kwargs))

    async def should_not_contain(_guild, _actor):
        raise AssertionError("the physical guild owner must not be contained")

    monkeypatch.setattr(guardian.anti_nuke, "get_antinuke_settings", get_settings)
    monkeypatch.setattr(guardian.anti_nuke, "bot_add_authorization", authorize)
    monkeypatch.setattr(guardian.anti_nuke, "_post_incident", post_incident)
    monkeypatch.setattr(guardian, "_contain_peer", should_not_contain)
    monkeypatch.setattr(
        guardian,
        "_panic_state",
        lambda *_args, **_kwargs: (False, False, []),
    )

    class Guild:
        id = 10
        owner_id = 99

        def __init__(self) -> None:
            self.kicked: list[int] = []

        async def kick(self, target, *, reason: str):
            assert "unauthorized bot addition" in reason
            self.kicked.append(int(target.id))

    guild = Guild()
    target = SimpleNamespace(id=55)
    actor = SimpleNamespace(id=99, roles=[])
    entry = SimpleNamespace(target=target)

    asyncio.run(guardian._handle_bot_add(guild, entry, actor))  # noqa: SLF001

    assert authorization_calls == [(10, 55, 99)]
    assert guild.kicked == [55]
    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Unauthorized Bot Added"
    assert "pre-approve" in str(incidents[0]["details"])

    async def authorize_target(_guild, _target, _actor, _settings):
        return True, "bot ID is explicitly trusted"

    monkeypatch.setattr(guardian.anti_nuke, "bot_add_authorization", authorize_target)
    asyncio.run(guardian._handle_bot_add(guild, entry, actor))  # noqa: SLF001

    assert guild.kicked == [55]
    assert len(incidents) == 1
