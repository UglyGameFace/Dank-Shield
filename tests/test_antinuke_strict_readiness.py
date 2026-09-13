from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_readiness_gate_runtime as gate
from stoney_verify import anti_nuke_readiness_strict as strict


class FakeBot:
    def __init__(self, guild=None) -> None:
        self.guild = guild

    def get_guild(self, _guild_id: int):
        return self.guild


class FakeRole:
    def __init__(self, role_id: int, name: str, members=None) -> None:
        self.id = role_id
        self.name = name
        self.members = list(members or [])
        self.permissions = SimpleNamespace()

    def is_default(self) -> bool:
        return False


class FakeOverwrite:
    pass


def _guild(*, roles=None, channels=None):
    bot_role = FakeRole(500, "Dank Shield")
    me = SimpleNamespace(id=55, roles=[bot_role])
    return SimpleNamespace(
        id=7,
        owner_id=999,
        me=me,
        roles=[bot_role, *(roles or [])],
        channels=list(channels or []),
    )


def test_delegated_risky_role_blocks_strict_contain_readiness(monkeypatch) -> None:
    member = SimpleNamespace(id=77)
    risky = FakeRole(10, "Moderator", [member])
    guild = _guild(roles=[risky])

    monkeypatch.setattr(
        anti_nuke,
        "role_has_dangerous_permissions",
        lambda role: role is risky,
    )
    monkeypatch.setattr(anti_nuke, "_role_is_default", lambda _role: False)

    blockers = strict.delegated_authority_blockers(guild)
    assert len(blockers) == 1
    assert "@Moderator" in blockers[0]


def test_owner_only_risky_role_is_not_delegated_blocker(monkeypatch) -> None:
    owner = SimpleNamespace(id=999)
    risky = FakeRole(10, "Owner Root", [owner])
    guild = _guild(roles=[risky])

    monkeypatch.setattr(
        anti_nuke,
        "role_has_dangerous_permissions",
        lambda role: role is risky,
    )
    monkeypatch.setattr(anti_nuke, "_role_is_default", lambda _role: False)

    assert strict.delegated_authority_blockers(guild) == []


def test_delegated_risky_channel_overwrite_blocks_readiness(monkeypatch) -> None:
    role = FakeRole(12, "Helper")
    overwrite = FakeOverwrite()
    channel = SimpleNamespace(name="general", overwrites={role: overwrite})
    guild = _guild(roles=[role], channels=[channel])

    monkeypatch.setattr(anti_nuke, "role_has_dangerous_permissions", lambda _role: False)
    monkeypatch.setattr(
        anti_nuke,
        "_overwrite_grants_dangerous_permissions",
        lambda item: item is overwrite,
    )

    blockers = strict.delegated_authority_blockers(guild)
    assert len(blockers) == 1
    assert "#general" in blockers[0]


def test_health_gate_surfaces_delegated_authority(monkeypatch) -> None:
    guild = _guild()
    original_health = anti_nuke.antinuke_permission_health
    had_flag = hasattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, gate._HEALTH_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001

    monkeypatch.setattr(anti_nuke, "antinuke_permission_health", lambda _guild, settings=None: [])
    monkeypatch.setattr(gate, "delegated_authority_blockers", lambda _guild: ["strict blocker"])

    try:
        assert gate._patch_health() is True  # noqa: SLF001
        missing = anti_nuke.antinuke_permission_health(
            guild,
            {"antinuke_enabled": True, "antinuke_mode": "contain"},
        )
        assert missing == ["strict blocker"]
    finally:
        anti_nuke.antinuke_permission_health = original_health
        if had_flag:
            setattr(anti_nuke, gate._HEALTH_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, gate._HEALTH_FLAG):  # noqa: SLF001
            delattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001


def test_save_gate_refuses_to_arm_when_delegated_authority_remains(monkeypatch) -> None:
    guild = _guild()
    bot = FakeBot(guild)
    original_save = anti_nuke.save_antinuke_settings
    had_flag = hasattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, gate._SAVE_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001

    async def current(_guild_id: int):
        return anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": False, "antinuke_mode": "contain"}
        )

    saves: list[dict] = []

    async def saved(_guild_id: int, patch):
        saves.append(dict(patch))
        return anti_nuke.normalize_antinuke_settings(patch)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", current)
    monkeypatch.setattr(anti_nuke, "save_antinuke_settings", saved)
    monkeypatch.setattr(gate, "delegated_authority_blockers", lambda _guild: ["strict blocker"])

    try:
        assert gate._patch_save(bot) is True  # noqa: SLF001
        try:
            asyncio.run(
                anti_nuke.save_antinuke_settings(  # type: ignore[misc]
                    7,
                    {"antinuke_enabled": True, "antinuke_mode": "contain"},
                )
            )
        except RuntimeError as exc:
            assert "cannot be armed" in str(exc)
        else:
            raise AssertionError("strict readiness gate accepted delegated authority")
        assert saves == []
    finally:
        anti_nuke.save_antinuke_settings = original_save
        if had_flag:
            setattr(anti_nuke, gate._SAVE_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, gate._SAVE_FLAG):  # noqa: SLF001
            delattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001
