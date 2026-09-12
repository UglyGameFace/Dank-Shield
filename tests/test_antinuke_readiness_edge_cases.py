from __future__ import annotations

from types import SimpleNamespace

from stoney_verify import anti_nuke


class FakePermissions:
    def __init__(self, **values):
        for name in anti_nuke.DANGEROUS_PERMISSION_NAMES:
            setattr(self, name, bool(values.get(name, False)))
        self.view_audit_log = bool(values.get("view_audit_log", False))
        self.manage_roles = bool(values.get("manage_roles", False))


class FakeRole:
    def __init__(
        self,
        role_id: int,
        name: str,
        *,
        permissions=None,
        managed: bool = False,
        default: bool = False,
        members=None,
    ) -> None:
        self.id = role_id
        self.name = name
        self.permissions = permissions or FakePermissions()
        self.managed = managed
        self._default = default
        self.members = list(members or [])

    def is_default(self) -> bool:
        return self._default


def test_managed_dangerous_role_blocks_containment_readiness() -> None:
    managed_admin = FakeRole(
        200,
        "Managed Admin",
        permissions=FakePermissions(administrator=True),
        managed=True,
        members=[SimpleNamespace(id=444, roles=[])],
    )
    me = SimpleNamespace(
        top_role=SimpleNamespace(id=100),
        guild_permissions=FakePermissions(view_audit_log=True, manage_roles=True),
    )
    guild = SimpleNamespace(
        id=1,
        owner_id=999,
        me=me,
        roles=[managed_admin],
    )
    settings = anti_nuke.normalize_antinuke_settings({"antinuke_enabled": True})

    blockers = anti_nuke.antinuke_permission_health(guild, settings)

    assert any(
        "managed @Managed Admin cannot be stripped" in item
        for item in blockers
    )

    trusted_settings = anti_nuke.normalize_antinuke_settings(
        {
            "antinuke_enabled": True,
            "antinuke_trusted_role_ids": [managed_admin.id],
        }
    )
    assert anti_nuke.antinuke_permission_health(guild, trusted_settings) == []


def test_never_triggered_actor_is_ready_even_at_low_monotonic_time(monkeypatch) -> None:
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    monkeypatch.setattr(anti_nuke.time, "monotonic", lambda: 1.0)

    assert anti_nuke._trigger_ready(1, 2, anti_nuke._CONTAINMENT_COOLDOWN_KEY) is True


def test_successful_trigger_respects_containment_cooldown(monkeypatch) -> None:
    anti_nuke._TRIGGER_COOLDOWNS.clear()
    now = {"value": 10.0}
    monkeypatch.setattr(anti_nuke.time, "monotonic", lambda: now["value"])

    anti_nuke._mark_triggered(1, 2, anti_nuke._CONTAINMENT_COOLDOWN_KEY)
    now["value"] = 20.0
    assert anti_nuke._trigger_ready(1, 2, anti_nuke._CONTAINMENT_COOLDOWN_KEY) is False

    now["value"] = 40.0
    assert anti_nuke._trigger_ready(1, 2, anti_nuke._CONTAINMENT_COOLDOWN_KEY) is True
