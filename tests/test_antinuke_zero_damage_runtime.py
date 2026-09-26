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
    self_action._PENDING.clear()  # noqa: SLF001
    self_action._EXPECTED_SIDE_EFFECTS.clear()  # noqa: SLF001


def _healthy_self_proof_bot() -> FakeBot:
    bot = FakeBot()
    setattr(bot, self_action._INSTALL_FLAG, True)  # noqa: SLF001
    setattr(bot.http, self_action._HTTP_PATCH_FLAG, True)  # noqa: SLF001
    return bot


def _entry(
    guild: FakeGuild,
    action: str,
    *,
    target_id: int | None = None,
    reason: str = "",
):
    return SimpleNamespace(
        guild=guild,
        action=SimpleNamespace(name=action),
        user=SimpleNamespace(id=55),
        user_id=55,
        target=(SimpleNamespace(id=target_id) if target_id is not None else None),
        reason=reason,
        extra=None,
    )


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
    original_local = set(self_action._LOCAL_PROVENANCE_ACTIONS)  # noqa: SLF001
    original_external = set(self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS)  # noqa: SLF001
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
        self_action._LOCAL_PROVENANCE_ACTIONS.clear()  # noqa: SLF001
        self_action._LOCAL_PROVENANCE_ACTIONS.update(original_local)  # noqa: SLF001
        self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS.clear()  # noqa: SLF001
        self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS.update(original_external)  # noqa: SLF001
        self_action._unmatched_self_action = original_unmatched  # noqa: SLF001
        if had_flag:
            setattr(self_action, runtime._SELF_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(self_action, runtime._SELF_FLAG):  # noqa: SLF001
            delattr(self_action, runtime._SELF_FLAG)  # noqa: SLF001


def test_provenance_contract_matrix_covers_zero_damage_protected_surface() -> None:
    original_request_spec = self_action._request_spec  # noqa: SLF001
    original_actions = self_action._PROTECTED_ACTIONS  # noqa: SLF001
    original_local = set(self_action._LOCAL_PROVENANCE_ACTIONS)  # noqa: SLF001
    original_external = set(self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS)  # noqa: SLF001
    original_unmatched = self_action._unmatched_self_action  # noqa: SLF001
    had_flag = hasattr(self_action, runtime._SELF_FLAG)  # noqa: SLF001
    old_flag = getattr(self_action, runtime._SELF_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(self_action, runtime._SELF_FLAG)  # noqa: SLF001

    try:
        assert runtime._patch_self_action() is True  # noqa: SLF001
        classified = (
            set(self_action._LOCAL_PROVENANCE_ACTIONS)  # noqa: SLF001
            | set(self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS)  # noqa: SLF001
        )
        assert set(self_action._PROTECTED_ACTIONS) <= classified  # noqa: SLF001
        assert not (
            set(self_action._LOCAL_PROVENANCE_ACTIONS)  # noqa: SLF001
            & set(self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS)  # noqa: SLF001
        )
        assert {
            "invite_update",
            "integration_create",
            "integration_update",
            "onboarding_prompt_create",
            "onboarding_prompt_update",
            "onboarding_prompt_delete",
            "home_settings_create",
            "home_settings_update",
            "member_move",
            "member_disconnect",
        } <= set(self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS)  # noqa: SLF001
        assert {"onboarding_create", "onboarding_update"} <= set(
            self_action._LOCAL_PROVENANCE_ACTIONS  # noqa: SLF001
        )
    finally:
        self_action._request_spec = original_request_spec  # noqa: SLF001
        self_action._PROTECTED_ACTIONS = original_actions  # noqa: SLF001
        self_action._LOCAL_PROVENANCE_ACTIONS.clear()  # noqa: SLF001
        self_action._LOCAL_PROVENANCE_ACTIONS.update(original_local)  # noqa: SLF001
        self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS.clear()  # noqa: SLF001
        self_action._EXTERNAL_ONLY_PROTECTED_ACTIONS.update(original_external)  # noqa: SLF001
        self_action._unmatched_self_action = original_unmatched  # noqa: SLF001
        if had_flag:
            setattr(self_action, runtime._SELF_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(self_action, runtime._SELF_FLAG):  # noqa: SLF001
            delattr(self_action, runtime._SELF_FLAG)  # noqa: SLF001


def test_self_ejection_safety_holds_when_http_proof_is_unhealthy(monkeypatch) -> None:
    _reset_runtime_state()
    bot = FakeBot()
    setattr(bot, self_action._INSTALL_FLAG, True)  # noqa: SLF001
    guild = FakeGuild()
    incidents: list[dict] = []

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", post)

    asyncio.run(
        runtime._unmatched_self_action(  # noqa: SLF001
            bot,
            guild,
            _entry(guild, "channel_delete", target_id=90),
            "channel_delete",
        )
    )

    assert guild.leave_calls == 0
    assert incidents
    assert "Self-Provenance Safety Hold" in incidents[0]["title"]


def test_audit_compat_action_holds_when_compat_route_proof_is_missing(
    monkeypatch,
) -> None:
    _reset_runtime_state()
    bot = _healthy_self_proof_bot()
    guild = FakeGuild()
    incidents: list[dict] = []
    had_flag = hasattr(self_action, runtime._AUDIT_COMPAT_ROUTE_FLAG)  # noqa: SLF001
    old_flag = getattr(
        self_action,
        runtime._AUDIT_COMPAT_ROUTE_FLAG,  # noqa: SLF001
        None,
    )
    if had_flag:
        delattr(self_action, runtime._AUDIT_COMPAT_ROUTE_FLAG)  # noqa: SLF001

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", post)
    try:
        asyncio.run(
            runtime._unmatched_self_action(  # noqa: SLF001
                bot,
                guild,
                _entry(guild, "member_disconnect", target_id=90),
                "member_disconnect",
            )
        )
    finally:
        if had_flag:
            setattr(
                self_action,
                runtime._AUDIT_COMPAT_ROUTE_FLAG,  # noqa: SLF001
                old_flag,
            )
        elif hasattr(self_action, runtime._AUDIT_COMPAT_ROUTE_FLAG):  # noqa: SLF001
            delattr(self_action, runtime._AUDIT_COMPAT_ROUTE_FLAG)  # noqa: SLF001

    assert guild.leave_calls == 0
    assert incidents
    assert "audit-compat-route-proof-unhealthy" in incidents[0]["details"]


def test_recent_local_request_without_marker_blocks_self_ejection(monkeypatch) -> None:
    _reset_runtime_state()
    bot = _healthy_self_proof_bot()
    guild = FakeGuild()
    self_action._authorize(  # noqa: SLF001
        self_action._spec(("channel_update",), guild.id, "id:90"),  # noqa: SLF001
        "local rename",
    )
    incidents: list[dict] = []

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", post)

    asyncio.run(
        runtime._unmatched_self_action(  # noqa: SLF001
            bot,
            guild,
            _entry(guild, "channel_update", target_id=90, reason=""),
            "channel_update",
        )
    )

    assert guild.leave_calls == 0
    assert incidents
    assert "recent-local-request-without-audit-marker" in incidents[0]["details"]


def test_known_local_marker_scope_mismatch_blocks_self_ejection(monkeypatch) -> None:
    _reset_runtime_state()
    bot = _healthy_self_proof_bot()
    guild = FakeGuild()
    _nonce, reason = self_action._authorize(  # noqa: SLF001
        self_action._spec(("channel_update",), guild.id, "id:90"),  # noqa: SLF001
        "local rename",
    )
    incidents: list[dict] = []

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def post(_guild, **kwargs):
        incidents.append(kwargs)

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", post)

    asyncio.run(
        runtime._unmatched_self_action(  # noqa: SLF001
            bot,
            guild,
            _entry(guild, "channel_update", target_id=91, reason=reason),
            "channel_update",
        )
    )

    assert guild.leave_calls == 0
    assert incidents
    assert "known-local-marker-scope-mismatch" in incidents[0]["details"]


def test_healthy_unmatched_self_action_still_quarantines_and_ejects(monkeypatch) -> None:
    _reset_runtime_state()
    bot = _healthy_self_proof_bot()
    guild = FakeGuild()
    persisted: list[tuple[int, str]] = []

    async def settings(_guild_id: int):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def persist(guild_id: int, action_name: str):
        persisted.append((guild_id, action_name))
        return 0

    async def no_warning(*_args, **_kwargs):
        return None

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(runtime, "_persist_quarantine", persist)
    monkeypatch.setattr(runtime, "_warn_owner", no_warning)

    asyncio.run(
        runtime._unmatched_self_action(  # noqa: SLF001
            bot,
            guild,
            _entry(guild, "channel_delete", target_id=90, reason="external"),
            "channel_delete",
        )
    )

    assert persisted == [(guild.id, "channel_delete")]
    assert guild.leave_calls == 1
    assert guild.id in self_action._COMPROMISE_GUILDS  # noqa: SLF001


def test_guardian_final_surface_is_expanded_but_first_strike_stays_scoped() -> None:
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
        assert guardian._ACTIONS["integration_update"][3] == 1  # noqa: SLF001
        assert guardian._ACTIONS["home_settings_update"][3] == 1  # noqa: SLF001
        assert guardian._ACTIONS["emoji_create"][3] is None  # noqa: SLF001
        assert guardian._ACTIONS["scheduled_event_create"][3] is None  # noqa: SLF001

        for field in (
            "verification_level",
            "explicit_content_filter",
            "rules_channel_id",
            "public_updates_channel_id",
            "safety_alerts_channel_id",
            "features",
            "mfa_level",
            "owner",
            "vanity_url_code",
        ):
            assert field in guardian._GUILD_UPDATE_SECURITY_FIELDS  # noqa: SLF001

        for field in (
            "name",
            "icon",
            "description",
            "default_message_notifications",
            "afk_channel_id",
            "afk_timeout",
            "system_channel_id",
            "system_channel_flags",
            "preferred_locale",
            "premium_progress_bar_enabled",
        ):
            assert field not in guardian._GUILD_UPDATE_SECURITY_FIELDS  # noqa: SLF001

        routine_entry = SimpleNamespace(
            before=SimpleNamespace(name="Old", afk_timeout=300),
            after=SimpleNamespace(name="New", afk_timeout=600),
        )
        assert guardian._guild_update_security_fields(routine_entry) == []  # noqa: SLF001

        security_entry = SimpleNamespace(
            before=SimpleNamespace(verification_level=3, rules_channel_id=10),
            after=SimpleNamespace(verification_level=0, rules_channel_id=11),
        )
        assert set(guardian._guild_update_security_fields(security_entry)) == {  # noqa: SLF001
            "rules_channel_id",
            "verification_level",
        }
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
    assert f"discord.py[voice]=={runtime.SUPPORTED_DISCORD_PY}" in requirements
