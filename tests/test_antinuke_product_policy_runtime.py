from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_lockdown_runtime as lockdown
from stoney_verify import anti_nuke_product_policy_runtime as policy


def _clear_flag(target, flag: str):
    had = hasattr(target, flag)
    old = getattr(target, flag, None)
    if had:
        delattr(target, flag)
    return had, old


def _restore_flag(target, flag: str, had: bool, old) -> None:
    if had:
        setattr(target, flag, old)
    elif hasattr(target, flag):
        delattr(target, flag)


def test_strict_setting_defaults_off_and_normalizes_true(monkeypatch) -> None:
    original_normalize = anti_nuke.normalize_antinuke_settings
    original_defaults = dict(anti_nuke.ANTINUKE_DEFAULTS)
    had, old = _clear_flag(anti_nuke, policy._SETTING_FLAG)  # noqa: SLF001

    try:
        assert policy._patch_setting_model() is True  # noqa: SLF001
        assert anti_nuke.normalize_antinuke_settings({})[policy.STRICT_LOCKDOWN_KEY] is False
        assert (
            anti_nuke.normalize_antinuke_settings(
                {policy.STRICT_LOCKDOWN_KEY: "true"}
            )[policy.STRICT_LOCKDOWN_KEY]
            is True
        )
        assert policy.STRICT_LOCKDOWN_KEY in anti_nuke.ANTINUKE_DEFAULTS
    finally:
        anti_nuke.normalize_antinuke_settings = original_normalize
        anti_nuke.ANTINUKE_DEFAULTS.clear()
        anti_nuke.ANTINUKE_DEFAULTS.update(original_defaults)
        _restore_flag(anti_nuke, policy._SETTING_FLAG, had, old)  # noqa: SLF001


def test_normal_contain_does_not_force_trusted_structural_threshold(monkeypatch) -> None:
    calls: list[int | None] = []
    state = {policy.STRICT_LOCKDOWN_KEY: False}

    async def settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            **state,
        }

    async def base(_guild, **kwargs):
        calls.append(kwargs.get("threshold_override"))
        return True

    had, old = _clear_flag(anti_nuke, policy._PROCESS_FLAG)  # noqa: SLF001
    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", base)
    monkeypatch.setattr(
        lockdown,
        "_STRICT_PROCESS_ACTION_KEYS",
        frozenset({"channel_delete"}),
    )

    try:
        assert policy._patch_threshold_policy() is True  # noqa: SLF001
        entry = SimpleNamespace(user=SimpleNamespace(id=77))
        guild = SimpleNamespace(id=7)
        asyncio.run(
            anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
                guild,
                entry=entry,
                action_key="channel_delete",
                action_label="delete",
                target_label="#general",
                threshold_key="antinuke_channel_delete_threshold",
                threshold_override=None,
            )
        )
        assert calls == [None]

        state[policy.STRICT_LOCKDOWN_KEY] = True
        asyncio.run(
            anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
                guild,
                entry=entry,
                action_key="channel_delete",
                action_label="delete",
                target_label="#general",
                threshold_key="antinuke_channel_delete_threshold",
                threshold_override=None,
            )
        )
        assert calls == [None, 1]
    finally:
        _restore_flag(anti_nuke, policy._PROCESS_FLAG, had, old)  # noqa: SLF001


