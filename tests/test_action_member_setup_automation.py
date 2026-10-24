from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import action_member_setup_automation as automation
from stoney_verify.action_builder_policy import (
    ActionBuilderDraft, edit_rule, ACTION_BUILDER_KEY,
)
from stoney_verify.action_schedule_policy import ActionSchedule
from stoney_verify.commands_ext import public_action_schedule as ui


def _config(*, included=True, published=True, source="supabase:guild_configs"):
    draft = edit_rule(
        ActionBuilderDraft(),
        condition="member_setup_revision", included=included,
        severity="recommended", note="",
    )
    return {
        "source": source,
        ACTION_BUILDER_KEY: draft.to_payload(),
        "member_setup_state": {
            "enabled": True, "current_revision": 3,
            "history": ([{"revision": 3, "published_at": "2026-10-22T12:00:00+00:00"}]
                        if published else []),
        },
    }


def test_default_off_and_unknown_policy_fails_closed():
    assert not automation.parse_automation({}).enabled
    assert automation.MemberSetupAutomation(True).to_payload() == {"version": 1, "enabled": True}
    for value in (
        {"version": 1, "enabled": "true"},
        {"version": 2, "enabled": True},
        {"version": 1, "enabled": 1},
        {"enabled": True},
    ):
        with pytest.raises(ValueError):
            automation.parse_automation({automation.KEY: value})
    assert not automation.verified_config({"source": "unavailable:db"})
    assert not automation.verified_config({"source": "env_fallback"})
    assert automation.activation_prerequisites(_config()) == ""
    assert "Include Member Setup" in automation.activation_prerequisites(_config(included=False))
    assert "published" in automation.activation_prerequisites(_config(published=False)).lower()


def test_server_manager_can_enable_and_disable_via_existing_atomic_config(monkeypatch):
    from stoney_verify import guild_config
    from stoney_verify import action_center_postgres
    saved = []

    async def compare(guild_id, key, *, expected, value, source):
        saved.append((guild_id, key, expected, value, source))
        return True, {key: value}

    monkeypatch.setattr(guild_config, "compare_and_swap_guild_config_key", compare)
    monkeypatch.setattr(action_center_postgres, "ledger_ready_sync", lambda: True)
    cfg = _config()
    assert asyncio.run(automation.save_automation_enabled(
        123, expected_config=cfg, enabled=True
    ))
    assert saved[-1][:4] == (123, automation.KEY, None, {"version": 1, "enabled": True})
    assert saved[-1][4] == "action_member_setup_automation_toggle"

    on = dict(cfg, **{automation.KEY: {"version": 1, "enabled": True}})
    assert asyncio.run(automation.save_automation_enabled(
        123, expected_config=on, enabled=False
    ))
    assert saved[-1][3] == {"version": 1, "enabled": False}


def test_no_ledger_or_missing_builder_prevents_enabling(monkeypatch):
    from stoney_verify import guild_config
    from stoney_verify import action_center_postgres
    attempts = []
    async def compare(*_args, **_kwargs):
        attempts.append(True)
        return True, {}
    monkeypatch.setattr(guild_config, "compare_and_swap_guild_config_key", compare)
    monkeypatch.setattr(action_center_postgres, "ledger_ready_sync", lambda: False)
    with pytest.raises(RuntimeError, match="ledger"):
        asyncio.run(automation.save_automation_enabled(
            123, expected_config=_config(), enabled=True
        ))
    with pytest.raises(ValueError, match="Include Member Setup"):
        asyncio.run(automation.save_automation_enabled(
            123, expected_config=_config(included=False), enabled=True
        ))
    with pytest.raises(RuntimeError):
        asyncio.run(automation.save_automation_enabled(
            123, expected_config=_config(source="unavailable:db"), enabled=True
        ))
    assert attempts == []


def test_owner_toggle_visible_without_discloud_variables_or_new_root_commands():
    view = ui.ScheduleView(42, 123, ActionSchedule())
    labels = [x.label for x in view.children if isinstance(x, discord.ui.Button)]
    assert "Enable Automatic Setup DMs" in labels
    enabled = ui.ScheduleView(42, 123, ActionSchedule(), automation_enabled=True)
    assert "Disable Automatic Setup DMs" in [
        x.label for x in enabled.children if isinstance(x, discord.ui.Button)
    ]
    embed = ui._embed(ActionSchedule(), automation_enabled=True)
    assert "ON" in embed.description
    assert "Unverified members are automatically eligible" in str(embed.to_dict())
    assert "scoped_interaction_is_staff(interaction)" in Path(ui.__file__).read_text()
    code = Path(automation.__file__).read_text()
    assert "compare_and_swap_guild_config_key" in code
    assert "read_member_consent" not in code  # No extra Unverified opt-in.
    assert "member.send(" not in code
    assert "DANK_ACTION_CENTER_CANARY_SEND" not in code
