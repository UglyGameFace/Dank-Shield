from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke


class FakePermissions:
    def __init__(self, **values):
        for name in anti_nuke.DANGEROUS_PERMISSION_NAMES:
            setattr(self, name, bool(values.get(name, False)))
        self.view_audit_log = bool(values.get("view_audit_log", False))
        self.manage_roles = bool(values.get("manage_roles", False))
        self.kick_members = bool(values.get("kick_members", False))


class FakeRole:
    def __init__(
        self,
        role_id: int,
        name: str,
        *,
        position: int,
        managed: bool = False,
        default: bool = False,
        permissions=None,
        members=None,
    ) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.managed = managed
        self._default = default
        self.permissions = permissions or FakePermissions()
        self.members = list(members or [])

    def is_default(self) -> bool:
        return self._default

    def __lt__(self, other) -> bool:
        return self.position < getattr(other, "position", 0)


class FakeMember:
    def __init__(self, user_id: int, roles) -> None:
        self.id = user_id
        self.roles = list(roles)
        self.top_role = max(self.roles, key=lambda role: role.position)
        self.removed_role_ids: list[int] = []

    async def remove_roles(self, *roles, reason=None) -> None:
        _ = reason
        self.removed_role_ids.extend(int(role.id) for role in roles)


class FakeGuild:
    def __init__(self, actor: FakeMember, bot_member: FakeMember, *, kick_fails: bool) -> None:
        self.id = 123
        self.owner_id = 999
        self.me = bot_member
        self._actor = actor
        self.kick_fails = kick_fails
        self.kicked_ids: list[int] = []

    def get_member(self, user_id: int):
        return self._actor if int(user_id) == int(self._actor.id) else None

    async def fetch_member(self, user_id: int):
        return self.get_member(user_id)

    async def kick(self, member, reason=None) -> None:
        _ = reason
        if self.kick_fails:
            raise RuntimeError("hierarchy blocks kick")
        self.kicked_ids.append(int(member.id))


def test_confirmed_actor_is_removed_even_when_base_roles_look_harmless(monkeypatch) -> None:
    everyone = FakeRole(1, "@everyone", position=0, default=True)
    harmless = FakeRole(2, "Harmless Name", position=10)
    bot_role = FakeRole(3, "Dank Shield", position=50)
    actor = FakeMember(55, [everyone, harmless])
    bot_member = FakeMember(77, [everyone, bot_role])
    guild = FakeGuild(actor, bot_member, kick_fails=False)

    monkeypatch.setattr(anti_nuke.discord, "Member", FakeMember)

    removed, blocked = asyncio.run(
        anti_nuke._contain_actor(
            guild,
            actor,
            reason="confirmed destructive action",
        )
    )

    assert guild.kicked_ids == [55]
    assert removed == ["removed member from server"]
    assert blocked == []
    assert actor.removed_role_ids == []


def test_failed_member_removal_strips_every_manageable_role_and_stays_retryable(monkeypatch) -> None:
    everyone = FakeRole(1, "@everyone", position=0, default=True)
    harmless_overwrite_role = FakeRole(2, "Channel Helper", position=10)
    another_role = FakeRole(4, "Member", position=20)
    bot_role = FakeRole(3, "Dank Shield", position=50)
    actor = FakeMember(55, [everyone, harmless_overwrite_role, another_role])
    bot_member = FakeMember(77, [everyone, bot_role])
    guild = FakeGuild(actor, bot_member, kick_fails=True)

    monkeypatch.setattr(anti_nuke.discord, "Member", FakeMember)

    removed, blocked = asyncio.run(
        anti_nuke._contain_actor(
            guild,
            actor,
            reason="confirmed destructive action",
        )
    )

    assert set(actor.removed_role_ids) == {2, 4}
    assert set(removed) == {"Channel Helper", "Member"}
    assert "member removal failed" in blocked


class FakeOverwrite:
    def __init__(self, **values) -> None:
        for name in anti_nuke.DANGEROUS_PERMISSION_NAMES:
            setattr(self, name, values.get(name))


def test_unmanageable_member_channel_overwrite_blocks_contain_readiness() -> None:
    everyone = FakeRole(1, "@everyone", position=0, default=True)
    bot_role = FakeRole(2, "Dank Shield", position=50)
    attacker_high = FakeRole(3, "High Harmless Role", position=60)
    attacker = FakeMember(444, [everyone, attacker_high])
    bot_member = FakeMember(777, [everyone, bot_role])
    bot_member.guild_permissions = FakePermissions(
        view_audit_log=True,
        manage_roles=True,
        kick_members=True,
    )
    channel = SimpleNamespace(
        id=888,
        name="staff-room",
        overwrites={attacker: FakeOverwrite(manage_channels=True)},
    )
    guild = SimpleNamespace(
        id=123,
        owner_id=999,
        me=bot_member,
        roles=[everyone, bot_role, attacker_high],
        channels=[channel],
    )

    settings = anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})
    blockers = anti_nuke.antinuke_permission_health(guild, settings)

    assert any(
        "Channel overwrite containment" in item
        and "staff-room" in item
        and "member 444" in item
        for item in blockers
    )
