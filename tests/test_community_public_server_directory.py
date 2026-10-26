"""Read-only public game server adapter contract tests."""

import asyncio
from unittest.mock import patch

import pytest

from stoney_verify import community_public_server_directory as directory
from stoney_verify.community_external_lfg import ExternalDiscoveryError


def _payload():
    return {"servers": [
        {"id": 42, "game": "minecraft", "name": "@everyone SMP",
         "online": True, "trust": "confirmed", "players": 5, "max_players": 20,
         "url": "https://evil.example/", "address": "private"},
        {"id": 43, "game": "minecraft", "name": "Untrusted",
         "online": True, "trust": "suspicious", "players": 100, "max_players": 100},
        {"id": 44, "game": "minecraft", "name": "Offline",
         "online": False, "trust": "confirmed", "players": 2, "max_players": 10},
    ]}


def test_only_confirmed_online_servers_and_safe_links():
    assert directory.normalize_servers(_payload()) == [{
        "name": "＠everyone SMP", "game": "minecraft", "players": 5,
        "capacity": 20, "available_slots": 15, "source": "battlemetrics.ru",
        "url": "https://battlemetrics.ru/game/minecraft/server/42",
        "kind": "public_game_server",
    }]


def test_invalid_envelope_rejected():
    for value in (None, [], {}, {"servers": "wrong"}):
        with pytest.raises(ExternalDiscoveryError, match="unfamiliar"):
            directory.normalize_servers(value)


def test_no_key_no_request():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ExternalDiscoveryError, match="not configured"):
            asyncio.run(directory.list_public_servers(game="minecraft"))


def test_bad_filters_fail_closed():
    with pytest.raises(ExternalDiscoveryError, match="supported game"):
        asyncio.run(directory.list_public_servers(game="../../etc"))
    with pytest.raises(ExternalDiscoveryError, match="supported game"):
        asyncio.run(directory.list_public_servers(game="minecraft", country="../../"))