def test_guardian_policy_restores_normal_trust_and_reapplies_strict(monkeypatch) -> None:
    state = {policy.STRICT_LOCKDOWN_KEY: False}
    seen_overrides: list[int | None] = []
    seen_actor_ids: list[int] = []

    async def settings(_guild_id: int):
        return {
            "antinuke_enabled": True,
            "antinuke_mode": "contain",
            **state,
        }

    async def base_process(_guild, _entry, _actor, _action_name, spec):
        seen_overrides.append(spec[3])

    async def base_rollback(_guild, _entry, actor, _action_name):
        seen_actor_ids.append(int(actor.id))
        return "ok"

    fake_guardian = SimpleNamespace(
        _ACTIONS={
            "channel_delete": (
                "Channel deletion",
                "antinuke_channel_delete_threshold",
                "channel_delete",
                1,
            )
        },
        _rollback_untrusted_overwrite=base_rollback,
        _rollback_untrusted_automod=base_rollback,
        _process=base_process,
    )
    fake_lockdown = SimpleNamespace(
        _STRICT_GUARDIAN_ACTIONS=frozenset({"channel_delete"})
    )
    fake_zero = SimpleNamespace(_STRICT_ACTIONS=frozenset({"channel_delete"}))

    monkeypatch.setattr(policy, "guardian", fake_guardian)
    monkeypatch.setattr(policy, "lockdown", fake_lockdown)
    monkeypatch.setattr(policy, "zero_damage", fake_zero)
    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(
        anti_nuke,
        "_actor_is_owner_or_bot",
        lambda _guild, _actor: False,
    )

    assert policy._patch_guardian_policy() is True  # noqa: SLF001
    assert fake_guardian._ACTIONS["channel_delete"][3] is None

    guild = SimpleNamespace(id=7)
    actor = SimpleNamespace(id=77, roles=[])
    spec = fake_guardian._ACTIONS["channel_delete"]

    asyncio.run(
        fake_guardian._process(guild, object(), actor, "channel_delete", spec)
    )
    asyncio.run(
        fake_guardian._rollback_untrusted_overwrite(
            guild, object(), actor, "overwrite_update"
        )
    )
    assert seen_overrides == [None]
    assert seen_actor_ids == [77]

    state[policy.STRICT_LOCKDOWN_KEY] = True
    asyncio.run(
        fake_guardian._process(guild, object(), actor, "channel_delete", spec)
    )
    asyncio.run(
        fake_guardian._rollback_untrusted_overwrite(
            guild, object(), actor, "overwrite_update"
        )
    )
    assert seen_overrides == [None, 1]
    assert seen_actor_ids == [77, 0]


def test_health_message_does_not_invent_bot_permission_failures() -> None:
    message = policy._health_message(  # noqa: SLF001
        "Strict Lockdown was not enabled. Readiness blockers:",
        ["Strict Lockdown: @Manager grants Manage Server"],
        strict=True,
    )

    assert "Normal **Contain** can remain enabled" in message
    assert "View Audit Log" not in message
    assert "Manage Roles" not in message
    assert "Kick Members" not in message


def test_health_message_reports_only_real_bot_readiness_items() -> None:
    message = policy._health_message(  # noqa: SLF001
        "AntiNuke was not enabled. Readiness blockers:",
        ["View Audit Log", "Role hierarchy: move Dank Shield above @Admin"],
        strict=False,
    )

    assert "View Audit Log" in message
    assert "Role hierarchy" in message
    assert "Administrator is not required" in message
    assert "Strict Lockdown** is the blocker" not in message


def test_strict_ui_control_is_owner_guarded_and_named() -> None:
    source = inspect.getsource(policy._patch_ui)  # noqa: SLF001

    assert "_require_antinuke_owner" in source
    assert "dank_protection:antinuke_strict_lockdown" in source
    assert "Normal Contain is the recommended starting mode" in source


def test_main_installs_post_app_policy_after_app_import_before_run() -> None:
    coordinator_source = Path(
        "stoney_verify/anti_nuke_runtime_coordinator.py"
    ).read_text(encoding="utf-8")
    product = coordinator_source.index('"product_policy"')
    reentry = coordinator_source.index('"reentry_race"', product)
    assert product < reentry

    source = Path("main.py").read_text(encoding="utf-8")
    app_import = source.index("from stoney_verify.app import run as _run_dank_shield")
    post_install = source.index("    install_anti_nuke_post_app(bot)", app_import)
    bot_run = source.index("    _run_dank_shield()", post_install)
    assert app_import < post_install < bot_run
