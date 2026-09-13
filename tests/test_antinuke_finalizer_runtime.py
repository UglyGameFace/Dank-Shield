from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_finalizer_runtime as finalizer
from stoney_verify import anti_nuke_gateway_runtime as gateway


class FakePermissions:
    def __init__(self, *, manage_guild: bool) -> None:
        self.administrator = False
        self.view_audit_log = True
        self.manage_roles = True
        self.kick_members = True
        self.manage_channels = True
        self.manage_webhooks = True
        self.manage_guild = manage_guild


class FakeBot:
    def __init__(self, *, moderation: bool, gateway_ready: bool) -> None:
        self.intents = SimpleNamespace(moderation=moderation)
        self.removed: list[tuple[object, str]] = []
        if gateway_ready:
            setattr(self, gateway._INSTALL_FLAG, True)  # noqa: SLF001

    def remove_listener(self, listener, event_name: str) -> None:
        self.removed.append((listener, event_name))


def _guild(*, manage_guild: bool):
    return SimpleNamespace(
        id=9001,
        owner_id=999999,
        roles=[],
        channels=[],
        me=SimpleNamespace(
            id=555,
            top_role=SimpleNamespace(id=100, position=100),
            guild_permissions=FakePermissions(manage_guild=manage_guild),
        ),
    )


def test_contain_readiness_requires_manage_server_for_automod_recovery() -> None:
    original = anti_nuke.antinuke_permission_health
    had_flag = hasattr(anti_nuke, finalizer._HEALTH_PATCH_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, finalizer._HEALTH_PATCH_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, finalizer._HEALTH_PATCH_FLAG)  # noqa: SLF001

    try:
        assert finalizer._patch_permission_health() is True  # noqa: SLF001
        settings = anti_nuke.normalize_antinuke_settings(
            {"antinuke_enabled": True, "antinuke_mode": "contain"}
        )

        missing = anti_nuke.antinuke_permission_health(
            _guild(manage_guild=False), settings
        )
        assert "Manage Server" in missing

        healthy = anti_nuke.antinuke_permission_health(
            _guild(manage_guild=True), settings
        )
        assert "Manage Server" not in healthy
    finally:
        anti_nuke.antinuke_permission_health = original
        if had_flag:
            setattr(anti_nuke, finalizer._HEALTH_PATCH_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, finalizer._HEALTH_PATCH_FLAG):  # noqa: SLF001
            delattr(anti_nuke, finalizer._HEALTH_PATCH_FLAG)  # noqa: SLF001


def test_gateway_owns_channel_create_when_moderation_audit_gateway_is_ready() -> None:
    bot = FakeBot(moderation=True, gateway_ready=True)

    assert finalizer._retire_legacy_channel_create_owner(bot) is True  # noqa: SLF001
    assert (
        anti_nuke.antinuke_on_guild_channel_create,
        "on_guild_channel_create",
    ) in bot.removed


def test_native_channel_create_fallback_is_preserved_without_gateway() -> None:
    bot = FakeBot(moderation=False, gateway_ready=False)

    assert finalizer._retire_legacy_channel_create_owner(bot) is False  # noqa: SLF001
    assert bot.removed == []


def test_main_installs_protection_layers_in_order_before_app_import() -> None:
    source = Path("main.py").read_text(encoding="utf-8")
    expected = (
        "    _install_anti_nuke_gateway_runtime()\n"
        "    _install_anti_nuke_finalizer_runtime()\n"
        "    _install_anti_nuke_incident_runtime()\n"
        "    _install_hostile_actor_runtime()\n"
        "    _install_anti_nuke_lockdown_runtime()\n"
        "    _install_anti_nuke_self_action_runtime()\n"
        "    _install_anti_nuke_zero_damage_runtime()\n"
        "    from stoney_verify.app import run as _run_dank_shield"
    )
    assert expected in source
