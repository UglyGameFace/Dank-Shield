from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify.members_new import join_context_service as join_context


class FakeGuild:
    def __init__(
        self,
        *,
        manage_guild: bool,
        administrator: bool = False,
        features: list[str] | None = None,
    ) -> None:
        self.id = 123
        self.features = list(features or [])
        self.me = SimpleNamespace(
            guild_permissions=SimpleNamespace(
                manage_guild=manage_guild,
                administrator=administrator,
            )
        )
        self.invites_calls = 0
        self.vanity_calls = 0

    async def invites(self):
        self.invites_calls += 1
        return []

    async def vanity_invite(self):
        self.vanity_calls += 1
        return SimpleNamespace(code="example", uses=3)


def test_invite_cache_warm_skips_guaranteed_forbidden_request() -> None:
    guild = FakeGuild(manage_guild=False)

    result = asyncio.run(
        join_context._warm_invite_cache_for_guild_unlocked(guild)  # noqa: SLF001
    )

    assert result is False
    assert guild.invites_calls == 0
    assert guild.vanity_calls == 0


def test_invite_cache_warm_does_not_call_vanity_endpoint_without_feature() -> None:
    guild = FakeGuild(manage_guild=True, features=[])

    result = asyncio.run(
        join_context._warm_invite_cache_for_guild_unlocked(guild)  # noqa: SLF001
    )

    assert result is True
    assert guild.invites_calls == 1
    assert guild.vanity_calls == 0


def test_invite_cache_warm_calls_vanity_only_when_supported() -> None:
    guild = FakeGuild(manage_guild=True, features=["VANITY_URL"])

    result = asyncio.run(
        join_context._warm_invite_cache_for_guild_unlocked(guild)  # noqa: SLF001
    )

    assert result is True
    assert guild.invites_calls == 1
    assert guild.vanity_calls == 1
