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
