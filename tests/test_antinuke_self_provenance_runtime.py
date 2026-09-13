from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from stoney_verify import anti_nuke_self_provenance_runtime as provenance


class FakeRoute:
    def __init__(
        self,
        method: str,
        path: str,
        *,
        url: str,
        guild_id: int | None = None,
        channel_id: int | None = None,
        webhook_id: int | None = None,
    ) -> None:
        self.method = method
        self.path = path
        self.url = url
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.webhook_id = webhook_id


class FakeHttp:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def request(self, route, *args, **kwargs):
        self.calls.append((route, args, dict(kwargs)))
        return {"ok": True}


class FakeBot:
    def __init__(self, bot_id: int = 999) -> None:
        self.http = FakeHttp()
        self.user = SimpleNamespace(id=bot_id)
        self.listeners: dict[str, list[object]] = {}

    def add_listener(self, listener, event_name: str) -> None:
        self.listeners.setdefault(event_name, []).append(listener)

    def remove_listener(self, listener, event_name: str) -> None:
        values = self.listeners.setdefault(event_name, [])
        try:
            values.remove(listener)
        except ValueError:
            pass


def _reset() -> None:
    provenance._PENDING.clear()  # noqa: SLF001
    provenance._OWNER_DM_AT.clear()  # noqa: SLF001


def _entry(*, reason: str, actor_id: int = 999, target_id: int | None = 123):
    target = SimpleNamespace(id=target_id) if target_id is not None else None
    return SimpleNamespace(
        reason=reason,
        user=SimpleNamespace(id=actor_id),
        target=target,
    )


def test_structural_http_request_gets_one_time_audit_provenance() -> None:
    _reset()
    bot = FakeBot()
    assert provenance._patch_http_request(bot) is True  # noqa: SLF001

    route = FakeRoute(
        "DELETE",
        "/channels/{channel_id}",
        url="https://discord.com/api/v10/channels/123",
        channel_id=123,
    )
    asyncio.run(bot.http.request(route, reason="Dank Shield legitimate cleanup"))

    assert len(bot.http.calls) == 1
    sent_reason = str(bot.http.calls[0][2]["reason"])
    token = provenance._marker_token(SimpleNamespace(reason=sent_reason))  # noqa: SLF001
    assert token
    assert token in provenance._PENDING  # noqa: SLF001

    guild = SimpleNamespace(id=55)
    entry = _entry(reason=sent_reason, target_id=123)
    valid, status = provenance._consume_provenance(  # noqa: SLF001
        guild, entry, "channel_delete"
    )
    assert valid is True
    assert status == "valid local provenance"

    replay_valid, replay_status = provenance._consume_provenance(  # noqa: SLF001
        guild, entry, "channel_delete"
    )
    assert replay_valid is False
    assert "replayed" in replay_status


def test_provenance_rejects_action_and_target_mismatch() -> None:
    _reset()
    now = time.monotonic()
    provenance._PENDING["actiontoken123"] = provenance._PendingMutation(  # noqa: SLF001
        issued_at=now,
        method="DELETE",
        path="/channels/{channel_id}",
        expectation=provenance._Expectation(  # noqa: SLF001
            actions=frozenset({"channel_delete", "thread_delete"}),
            target_id=123,
        ),
    )
    entry = _entry(reason="x [DS-PROV:actiontoken123]", target_id=123)
    valid, status = provenance._consume_provenance(  # noqa: SLF001
        SimpleNamespace(id=1), entry, "role_delete"
    )
    assert valid is False
    assert "action mismatch" in status
    assert "actiontoken123" not in provenance._PENDING  # noqa: SLF001

    provenance._PENDING["targettoken123"] = provenance._PendingMutation(  # noqa: SLF001
        issued_at=now,
        method="DELETE",
        path="/channels/{channel_id}",
        expectation=provenance._Expectation(  # noqa: SLF001
            actions=frozenset({"channel_delete"}),
            target_id=123,
        ),
    )
    wrong_target = _entry(reason="x [DS-PROV:targettoken123]", target_id=456)
    valid, status = provenance._consume_provenance(  # noqa: SLF001
        SimpleNamespace(id=1), wrong_target, "channel_delete"
    )
    assert valid is False
    assert "target mismatch" in status


def test_non_administrative_message_send_is_not_marked() -> None:
    _reset()
    bot = FakeBot()
    assert provenance._patch_http_request(bot) is True  # noqa: SLF001
    route = FakeRoute(
        "POST",
        "/channels/{channel_id}/messages",
        url="https://discord.com/api/v10/channels/123/messages",
        channel_id=123,
    )
    asyncio.run(bot.http.request(route))
    assert bot.http.calls[0][2].get("reason") is None
    assert provenance._PENDING == {}  # noqa: SLF001


