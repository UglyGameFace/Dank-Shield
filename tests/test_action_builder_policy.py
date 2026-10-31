from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import action_builder_policy as policy
from stoney_verify import action_center_service as inbox
from stoney_verify.commands_ext import public_action_builder as ui
from stoney_verify.commands_ext import public_action_center


def test_empty_guild_has_no_automatic_or_saved_reminders() -> None:
    draft = policy.parse_action_builder({})
    assert draft.revision == 0
    assert draft.rules == ()
    assert all(not draft.rule_for(key).included for key in policy.CONDITIONS)
    payload = draft.to_payload()
    assert payload["delivery_enabled"] is False
    assert payload["rules"] == []


def test_strict_rule_contract_rejects_arbitrary_member_targets_and_schedules() -> None:
    for bad in (
        {"condition": "all_members"},
        {"condition": "pending_verification", "severity": "recommended"},
        {"condition": "pending_verification", "delivery": "dm"},
        {"condition": "pending_verification", "schedule": "daily"},
        {"condition": "pending_verification", "audience": "everyone"},
        {"condition": "member_setup_revision", "severity": "dismissed"},
    ):
        with pytest.raises(ValueError):
            policy.DraftActionRule(**bad)
    for malicious in ("@everyone", "@here", "x" * 161):
        with pytest.raises(ValueError):
            policy.edit_rule(
                policy.ActionBuilderDraft(), condition="member_setup_revision",
                included=True, severity="recommended", note=malicious,
            )
    with pytest.raises(ValueError):
        policy.edit_rule(
            policy.ActionBuilderDraft(), condition="member_setup_revision",
            included="on", severity="required", note="Wrong type",
        )


def test_draft_round_trip_revision_and_isolated_rules() -> None:
    draft = policy.ActionBuilderDraft()
    first = policy.edit_rule(
        draft, condition="pending_verification",
        included=True, severity="required", note="Please finish verification",
    )
    second = policy.edit_rule(
        first, condition="member_setup_revision",
        included=True, severity="recommended", note="Review the update",
    )
    assert (first.revision, second.revision) == (1, 2)
    assert first.rule_for("member_setup_revision").included is False
    parsed = policy.parse_action_builder({policy.ACTION_BUILDER_KEY: second.to_payload()})
    assert parsed == second
    assert len(parsed.rules) == 2
    assert parsed.to_payload()["delivery_enabled"] is False
    assert parsed.rule_for("pending_verification").severity == "required"


def test_unknown_schema_and_invalid_delivery_fail_closed() -> None:
    for value in (
        {"version": 2, "rules": []},
        {"version": 1, "rules": [], "delivery_enabled": True},
        {"version": 1, "rules": [], "delivery_enabled": "false"},
        {"version": 1, "rules": [{"condition": "fake", "included": True}]},
    ):
        with pytest.raises(ValueError):
            policy.parse_action_builder({policy.ACTION_BUILDER_KEY: value})


def test_stale_save_blocked_and_uses_canonical_atomic_cas(monkeypatch) -> None:
    key = policy.ACTION_BUILDER_KEY
    cfg = {"source": "guild_configs"}
    existing = policy.parse_action_builder(cfg)
    updated = policy.edit_rule(
        existing, condition="member_setup_revision",
        included=True, severity="recommended", note="Reminder is only a draft",
    )
    called = []
    async def cas(guild_id, config_key, *, expected, value, source):
        called.append((guild_id, config_key, expected, value, source))
        return True, {config_key: value}
    from stoney_verify import guild_config
    monkeypatch.setattr(guild_config, "compare_and_swap_guild_config_key", cas)
    applied, saved = asyncio.run(
        policy.save_action_builder(4567, expected_config=cfg, draft=updated)
    )
    assert applied
    assert called[0][0:3] == (4567, key, None)
    assert called[0][3]["delivery_enabled"] is False
    assert called[0][4] == "action_reminder_center_draft"
    assert saved[key]["revision"] == 1
    with pytest.raises(ValueError, match="revision conflict"):
        asyncio.run(policy.save_action_builder(
            4567, expected_config=cfg, draft=policy.ActionBuilderDraft(revision=3),
        ))
    assert len(called) == 1


def test_preview_uses_owner_status_and_does_not_evaluate_unknown_as_complete() -> None:
    draft = policy.edit_rule(
        policy.ActionBuilderDraft(),
        condition="member_setup_revision", included=True,
        severity="recommended", note="Review setup",
    )
    required = inbox.setup_action({
        "enabled": True, "is_current": False, "current_revision": 12,
        "pending_sections": ["community"], "severity": "access_gated",
        "access_gated": True,
    })
    snapshot = inbox.ActionSnapshot(
        guild_id=123, user_id=42, items=(required,),
        checked_sources=("member_setup",), unavailable_sources=("verification",),
    )
    items = policy.preview_rules(draft, snapshot)
    assert items[0].condition == "pending_verification"
    assert items[0].status == "unavailable"
    assert items[1].status == "pending"
    assert items[1].severity == "required"  # Never downgrade canonical access gate.
    embed = ui._preview_embed(draft, snapshot)
    text = str(embed.to_dict())
    assert "Source unavailable" in text
    assert "No delivery" in text


def test_staff_builder_private_buttons_without_member_exposure() -> None:
    draft = policy.ActionBuilderDraft()
    panel = ui.ActionBuilderView(42, 123, draft)
    labels = {x.label for x in panel.children if isinstance(x, discord.ui.Button)}
    assert labels == {
        "Verification Rule", "Member Setup Rule", "Preview Conditions",
        "Refresh Draft", "Safety Schedule (Draft)", "Back to Action Inbox",
    }
    assert len(panel.children) <= 25
    snapshot = inbox.ActionSnapshot(
        guild_id=123, user_id=42, items=(),
        checked_sources=("verification", "member_setup"), unavailable_sources=(),
    )
    private = public_action_center.ActionCenterView(42, snapshot, is_staff=False)
    staff = public_action_center.ActionCenterView(42, snapshot, is_staff=True, section="staff")
    assert "Action Builder (Draft)" not in [getattr(x, "label", None) for x in private.children]
    assert "Action Builder (Draft)" in [getattr(x, "label", None) for x in staff.children]


def test_never_creates_new_ledger_scheduler_root_commands_or_guild_scans() -> None:
    source = Path(policy.__file__).read_text()
    visual = Path(ui.__file__).read_text()
    for forbidden in (
        "create_task(", "bot.guilds", "member.send(", "channel.send(",
        "supabase/migrations/", "discord.app_commands",
        "mark_section_reviewed(", "complete_current_revision(",
    ):
        assert forbidden not in source + visual
    assert "compare_and_swap_guild_config_key" in source
    assert "load_member_actions" in visual
    assert "scoped_interaction_is_staff" in visual
