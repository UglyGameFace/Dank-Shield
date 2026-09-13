from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke
from stoney_verify import anti_nuke_incident_runtime as incident


def _enabled_settings():
    return anti_nuke.normalize_antinuke_settings(
        {"antinuke_enabled": True, "antinuke_mode": "contain"}
    )


def test_owner_destructive_burst_is_not_silently_exempt(monkeypatch) -> None:
    original_process = anti_nuke._process_claimed_destructive_event  # noqa: SLF001
    had_flag = hasattr(anti_nuke, incident._OWNER_POLICY_PATCH_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, incident._OWNER_POLICY_PATCH_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, incident._OWNER_POLICY_PATCH_FLAG)  # noqa: SLF001

    incidents: list[dict] = []

    async def fake_settings(_guild_id: int, *, refresh: bool = False):
        return _enabled_settings()

    async def fake_post(_guild, **kwargs):
        incidents.append(dict(kwargs))

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    monkeypatch.setattr(anti_nuke, "_post_incident", fake_post)
    anti_nuke._ACTION_WINDOWS.clear()  # noqa: SLF001
    anti_nuke._TRIGGER_COOLDOWNS.clear()  # noqa: SLF001

    guild = SimpleNamespace(id=5001, owner_id=42)
    owner = SimpleNamespace(id=42, mention="<@42>")
    entry = SimpleNamespace(user=owner)

    try:
        assert incident._patch_owner_compromise_policy() is True  # noqa: SLF001
        first = asyncio.run(
            anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
                guild,
                entry=entry,
                action_key="channel_delete",
                action_label="Channel deletion",
                target_label="#one",
                threshold_key="antinuke_channel_delete_threshold",
            )
        )
        second = asyncio.run(
            anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
                guild,
                entry=entry,
                action_key="channel_delete",
                action_label="Channel deletion",
                target_label="#two",
                threshold_key="antinuke_channel_delete_threshold",
            )
        )

        assert first is True
        assert second is True
        assert len(incidents) == 1
        assert incidents[0]["title"] == "🚨 AntiNuke Owner-Compromise Warning"
        assert "does not allow bots" in incidents[0]["response_label"]
        assert "cannot revoke" in incidents[0]["response_label"]
    finally:
        anti_nuke._process_claimed_destructive_event = original_process  # noqa: SLF001
        anti_nuke._ACTION_WINDOWS.clear()  # noqa: SLF001
        anti_nuke._TRIGGER_COOLDOWNS.clear()  # noqa: SLF001
        if had_flag:
            setattr(anti_nuke, incident._OWNER_POLICY_PATCH_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, incident._OWNER_POLICY_PATCH_FLAG):  # noqa: SLF001
            delattr(anti_nuke, incident._OWNER_POLICY_PATCH_FLAG)  # noqa: SLF001


def test_last_known_antinuke_state_survives_cold_config_outage(monkeypatch, tmp_path) -> None:
    state_file = tmp_path / "antinuke-security.json"
    monkeypatch.setenv("DANK_ANTINUKE_SECURITY_STATE_FILE", str(state_file))

    original_get = anti_nuke.get_antinuke_settings
    original_save = anti_nuke.save_antinuke_settings
    had_flag = hasattr(anti_nuke, incident._SECURITY_STATE_PATCH_FLAG)  # noqa: SLF001
    old_flag = getattr(anti_nuke, incident._SECURITY_STATE_PATCH_FLAG, None)  # noqa: SLF001
    if had_flag:
        delattr(anti_nuke, incident._SECURITY_STATE_PATCH_FLAG)  # noqa: SLF001
    incident._SECURITY_STATE_MEMORY.clear()  # noqa: SLF001

    mode = {"available": True}

    async def fake_config(_guild_id: int, *, refresh: bool = False):
        if mode["available"]:
            return {
                "source": "supabase:guild_configs",
                "antinuke_enabled": True,
                "antinuke_mode": "contain",
                "antinuke_channel_delete_threshold": 2,
            }
        return {"source": "unavailable:db_read_failed"}

    monkeypatch.setattr(incident.guild_config, "get_guild_config", fake_config)

    try:
        assert incident._patch_security_state_persistence() is True  # noqa: SLF001
        live = asyncio.run(anti_nuke.get_antinuke_settings(7001, refresh=True))
        assert live["antinuke_enabled"] is True
        assert state_file.exists()

        mode["available"] = False
        recovered = asyncio.run(anti_nuke.get_antinuke_settings(7001, refresh=True))
        assert recovered["antinuke_enabled"] is True
        assert recovered["antinuke_mode"] == "contain"
    finally:
        anti_nuke.get_antinuke_settings = original_get
        anti_nuke.save_antinuke_settings = original_save
        incident._SECURITY_STATE_MEMORY.clear()  # noqa: SLF001
        if had_flag:
            setattr(anti_nuke, incident._SECURITY_STATE_PATCH_FLAG, old_flag)  # noqa: SLF001
        elif hasattr(anti_nuke, incident._SECURITY_STATE_PATCH_FLAG):  # noqa: SLF001
            delattr(anti_nuke, incident._SECURITY_STATE_PATCH_FLAG)  # noqa: SLF001


def test_durable_snapshot_preserves_intentional_disabled_state(monkeypatch, tmp_path) -> None:
    state_file = tmp_path / "antinuke-security.json"
    monkeypatch.setenv("DANK_ANTINUKE_SECURITY_STATE_FILE", str(state_file))
    incident._SECURITY_STATE_MEMORY.clear()  # noqa: SLF001

    disabled = anti_nuke.normalize_antinuke_settings(
        {"antinuke_enabled": False, "antinuke_mode": "contain"}
    )
    assert incident._write_security_snapshot(7201, disabled) is True  # noqa: SLF001
    incident._SECURITY_STATE_MEMORY.clear()  # noqa: SLF001

    recovered = incident._read_security_snapshot(7201)  # noqa: SLF001
    assert recovered is not None
    assert recovered["antinuke_enabled"] is False


def test_prewarm_loads_security_state_for_every_connected_guild(monkeypatch) -> None:
    seen: list[tuple[int, bool]] = []

    async def fake_settings(guild_id: int, *, refresh: bool = False):
        seen.append((int(guild_id), bool(refresh)))
        return _enabled_settings()

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", fake_settings)
    bot = SimpleNamespace(
        guilds=[SimpleNamespace(id=8101), SimpleNamespace(id=8102)]
    )

    asyncio.run(incident._prewarm_security_state(bot))  # noqa: SLF001

    assert sorted(seen) == [(8101, True), (8102, True)]
