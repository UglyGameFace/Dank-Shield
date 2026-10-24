from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import discord
import pytest

from stoney_verify import action_center_service as service
from stoney_verify.commands_ext import public_action_center as ui
from stoney_verify import navigation_registry as nav
from stoney_verify.commands_ext import public_command_surface_v2 as surface


def test_action_contract_cannot_claim_scheduled_delivery_or_new_completion_truth() -> None:
    valid = service.verification_action(member_pending=True, cfg={})
    assert valid is not None
    assert valid.owner == "role_truth"
    assert valid.route == "verification"
    assert valid.target == "member"
    assert valid.condition == "pending"
    assert valid.delivery == "private_panel"
    assert valid.schedule == "on_open"
    assert service.verification_action(member_pending=False, cfg={}) is None
    args = vars(valid)
    for update in (
        {"route": "duplicate_verify"},
        {"severity": "automatic_ban"},
        {"delivery": "dm"},
        {"schedule": "hourly"},
        {"target": "guild"},
        {"condition": "already_complete"},
        {"description": "x" * 481},
    ):
        with pytest.raises(ValueError):
            service.ActionItem(**{**args, **update})


def test_member_setup_uses_owner_provided_revision_severity() -> None:
    status = {
        "enabled": True,
        "is_current": False,
        "current_revision": 9,
        "pending_sections": ["community", "notifications"],
        "severity": "recommended",
        "access_gated": False,
    }
    item = service.setup_action(status)
    assert item is not None
    assert item.key == "member_setup:revision:9"
    assert "Community" in item.description
    assert item.severity == "recommended"
    status["severity"] = "access_gated"
    assert service.setup_action(status).severity == "required"
    status["access_gated"] = True
    assert service.setup_action(status).severity == "required"
    status["is_current"] = True
    assert service.setup_action(status) is None
    status["is_current"] = False
    status["enabled"] = False
    assert service.setup_action(status) is None
    status["enabled"] = True
    status["pending_sections"] = []
    assert service.setup_action(status) is None


def test_results_sorted_and_recomputed_from_owners_each_open(monkeypatch) -> None:
    item = service.verification_action(member_pending=True, cfg={})
    status = {
        "enabled": True, "is_current": False, "current_revision": 2,
        "pending_sections": ["profile"], "severity": "recommended",
    }
    setup_item = service.setup_action(status)
    async def verification(_member):
        return item, True
    async def member_setup(_member):
        return setup_item
    monkeypatch.setattr(service, "_read_verification", verification)
    monkeypatch.setattr(service, "_read_member_setup", member_setup)
    class Member:
        id = 2345
        guild = type("Guild", (), {"id": 4567})()
    monkeypatch.setattr(service.discord, "Member", Member)
    result = asyncio.run(service.load_member_actions(Member()))
    assert [a.route for a in result.items] == ["verification", "member_setup"]
    assert result.checked_sources == ("verification", "member_setup")
    assert result.unavailable_sources == ()
    assert result.verification_reminders_enabled is True
    assert not result.is_clear

    async def cleared(_member):
        return None, False
    async def cleared_setup(_member):
        return None
    monkeypatch.setattr(service, "_read_verification", cleared)
    monkeypatch.setattr(service, "_read_member_setup", cleared_setup)
    result = asyncio.run(service.load_member_actions(Member()))
    assert result.is_clear
    assert result.items == ()


def test_unavailable_source_is_not_labeled_complete(monkeypatch) -> None:
    async def fails(_member):
        raise RuntimeError("Sensitive database error contains user data")
    async def blank(_member):
        return None
    monkeypatch.setattr(service, "_read_verification", fails)
    monkeypatch.setattr(service, "_read_member_setup", blank)
    class Member:
        id = 1357
        guild = type("Guild", (), {"id": 2468})()
    monkeypatch.setattr(service.discord, "Member", Member)
    result = asyncio.run(service.load_member_actions(Member()))
    assert result.items == ()
    assert not result.is_clear
    assert result.unavailable_sources == ("verification",)
    embed = ui._embed(result)
    assert "Some sources unavailable" in embed.description
    assert "Sensitive database error" not in str(embed.to_dict())


def test_owner_buttons_show_only_relevant_actions_and_require_staff() -> None:
    snap = service.ActionSnapshot(
        guild_id=7, user_id=11, items=(service.verification_action(member_pending=True, cfg={}),),
        checked_sources=("verification", "member_setup"), unavailable_sources=(),
    )
    member = ui.ActionCenterView(11, snap, is_staff=False)
    labels = {c.label for c in member.children if isinstance(c, discord.ui.Button)}
    assert labels == {"Find Verification", "My Reminder Preferences", "Refresh", "Home", "Close"}
    staff = ui.ActionCenterView(11, snap, is_staff=True)
    staff_labels = {c.label for c in staff.children if isinstance(c, discord.ui.Button)}
    assert "Verification Reminder Settings" in staff_labels
    assert "Member Setup Manager" in staff_labels
    assert "Notification Systems" in staff_labels
    assert "Review Member Setup" not in staff_labels
    assert len(staff.children) <= 25
    assert all(int(getattr(c, "row", 0) or 0) <= 4 for c in staff.children)


