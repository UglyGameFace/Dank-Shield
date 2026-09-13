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


def test_main_installs_product_policy_after_app_import_before_run() -> None:
    source = Path("main.py").read_text(encoding="utf-8")
    app_import = source.index("from stoney_verify.app import run as _run_dank_shield")
    product_install = source.index("    _install_anti_nuke_product_policy_runtime()", app_import)
    bot_run = source.index("    _run_dank_shield()", product_install)

    assert app_import < product_install < bot_run
