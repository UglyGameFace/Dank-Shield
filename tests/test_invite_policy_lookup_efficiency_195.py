from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import invite_policy_engine as policy
from stoney_verify import spam_guard
from stoney_verify.startup_guards import invite_shield_sanitize_shared as shared


class FakeHttp:
    def __init__(self, payload=None) -> None:
        self.payload = payload
        self.calls = 0

    async def get_invite(self, code, *, with_counts=False, with_expiration=False):
        _ = (code, with_counts, with_expiration)
        self.calls += 1
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class FakeClient:
    def __init__(self, guilds=None) -> None:
        self.guilds = dict(guilds or {})
        self.fetch_calls = 0

    def get_guild(self, guild_id: int):
        return self.guilds.get(int(guild_id))

    async def fetch_invite(self, *args, **kwargs):
        _ = (args, kwargs)
        self.fetch_calls += 1
        raise AssertionError("central policy must not issue a second fetch_invite call")


class FakeState:
    def __init__(self, *, client=None, http=None) -> None:
        self.client = client or FakeClient()
        self.http = http or FakeHttp(RuntimeError("unexpected low-level fallback"))

    def _get_client(self):
        return self.client


class FakeGuild:
    def __init__(self, guild_id: int = 123, *, invite_codes=None, state=None) -> None:
        self.id = int(guild_id)
        self.vanity_url_code = None
        self._invite_codes = list(invite_codes or [])
        self.invites_calls = 0
        self.vanity_calls = 0
        self._state = state or FakeState()

    async def invites(self):
        self.invites_calls += 1
        return [SimpleNamespace(code=code) for code in self._invite_codes]

    async def vanity_invite(self):
        self.vanity_calls += 1
        return SimpleNamespace(code=None)


def test_guild_invite_codes_reuses_spam_cache_without_duplicate_list_fetch(monkeypatch) -> None:
    guild = FakeGuild(invite_codes=["should-not-be-read"])

    async def cached(_guild):
        return {"OwnCode"}

    monkeypatch.setattr(spam_guard, "_fetch_guild_invite_codes", cached)

    result = asyncio.run(policy._guild_invite_codes(guild))

    assert result == {"owncode"}
    assert guild.invites_calls == 0


def test_guild_invite_codes_keeps_direct_fallback_when_cache_is_empty(monkeypatch) -> None:
    guild = FakeGuild(invite_codes=["FallbackCode"])

    async def empty_cache(_guild):
        return set()

    monkeypatch.setattr(spam_guard, "_fetch_guild_invite_codes", empty_cache)

    result = asyncio.run(policy._guild_invite_codes(guild))

    assert result == {"fallbackcode"}
    assert guild.invites_calls == 1


def test_target_resolver_classifies_external_without_second_client_fetch(monkeypatch) -> None:
    external = SimpleNamespace(id=999, name="External Server")
    client = FakeClient({999: external})
    http = FakeHttp(RuntimeError("low-level fallback should not run"))
    guild = FakeGuild(123, state=FakeState(client=client, http=http))

    async def no_own_codes(_guild):
        return set()

    calls: list[str] = []

    async def resolve(code):
        calls.append(code)
        return 999

    monkeypatch.setattr(policy, "_guild_invite_codes", no_own_codes)
    monkeypatch.setattr(shared, "fetch_invite_guild_id", resolve)

    classification, target = asyncio.run(policy._invite_code_belongs_to_guild(guild, "OutsideCode"))

    assert classification == "external"
    assert target == "External Server"
    assert calls == ["outsidecode"]
    assert client.fetch_calls == 0
    assert http.calls == 0


def test_target_resolver_still_protects_same_server_invite(monkeypatch) -> None:
    current = SimpleNamespace(id=123, name="Current Server")
    client = FakeClient({123: current})
    http = FakeHttp(RuntimeError("low-level fallback should not run"))
    guild = FakeGuild(123, state=FakeState(client=client, http=http))

    async def no_own_codes(_guild):
        return set()

    async def resolve(_code):
        return 123

    monkeypatch.setattr(policy, "_guild_invite_codes", no_own_codes)
    monkeypatch.setattr(shared, "fetch_invite_guild_id", resolve)

    classification, target = asyncio.run(policy._invite_code_belongs_to_guild(guild, "LocalCode"))

    assert classification == "internal"
    assert target == "Current Server"
    assert client.fetch_calls == 0
    assert http.calls == 0


def test_unresolved_shared_lookup_retains_low_level_same_server_fallback(monkeypatch) -> None:
    client = FakeClient()
    http = FakeHttp({"guild": {"id": "123", "name": "Current Server"}})
    guild = FakeGuild(123, state=FakeState(client=client, http=http))

    async def no_own_codes(_guild):
        return set()

    async def unresolved(_code):
        return 0

    monkeypatch.setattr(policy, "_guild_invite_codes", no_own_codes)
    monkeypatch.setattr(shared, "fetch_invite_guild_id", unresolved)

    classification, target = asyncio.run(policy._invite_code_belongs_to_guild(guild, "LocalFallback"))

    assert classification == "internal"
    assert target == "Current Server"
    assert client.fetch_calls == 0
    assert http.calls == 1
