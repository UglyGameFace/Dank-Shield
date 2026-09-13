from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_guardian_runtime as guardian
from stoney_verify import anti_nuke_incident_runtime as incident
from stoney_verify import anti_nuke_self_action_runtime as self_action
from stoney_verify import anti_nuke_zero_damage_runtime as runtime


class FakeGuild:
    def __init__(self, guild_id: int = 7, failures: int = 0) -> None:
        self.id = guild_id
        self.owner_id = 999
        self.failures = failures
        self.leave_calls = 0

    async def leave(self) -> None:
        self.leave_calls += 1
        if self.leave_calls <= self.failures:
            raise RuntimeError("temporary leave failure")


class FakeBot:
    def __init__(self, user_id: int = 55) -> None:
        self.user = SimpleNamespace(id=user_id)
        self.http = SimpleNamespace()
        self.guilds = []
        self.listeners: list[tuple[object, str]] = []

    def add_listener(self, listener, event_name: str) -> None:
        self.listeners.append((listener, event_name))

    def get_user(self, _user_id: int):
        return None

    async def fetch_user(self, _user_id: int):
        return None

    def get_channel(self, _channel_id: int):
        return None


class FakeRoute:
    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.url = f"https://discord.com/api/v10{path}"


def _reset_runtime_state() -> None:
    runtime._EJECTION_IN_PROGRESS.clear()  # noqa: SLF001
    runtime._WARNED.clear()  # noqa: SLF001
    self_action._COMPROMISE_GUILDS.clear()  # noqa: SLF001


def test_self_ejection_retries_before_marking_compromise_handled(monkeypatch) -> None:
    _reset_runtime_state()
    bot = FakeBot()
    guild = FakeGuild(failures=2)

    async def no_warning(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime, "_warn_owner", no_warning)

    assert asyncio.run(runtime.attempt_quarantine_ejection(bot, guild, "channel_delete")) is True
    assert guild.leave_calls == 3
    assert guild.id in self_action._COMPROMISE_GUILDS  # noqa: SLF001


def test_failed_self_ejection_does_not_suppress_future_retry(monkeypatch) -> None:
    _reset_runtime_state()
    bot = FakeBot()
    guild = FakeGuild(failures=99)

    async def no_warning(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime, "_warn_owner", no_warning)

    assert asyncio.run(runtime.attempt_quarantine_ejection(bot, guild, "role_delete")) is False
    assert guild.leave_calls == 3
    assert guild.id not in self_action._COMPROMISE_GUILDS  # noqa: SLF001

    guild.failures = guild.leave_calls
    assert asyncio.run(runtime.attempt_quarantine_ejection(bot, guild, "role_delete")) is True
    assert guild.leave_calls == 4


def test_self_compromise_settings_use_durable_snapshot_on_config_failure(monkeypatch) -> None:
    async def broken_settings(_guild_id: int):
        raise RuntimeError("config offline")

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", broken_settings)
    monkeypatch.setattr(
        incident,
        "_read_security_snapshot",
        lambda _guild_id: {"antinuke_enabled": True, "antinuke_mode": "contain"},
    )

    settings = asyncio.run(runtime._settings_fail_closed(7))  # noqa: SLF001
    assert settings["antinuke_enabled"] is True
    assert settings["antinuke_mode"] == "contain"


def test_self_compromise_settings_fail_closed_without_snapshot(monkeypatch) -> None:
    async def broken_settings(_guild_id: int):
        raise RuntimeError("config offline")

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", broken_settings)
    monkeypatch.setattr(incident, "_read_security_snapshot", lambda _guild_id: None)

    settings = asyncio.run(runtime._settings_fail_closed(7))  # noqa: SLF001
    assert settings == {"antinuke_enabled": True, "antinuke_mode": "contain"}