def test_request_classifier_covers_high_impact_routes() -> None:
    role = provenance._request_expectation(  # noqa: SLF001
        FakeRoute(
            "DELETE",
            "/guilds/{guild_id}/roles/{role_id}",
            url="https://discord.com/api/v10/guilds/1/roles/22",
            guild_id=1,
        )
    )
    assert role is not None and role.actions == frozenset({"role_delete"})
    assert role.target_id == 22

    ban = provenance._request_expectation(  # noqa: SLF001
        FakeRoute(
            "PUT",
            "/guilds/{guild_id}/bans/{user_id}",
            url="https://discord.com/api/v10/guilds/1/bans/44",
            guild_id=1,
        )
    )
    assert ban is not None and ban.actions == frozenset({"ban"})
    assert ban.target_id == 44

    purge = provenance._request_expectation(  # noqa: SLF001
        FakeRoute(
            "POST",
            "/channels/{channel_id}/messages/bulk-delete",
            url="https://discord.com/api/v10/channels/55/messages/bulk-delete",
            channel_id=55,
        )
    )
    assert purge is not None and purge.actions == frozenset({"message_bulk_delete"})
    assert purge.target_id == 55


def test_valid_self_audit_entry_is_trusted_once_without_incident() -> None:
    _reset()
    bot = FakeBot()
    provenance._PENDING["validtoken1234"] = provenance._PendingMutation(  # noqa: SLF001
        issued_at=time.monotonic(),
        method="DELETE",
        path="/channels/{channel_id}",
        expectation=provenance._Expectation(  # noqa: SLF001
            actions=frozenset({"channel_delete"}), target_id=123
        ),
    )
    consumed: list[object] = []
    incidents: list[dict[str, object]] = []

    class AntiNuke:
        async def get_antinuke_settings(self, _guild_id):
            return {"antinuke_enabled": True, "antinuke_mode": "contain"}

        def _consume_audit_entry(self, entry):
            consumed.append(entry)
            return False

        async def _post_incident(self, _guild, **kwargs):
            incidents.append(kwargs)

    entry = _entry(reason="legit [DS-PROV:validtoken1234]", target_id=123)
    handled = asyncio.run(
        provenance._evaluate_self_entry(  # noqa: SLF001
            bot,
            SimpleNamespace(id=1),
            entry,
            "channel_delete",
            anti_nuke=AntiNuke(),
            guardian=SimpleNamespace(),
            gateway=SimpleNamespace(),
        )
    )
    assert handled is True
    assert len(consumed) == 1
    assert incidents == []


def test_unprovenanced_self_action_becomes_critical_incident(monkeypatch) -> None:
    _reset()
    bot = FakeBot()
    incidents: list[dict[str, object]] = []
    consumed: list[object] = []

    async def fake_rollback(*_args, **_kwargs):
        return "reverted safe state"

    async def fake_owner_warning(*_args, **_kwargs):
        return "guild owner warned by DM"

    monkeypatch.setattr(provenance, "_rollback_compromise_action", fake_rollback)
    monkeypatch.setattr(provenance, "_warn_owner", fake_owner_warning)

    class AntiNuke:
        async def get_antinuke_settings(self, _guild_id):
            return {"antinuke_enabled": True, "antinuke_mode": "contain"}

        def _consume_audit_entry(self, entry):
            consumed.append(entry)
            return False

        async def _post_incident(self, _guild, **kwargs):
            incidents.append(kwargs)

    entry = _entry(reason="external request", target_id=123)
    handled = asyncio.run(
        provenance._evaluate_self_entry(  # noqa: SLF001
            bot,
            SimpleNamespace(id=1),
            entry,
            "channel_delete",
            anti_nuke=AntiNuke(),
            guardian=SimpleNamespace(),
            gateway=SimpleNamespace(),
        )
    )
    assert handled is True
    assert len(consumed) == 1
    assert len(incidents) == 1
    assert incidents[0]["title"] == "🚨 AntiNuke Bot-Token Compromise Suspected"
    assert "missing provenance" in str(incidents[0]["details"])


def test_disabled_antinuke_does_not_reclassify_self_action() -> None:
    _reset()
    bot = FakeBot()

    class AntiNuke:
        async def get_antinuke_settings(self, _guild_id):
            return {"antinuke_enabled": False, "antinuke_mode": "contain"}

    handled = asyncio.run(
        provenance._evaluate_self_entry(  # noqa: SLF001
            bot,
            SimpleNamespace(id=1),
            _entry(reason="none"),
            "channel_delete",
            anti_nuke=AntiNuke(),
            guardian=SimpleNamespace(),
            gateway=SimpleNamespace(),
        )
    )
    assert handled is False


def test_sparse_recovered_self_actor_uses_same_provenance_decision(monkeypatch) -> None:
    _reset()
    bot = FakeBot()
    calls: list[str] = []

    class Incident:
        pass

    incident = Incident()

    async def original(_guild, _entry, _actor, _action_name):
        calls.append("original")

    incident._dispatch_recovered = original

    async def fake_evaluate(*_args, **_kwargs):
        calls.append("provenance")
        return True

    monkeypatch.setattr(provenance, "_evaluate_self_entry", fake_evaluate)
    guardian = SimpleNamespace(
        _EntryProxy=lambda entry, actor: SimpleNamespace(
            reason=getattr(entry, "reason", ""),
            user=actor,
            target=getattr(entry, "target", None),
        )
    )
    assert provenance._patch_sparse_dispatch(  # noqa: SLF001
        bot,
        SimpleNamespace(),
        guardian,
        SimpleNamespace(),
        incident,
    ) is True

    asyncio.run(
        incident._dispatch_recovered(
            SimpleNamespace(id=1),
            _entry(reason="none"),
            SimpleNamespace(id=bot.user.id),
            "role_update",
        )
    )
    assert calls == ["provenance"]