def test_no_new_worker_ledger_or_root_slash_command() -> None:
    source = Path(service.__file__).read_text(encoding="utf-8")
    surface_code = Path(surface.__file__).read_text(encoding="utf-8")
    ui_source = Path(ui.__file__).read_text(encoding="utf-8")
    assert "create_task(" not in source + ui_source
    # Private interaction replies are legitimate. What must be forbidden is
    # outbound member/channel delivery or a second database write engine.
    assert "member.send(" not in source + ui_source
    assert "channel.send(" not in source + ui_source
    assert ".rpc(" not in source + ui_source
    assert ".upsert(" not in source + ui_source
    assert ".insert(" not in source + ui_source
    assert "member_is_pending_verification" in source
    assert "_member_payload" in source
    assert "open_reminder_settings" in ui_source
    assert "open_member_setup_admin" in ui_source
    assert 'if key == "action_reminder_center":' in surface_code
    assert inspect.iscoroutinefunction(ui.open_action_center)


def test_registry_navigation_and_search_discover_action_center() -> None:
    feature = nav.feature_by_key("action_reminder_center")
    assert feature is not None
    assert feature.category == "actions"
    assert not feature.manager_only
    assert nav.search_features("reminder center")[0].key == "action_reminder_center"
    assert nav.search_features("my actions")[0].key == "action_reminder_center"
    assert "Action & Reminder Center" in {
        c.label for c in surface.FeatureCategoryView(11, "actions").children
        if isinstance(c, discord.ui.Button)
    }
    assert nav.REGISTRY_ERRORS == ()

def test_directory_uses_registered_canonical_routes_only() -> None:
    from stoney_verify.action_notification_owners import (
        NOTIFICATION_OWNERS, find_notification_owner,
    )
    from stoney_verify.commands_ext import public_action_notification_directory as directory
    assert len(NOTIFICATION_OWNERS) == 6
    assert len({item.key for item in NOTIFICATION_OWNERS}) == len(NOTIFICATION_OWNERS)
    assert len({item.feature_route for item in NOTIFICATION_OWNERS}) == len(NOTIFICATION_OWNERS)
    for item in NOTIFICATION_OWNERS:
        assert find_notification_owner(item.key) is item
        assert nav.feature_by_key(item.feature_route) is not None
        assert item.owner_module.startswith("stoney_verify.")
        assert item.delivery_contract
    assert find_notification_owner("unrecognized") is None
    view = directory.NotificationOwnerDirectoryView(42, 987)
    picker = next(c for c in view.children if isinstance(c, discord.ui.Select))
    assert {option.value for option in picker.options} == {item.key for item in NOTIFICATION_OWNERS}
    assert len(picker.options) <= 25
    assert all(len(option.description) <= 100 for option in picker.options)
    assert "does not check live enablement" in directory._directory_embed().description
    assert "Member Setup DMs remain disabled" in directory._directory_embed().description


def test_directory_denies_nonstaff_and_delegates_without_duplicate_sender(monkeypatch) -> None:
    from types import SimpleNamespace
    from stoney_verify.commands_ext import public_action_notification_directory as directory
    calls = []
    async def private(_interaction, message):
        calls.append(("private", message))
    async def router(_interaction, route):
        calls.append(("route", route))
    monkeypatch.setattr(directory, "_msg", private)
    monkeypatch.setattr(surface, "_route_feature", router)
    interaction = SimpleNamespace(guild=SimpleNamespace(id=99), guild_id=99, user=SimpleNamespace(id=21))
    monkeypatch.setattr(directory, "scoped_interaction_is_staff", lambda _: False)
    asyncio.run(directory.open_existing_notification_owner(interaction, "verification"))
    assert len(calls) == 1 and calls[0][0] == "private"
    calls.clear()
    monkeypatch.setattr(directory, "scoped_interaction_is_staff", lambda _: True)
    asyncio.run(directory.open_existing_notification_owner(interaction, "verification"))
    assert calls == [("route", "verification_reminders")]
    calls.clear()
    asyncio.run(directory.open_existing_notification_owner(interaction, "unknown"))
    assert len(calls) == 1 and calls[0][0] == "private"


def test_notification_directory_no_workers_sends_or_independent_configuration() -> None:
    from stoney_verify import action_notification_owners
    from stoney_verify.commands_ext import public_action_notification_directory as directory
    for module in (action_notification_owners, directory):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("create_task(", "member.send(", "channel.send(",
                          "reserve_member_setup_sync(", ".upsert(", ".insert(", ".rpc("):
            assert forbidden not in source
    assert "_route_feature(interaction, item.feature_route)" in Path(directory.__file__).read_text()
    assert "_member_payload" in Path(service.__file__).read_text()