def test_direct_webhook_http_route_is_covered_by_final_self_proof() -> None:
    original_request_spec = self_action._request_spec  # noqa: SLF001
    original_actions = self_action._PROTECTED_ACTIONS  # noqa: SLF001
    original_unmatched = self_action._unmatched_self_action  # noqa: SLF001
    had_flag = hasattr(self_action, runtime._SELF_FLAG)  # noqa: SLF001
    old_flag = getattr(self_action, runtime._SELF_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(self_action, runtime._SELF_FLAG)  # noqa: SLF001

    try:
        assert runtime._patch_self_action() is True  # noqa: SLF001
        spec = self_action._request_spec(  # noqa: SLF001
            FakeBot(), FakeRoute("DELETE", "/webhooks/444"), {}
        )
        assert spec is not None
        assert spec.actions == frozenset({"webhook_delete"})
        assert spec.target_key == "id:444"
    finally:
        self_action._request_spec = original_request_spec  # noqa: SLF001
        self_action._PROTECTED_ACTIONS = original_actions  # noqa: SLF001
        self_action._unmatched_self_action = original_unmatched  # noqa: SLF001
        if had_flag:
            setattr(self_action, runtime._SELF_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(self_action, runtime._SELF_FLAG):  # noqa: SLF001
            delattr(self_action, runtime._SELF_FLAG)  # noqa: SLF001


def test_guardian_final_surface_includes_single_delete_and_admin_updates() -> None:
    old_actions = dict(guardian._ACTIONS)  # noqa: SLF001
    old_fields = guardian._GUILD_UPDATE_SECURITY_FIELDS  # noqa: SLF001
    old_weights = dict(guardian._PANIC_WEIGHTS)  # noqa: SLF001
    old_panic_actions = guardian._PANIC_ACTIONS  # noqa: SLF001
    old_severe = guardian._PANIC_SEVERE_ACTIONS  # noqa: SLF001
    had_flag = hasattr(guardian, runtime._GUARDIAN_FLAG)  # noqa: SLF001
    old_flag = getattr(guardian, runtime._GUARDIAN_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(guardian, runtime._GUARDIAN_FLAG)  # noqa: SLF001

    try:
        assert runtime._patch_guardian() is True  # noqa: SLF001
        for action in (
            "message_delete",
            "integration_create",
            "integration_update",
            "stage_instance_delete",
            "onboarding_update",
            "home_settings_update",
            "member_disconnect",
        ):
            assert action in guardian._ACTIONS  # noqa: SLF001
        assert guardian._ACTIONS["message_delete"][3] == 1  # noqa: SLF001
        for field in (
            "system_channel_id",
            "rules_channel_id",
            "public_updates_channel_id",
            "safety_alerts_channel_id",
            "preferred_locale",
            "features",
        ):
            assert field in guardian._GUILD_UPDATE_SECURITY_FIELDS  # noqa: SLF001
    finally:
        guardian._ACTIONS.clear()  # noqa: SLF001
        guardian._ACTIONS.update(old_actions)  # noqa: SLF001
        guardian._GUILD_UPDATE_SECURITY_FIELDS = old_fields  # noqa: SLF001
        guardian._PANIC_WEIGHTS.clear()  # noqa: SLF001
        guardian._PANIC_WEIGHTS.update(old_weights)  # noqa: SLF001
        guardian._PANIC_ACTIONS = old_panic_actions  # noqa: SLF001
        guardian._PANIC_SEVERE_ACTIONS = old_severe  # noqa: SLF001
        if had_flag:
            setattr(guardian, runtime._GUARDIAN_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(guardian, runtime._GUARDIAN_FLAG):  # noqa: SLF001
            delattr(guardian, runtime._GUARDIAN_FLAG)  # noqa: SLF001


def test_contain_mode_forces_first_strike_even_for_delegated_actor(monkeypatch) -> None:
    original_process = anti_nuke._process_claimed_destructive_event  # noqa: SLF001
    had_flag = hasattr(anti_nuke, runtime._POLICY_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, runtime._POLICY_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, runtime._POLICY_FLAG)  # noqa: SLF001

    seen: list[int | None] = []

    async def fake_original(_guild, **kwargs):
        seen.append(kwargs.get("threshold_override"))
        return True

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    monkeypatch.setattr(anti_nuke, "_process_claimed_destructive_event", fake_original)
    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_actor_is_owner_or_bot", lambda _guild, _actor: False)

    try:
        assert runtime._patch_zero_grace() is True  # noqa: SLF001
        entry = SimpleNamespace(user=SimpleNamespace(id=77))
        asyncio.run(
            anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
                SimpleNamespace(id=7),
                entry=entry,
                action_key="ban",
                action_label="ban",
                target_label="member",
                threshold_key="antinuke_ban_threshold",
                threshold_override=None,
            )
        )
        assert seen == [1]
    finally:
        anti_nuke._process_claimed_destructive_event = original_process  # noqa: SLF001
        if had_flag:
            setattr(anti_nuke, runtime._POLICY_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, runtime._POLICY_FLAG):  # noqa: SLF001
            delattr(anti_nuke, runtime._POLICY_FLAG)  # noqa: SLF001


def test_compromise_quarantine_survives_memory_reset(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "quarantine.json"
    monkeypatch.setenv("DANK_ANTINUKE_COMPROMISE_STATE_FILE", str(path))
    runtime._write_local(7, "channel_delete", int(time.time()) + 600)  # noqa: SLF001
    assert runtime._local_active(7) is True  # noqa: SLF001
    runtime._EJECTION_IN_PROGRESS.clear()  # noqa: SLF001
    runtime._WARNED.clear()  # noqa: SLF001
    assert runtime._local_active(7) is True  # noqa: SLF001


def test_discord_py_route_contract_is_pinned() -> None:
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert f"discord.py=={runtime.SUPPORTED_DISCORD_PY}" in requirements
