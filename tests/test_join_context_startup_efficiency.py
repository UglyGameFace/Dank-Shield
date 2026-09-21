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
        self.invite_uses = 0

    async def invites(self):
        self.invites_calls += 1
        if self.invite_uses <= 0:
            return []
        return [
            SimpleNamespace(
                code="existing",
                uses=self.invite_uses,
                max_uses=0,
                temporary=False,
                inviter=None,
                channel=None,
            )
        ]

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


def test_cold_join_establishes_baseline_without_false_invite_credit() -> None:
    guild = FakeGuild(manage_guild=True)
    guild.invite_uses = 37
    member = SimpleNamespace(guild=guild)

    context = asyncio.run(
        join_context._detect_join_entry_context_unlocked(  # noqa: SLF001
            member,
            baseline_ready=False,
        )
    )

    assert context["entry_method"] == "invite_cache_warming"
    assert context["invite_code"] is None
    assert guild.invites_calls == 1
    assert join_context._INVITE_USES_CACHE[guild.id]["existing"] == 37  # noqa: SLF001


def test_warm_join_uses_real_increment_not_lifetime_invite_count() -> None:
    guild = FakeGuild(manage_guild=True)
    guild.invite_uses = 37
    join_context._INVITE_USES_CACHE[guild.id] = {"existing": 37}  # noqa: SLF001
    join_context._INVITE_META_CACHE[guild.id] = {}  # noqa: SLF001
    join_context._VANITY_USES_CACHE[guild.id] = None  # noqa: SLF001
    guild.invite_uses = 38
    member = SimpleNamespace(guild=guild)

    context = asyncio.run(
        join_context._detect_join_entry_context_unlocked(  # noqa: SLF001
            member,
            baseline_ready=True,
        )
    )

    assert context["entry_method"] == "invite"
    assert context["invite_code"] == "existing"
