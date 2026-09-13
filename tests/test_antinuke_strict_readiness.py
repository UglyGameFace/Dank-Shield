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
    def __init__(self, role_id: int, name: str, members=None, **permissions: bool) -> None:
        self.id = role_id
        self.name = name
        self.members = list(members or [])
        self.permissions = SimpleNamespace(**permissions)

    def is_default(self) -> bool:
        return False


class FakeOverwrite:
    def __init__(self, **permissions: bool) -> None:
        for key, value in permissions.items():
            setattr(self, key, value)


def _guild(*, roles=None, channels=None):
    bot_role = FakeRole(500, "Dank Shield")
    me = SimpleNamespace(id=55, roles=[bot_role])
    return SimpleNamespace(
        id=7,
        owner_id=999,
        owner=SimpleNamespace(id=999),
        me=me,
        roles=[bot_role, *(roles or [])],
        channels=list(channels or []),
    )


def test_delegated_role_reports_exact_strict_permissions(monkeypatch) -> None:
    member = SimpleNamespace(id=77)
    risky = FakeRole(
        10,
        "Moderator",
        [member],
        administrator=True,
        manage_channels=True,
    )
    guild = _guild(roles=[risky])
    monkeypatch.setattr(
        anti_nuke,
        "DANGEROUS_PERMISSION_NAMES",
        ("administrator", "manage_channels"),
    )

    blockers = strict.delegated_authority_blockers(guild)

    assert blockers == [
        "Strict Lockdown: @Moderator grants Administrator, Manage Channels"
    ]


def test_owner_only_risky_role_is_not_strict_blocker(monkeypatch) -> None:
    owner = SimpleNamespace(id=999)
    risky = FakeRole(10, "Owner Root", [owner], administrator=True)
    guild = _guild(roles=[risky])
    monkeypatch.setattr(
        anti_nuke,
        "DANGEROUS_PERMISSION_NAMES",
        ("administrator",),
    )

    assert strict.delegated_authority_blockers(guild) == []


def test_channel_overwrite_reports_exact_strict_permission(monkeypatch) -> None:
    role = FakeRole(12, "Helper")
    overwrite = FakeOverwrite(manage_channels=True)
    channel = SimpleNamespace(name="general", overwrites={role: overwrite})
    guild = _guild(roles=[role], channels=[channel])
    monkeypatch.setattr(
        anti_nuke,
        "DANGEROUS_PERMISSION_NAMES",
        ("manage_channels",),
    )

    blockers = strict.delegated_authority_blockers(guild)

    assert blockers == [
        "Strict Lockdown: #general grants Manage Channels to Helper"
    ]


def test_health_gate_does_not_block_normal_contain(monkeypatch) -> None:
    guild = _guild()
    original_health = anti_nuke.antinuke_permission_health
    original_normalize = anti_nuke.normalize_antinuke_settings
    had_flag = hasattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, gate._HEALTH_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001

    def normalize(settings):
        return dict(settings or {})

    monkeypatch.setattr(
        anti_nuke,
        "antinuke_permission_health",
        lambda _guild, settings=None: [],
    )
    monkeypatch.setattr(anti_nuke, "normalize_antinuke_settings", normalize)
    monkeypatch.setattr(
        gate,
        "delegated_authority_blockers",
        lambda _guild: ["Strict Lockdown: @Moderator grants Manage Server"],
    )

    try:
        assert gate._patch_health() is True  # noqa: SLF001
        missing = anti_nuke.antinuke_permission_health(
            guild,
            {
                "antinuke_enabled": True,
                "antinuke_mode": "contain",
                gate.STRICT_LOCKDOWN_KEY: False,
            },
        )
        assert missing == []
    finally:
        anti_nuke.antinuke_permission_health = original_health
        anti_nuke.normalize_antinuke_settings = original_normalize
        if had_flag:
            setattr(anti_nuke, gate._HEALTH_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, gate._HEALTH_FLAG):  # noqa: SLF001
            delattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001


def test_health_gate_blocks_only_strict_lockdown(monkeypatch) -> None:
    guild = _guild()
    original_health = anti_nuke.antinuke_permission_health
    original_normalize = anti_nuke.normalize_antinuke_settings
    had_flag = hasattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, gate._HEALTH_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001

    monkeypatch.setattr(
        anti_nuke,
        "antinuke_permission_health",
        lambda _guild, settings=None: [],
    )
    monkeypatch.setattr(
        anti_nuke,
        "normalize_antinuke_settings",
        lambda settings: dict(settings or {}),
    )
    monkeypatch.setattr(
        gate,
        "delegated_authority_blockers",
        lambda _guild: ["Strict Lockdown: @Moderator grants Manage Server"],
    )

    try:
        assert gate._patch_health() is True  # noqa: SLF001
        missing = anti_nuke.antinuke_permission_health(
            guild,
            {
                "antinuke_enabled": True,
                "antinuke_mode": "contain",
                gate.STRICT_LOCKDOWN_KEY: True,
            },
        )
        assert missing == ["Strict Lockdown: @Moderator grants Manage Server"]
    finally:
        anti_nuke.antinuke_permission_health = original_health
        anti_nuke.normalize_antinuke_settings = original_normalize
        if had_flag:
            setattr(anti_nuke, gate._HEALTH_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, gate._HEALTH_FLAG):  # noqa: SLF001
            delattr(anti_nuke, gate._HEALTH_FLAG)  # noqa: SLF001


