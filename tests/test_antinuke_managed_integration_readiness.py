from __future__ import annotations

from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_finalizer_runtime as finalizer


class FakePermissions:
    def __init__(self, **values) -> None:
        for name in anti_nuke.DANGEROUS_PERMISSION_NAMES:
            setattr(self, name, bool(values.get(name, False)))
        self.view_audit_log = bool(values.get("view_audit_log", False))
        self.manage_roles = bool(values.get("manage_roles", False))
        self.kick_members = bool(values.get("kick_members", False))
        self.manage_channels = bool(values.get("manage_channels", False))
        self.manage_webhooks = bool(values.get("manage_webhooks", False))
        self.manage_guild = bool(values.get("manage_guild", False))
        self.administrator = bool(values.get("administrator", False))


class FakeRole:
    def __init__(
        self,
        role_id: int,
        name: str,
        position: int,
        *,
        permissions=None,
        managed: bool = False,
        default: bool = False,
    ) -> None:
        self.id = role_id
        self.name = name
        self.position = position
        self.permissions = permissions or FakePermissions()
        self.managed = managed
        self._default = default
        self.members: list[object] = []

    def is_default(self) -> bool:
        return self._default

    def __lt__(self, other) -> bool:
        return int(self.position) < int(getattr(other, "position", -1))


def _healthy_bot_member(top_role: FakeRole):
    return SimpleNamespace(
        id=500,
        top_role=top_role,
        guild_permissions=FakePermissions(
            view_audit_log=True,
            manage_roles=True,
            kick_members=True,
            manage_channels=True,
            manage_webhooks=True,
            manage_guild=True,
        ),
    )


def _enabled_settings():
    return anti_nuke.normalize_antinuke_settings(
        {"antinuke_enabled": True, "antinuke_mode": "contain"}
    )


def _with_managed_patch(run):
    original = anti_nuke._dangerous_hierarchy_blockers  # noqa: SLF001
    had_flag = hasattr(anti_nuke, finalizer._MANAGED_ROLE_PATCH_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, finalizer._MANAGED_ROLE_PATCH_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, finalizer._MANAGED_ROLE_PATCH_FLAG)  # noqa: SLF001
    try:
        assert finalizer._patch_managed_role_readiness() is True  # noqa: SLF001
        run()
    finally:
        anti_nuke._dangerous_hierarchy_blockers = original  # noqa: SLF001
        if had_flag:
            setattr(anti_nuke, finalizer._MANAGED_ROLE_PATCH_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, finalizer._MANAGED_ROLE_PATCH_FLAG):  # noqa: SLF001
            delattr(anti_nuke, finalizer._MANAGED_ROLE_PATCH_FLAG)  # noqa: SLF001


def test_managed_integration_below_dank_shield_does_not_block_enablement() -> None:
    def run() -> None:
        dank_role = FakeRole(100, "Dank Shield", 100)
        managed_admin = FakeRole(
            200,
            "DISBOARD.org",
            20,
            permissions=FakePermissions(administrator=True),
            managed=True,
        )
        integration_member = SimpleNamespace(
            id=444,
            roles=[managed_admin],
            top_role=managed_admin,
        )
        managed_admin.members = [integration_member]
        guild = SimpleNamespace(
            id=1,
            owner_id=999,
            me=_healthy_bot_member(dank_role),
            roles=[managed_admin, dank_role],
            channels=[],
        )

        missing = anti_nuke.antinuke_permission_health(guild, _enabled_settings())

        assert not any("DISBOARD.org" in item for item in missing)
        assert missing == []

    _with_managed_patch(run)


def test_managed_dangerous_holder_above_dank_shield_still_blocks_enablement() -> None:
    def run() -> None:
        dank_role = FakeRole(100, "Dank Shield", 50)
        managed_admin = FakeRole(
            200,
            "High Integration",
            80,
            permissions=FakePermissions(administrator=True),
            managed=True,
        )
        integration_member = SimpleNamespace(
            id=445,
            roles=[managed_admin],
            top_role=managed_admin,
        )
        managed_admin.members = [integration_member]
        guild = SimpleNamespace(
            id=2,
            owner_id=999,
            me=_healthy_bot_member(dank_role),
            roles=[managed_admin, dank_role],
            channels=[],
        )

        missing = anti_nuke.antinuke_permission_health(guild, _enabled_settings())

        assert any(
            "managed @High Integration cannot be stripped" in item
            for item in missing
        )

    _with_managed_patch(run)
