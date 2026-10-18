from __future__ import annotations

import asyncio

import pytest

from stoney_verify import community_external_lfg as ext


def test_the100_normalizes_deduplicates_and_never_trusts_external_urls() -> None:
    rows = [
        {"id": 42, "game": {"name": "Destiny 2"}, "title": "@everyone raid", "url": "https://evil.test/"},
        {"id": "42", "game_name": "duplicate"},
        {"id": "../bad", "name": "Bad"},
        {"id": 43, "game_name": "Warframe", "game_activity": {"name": "Survival"}},
    ]
    result = ext.normalize_the100_sessions({"gaming_sessions": rows})
    assert len(result) == 2
    assert result[0]["url"] == "https://www.the100.io/gaming_sessions/42"
    assert "@everyone" not in result[0]["title"]
    assert result[1]["game"] == "Warframe"
    assert result[1]["title"] == "Survival"


def test_the100_rejects_unfamiliar_response_shapes() -> None:
    for payload in ({}, {"gaming_sessions": {}}, None, "bad"):
        with pytest.raises(ext.ExternalDiscoveryError):
            ext.normalize_the100_sessions(payload)


def test_the100_bounds_rows_and_retains_provider_source() -> None:
    rows = [{"id": number, "game_name": "Some Game"} for number in range(1, 70)]
    result = ext.normalize_the100_sessions(rows, limit=5)
    assert len(result) == 5
    assert all(row["source"] == "The100.io" for row in result)


def test_disabled_host_never_opens_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ext.THE100_TOKEN_ENV, raising=False)
    assert not ext.the100_configured()
    with pytest.raises(ext.ExternalDiscoveryError, match="not configured"):
        asyncio.run(ext.list_the100_sessions())


def test_provider_request_is_bounded_and_read_only() -> None:
    import inspect
    code = inspect.getsource(ext.list_the100_sessions)
    assert "session.get(" in code
    assert "session.post(" not in code
    assert "ClientTimeout(total=7, connect=3)" in code
    assert "_MAX_BYTES + 1" in code
    assert "Token token={token}" in code


class _FakeContent:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def read(self, limit: int) -> bytes:
        return self.data[:limit]


class _FakeResponse:
    def __init__(self, status: int, data: bytes) -> None:
        self.status = status
        self.content = _FakeContent(data)

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class _FakeSession:
    calls: list[tuple[str, dict]] = []
    status = 200
    data = b'[{"id": 11, "game_name": "Destiny 2"}]'

    def __init__(self, **kwargs: object) -> None:
        assert kwargs["timeout"].total == 7

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def get(self, url: str, *, headers: dict) -> _FakeResponse:
        self.calls.append((url, headers))
        return _FakeResponse(self.status, self.data)


def test_provider_uses_only_official_https_and_private_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ext.THE100_TOKEN_ENV, "secret-token")
    _FakeSession.calls = []
    _FakeSession.status = 200
    _FakeSession.data = b'[{"id": 11, "game_name": "Destiny 2"}]'
    monkeypatch.setattr(ext.aiohttp, "ClientSession", _FakeSession)
    sessions = asyncio.run(ext.list_the100_sessions())
    assert len(sessions) == 1
    assert _FakeSession.calls == [(
        "https://www.the100.io/api/v2/gaming_sessions",
        {"Authorization": "Token token=secret-token", "Accept": "application/json"},
    )]
    assert "secret-token" not in repr(sessions)


def test_provider_denial_and_bad_payload_do_not_fall_back_to_fake_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ext.THE100_TOKEN_ENV, "hidden")
    monkeypatch.setattr(ext.aiohttp, "ClientSession", _FakeSession)
    _FakeSession.status = 401
    with pytest.raises(ext.ExternalDiscoveryError, match="access was denied"):
        asyncio.run(ext.list_the100_sessions())
    _FakeSession.status = 200
    _FakeSession.data = b"this is not JSON"
    with pytest.raises(ext.ExternalDiscoveryError, match="Could not read"):
        asyncio.run(ext.list_the100_sessions())
    _FakeSession.data = b"x" * (ext._MAX_BYTES + 1)
    with pytest.raises(ext.ExternalDiscoveryError, match="too much data"):
        asyncio.run(ext.list_the100_sessions())


def test_hub_ui_contract_and_schema_opt_in_are_scoped() -> None:
    from pathlib import Path
    import ast
    import re

    root = Path(__file__).resolve().parents[1]
    ui = (root / "stoney_verify/commands_ext/public_community_hub.py").read_text(encoding="utf-8")
    service = (root / "stoney_verify/community_hub_service.py").read_text(encoding="utf-8")
    migration = (root / "supabase/migrations/20261018010000_community_hub_external_the100.sql").read_text(encoding="utf-8")
    ast.parse(ui)
    assert '"external_the100_enabled"' in service
    assert "external_the100_enabled boolean not null default false" in migration
    assert "await hub.get_settings(int(interaction.guild_id or 0))" in ui
    assert "external.the100_configured()" in ui
    assert 'custom_id="dank:hub:find:external:v1"' in ui
    assert 'custom_id="dank:hub:settings:the100:v1"' in ui
    assert "ExternalGroupsView" in ui
    assert "read-only, no external join actions" in ui
    assert "session_id=" not in ui[ui.index("class ExternalGroupsView"):ui.index("def _my_groups_embed")]
    ids = re.findall(r'custom_id\s*=\s*["\']([^"\']+)["\']', ui)
    assert ids and len(ids) == len(set(ids))