def test_save_gate_allows_normal_contain_with_staff_authority(monkeypatch) -> None:
    guild = _guild()
    bot = FakeBot(guild)
    original_save = anti_nuke.save_antinuke_settings
    original_normalize = anti_nuke.normalize_antinuke_settings
    had_flag = hasattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, gate._SAVE_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001

    async def current(_guild_id: int):
        return {
            "antinuke_enabled": False,
            "antinuke_mode": "contain",
            gate.STRICT_LOCKDOWN_KEY: False,
        }

    saves: list[dict] = []

    async def saved(_guild_id: int, patch):
        saves.append(dict(patch))
        return {**(await current(_guild_id)), **dict(patch)}

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", current)
    monkeypatch.setattr(anti_nuke, "save_antinuke_settings", saved)
    monkeypatch.setattr(
        anti_nuke,
        "normalize_antinuke_settings",
        lambda settings: dict(settings or {}),
    )
    monkeypatch.setattr(
        gate,
        "delegated_authority_blockers",
        lambda _guild: ["Strict Lockdown: @Moderator grants Manage Server"],
    )

    try:
        assert gate._patch_save(bot) is True  # noqa: SLF001
        result = asyncio.run(
            anti_nuke.save_antinuke_settings(
                7,
                {"antinuke_enabled": True, "antinuke_mode": "contain"},
            )
        )
        assert result["antinuke_enabled"] is True
        assert saves == [{"antinuke_enabled": True, "antinuke_mode": "contain"}]
    finally:
        anti_nuke.save_antinuke_settings = original_save
        anti_nuke.normalize_antinuke_settings = original_normalize
        if had_flag:
            setattr(anti_nuke, gate._SAVE_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, gate._SAVE_FLAG):  # noqa: SLF001
            delattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001


def test_save_gate_refuses_strict_lockdown_with_staff_authority(monkeypatch) -> None:
    guild = _guild()
    bot = FakeBot(guild)
    original_save = anti_nuke.save_antinuke_settings
    original_normalize = anti_nuke.normalize_antinuke_settings
    had_flag = hasattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, gate._SAVE_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001

    async def current(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            gate.STRICT_LOCKDOWN_KEY: False,
        }

    async def saved(_guild_id: int, patch):
        raise AssertionError("strict readiness blocker should stop the save")

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", current)
    monkeypatch.setattr(anti_nuke, "save_antinuke_settings", saved)
    monkeypatch.setattr(
        anti_nuke,
        "normalize_antinuke_settings",
        lambda settings: dict(settings or {}),
    )
    monkeypatch.setattr(
        gate,
        "delegated_authority_blockers",
        lambda _guild: ["Strict Lockdown: @Moderator grants Manage Server"],
    )

    try:
        assert gate._patch_save(bot) is True  # noqa: SLF001
        try:
            asyncio.run(
                anti_nuke.save_antinuke_settings(
                    7,
                    {gate.STRICT_LOCKDOWN_KEY: True},
                )
            )
        except RuntimeError as exc:
            assert "Strict Lockdown cannot be enabled" in str(exc)
        else:
            raise AssertionError("Strict Lockdown accepted delegated authority")
    finally:
        anti_nuke.save_antinuke_settings = original_save
        anti_nuke.normalize_antinuke_settings = original_normalize
        if had_flag:
            setattr(anti_nuke, gate._SAVE_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, gate._SAVE_FLAG):  # noqa: SLF001
            delattr(anti_nuke, gate._SAVE_FLAG)  # noqa: SLF001


def test_preexisting_normal_contain_does_not_warn(monkeypatch) -> None:
    guild = _guild()
    incidents: list[dict] = []
    gate._WARNED_GUILDS.clear()  # noqa: SLF001

    async def settings(_guild_id: int, refresh: bool = False):
        _ = refresh
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            gate.STRICT_LOCKDOWN_KEY: False,
        }

    async def post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", post)
    monkeypatch.setattr(
        gate,
        "delegated_authority_blockers",
        lambda _guild: ["Strict Lockdown: @Moderator grants Manage Server"],
    )

    assert asyncio.run(gate._warn_preexisting_unsafe_guild(guild)) is False  # noqa: SLF001
    assert incidents == []


def test_preexisting_strict_lockdown_blocker_is_reported_once(monkeypatch) -> None:
    guild = _guild()
    incidents: list[dict] = []
    gate._WARNED_GUILDS.clear()  # noqa: SLF001

    async def settings(_guild_id: int, refresh: bool = False):
        _ = refresh
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            gate.STRICT_LOCKDOWN_KEY: True,
        }

    async def post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", post)
    monkeypatch.setattr(
        gate,
        "delegated_authority_blockers",
        lambda _guild: ["Strict Lockdown: @Moderator grants Manage Server"],
    )

    assert asyncio.run(gate._warn_preexisting_unsafe_guild(guild)) is True  # noqa: SLF001
    assert asyncio.run(gate._warn_preexisting_unsafe_guild(guild)) is True  # noqa: SLF001
    assert len(incidents) == 1
    assert "Strict Lockdown" in incidents[0]["title"]
