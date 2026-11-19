"""Slice 9 picker migration (FINAL slice) — backups, history & everything remaining.

Felix's standing rule: every picker uses the house framework
(``stoney_verify/ui/picker.py``). Slice 9 migrated the last raw
surfaces: the server-backup panel pickers (incl. the ``[:25]``-
truncated ``BackupSelect``), the config-history pickers, and every
remaining census row not covered by Slices 1–8 — cleanup / verify
centers, live captions, modlog, role center, permission repair,
profile cards & signature studio, welcome setup & events, member
review, the action-notification directory, the owner guards, the
Embed Studio campaign/live pickers, the verification-reminder
recipient picker (which had no custom_id), and the persistent
Embed Studio role dropdown.

These tests prove:
- every migrated class is a house-framework class;
- lists over 25 page instead of truncating (backups, modlog
  channels, permission groups, config-history versions);
- a backup pick routes to ``_render_panel`` with exactly the
  picked backup's id — the same record the raw select selected;
- the reminder recipient picker keeps all candidates selected by
  default and a pick stores exactly the picked member ids;
- the persistent role dropdown keeps its identical stable
  custom_id through the framework's ``DankPersistentSelect``;
- FINAL SCAN: no raw ``discord.ui`` select subclasses or ad-hoc
  select instantiations remain anywhere in ``stoney_verify/``
  outside ``ui/picker.py`` and the census's two D-class standing
  exceptions.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify import config_history_ui as history
from stoney_verify import embed_studio_roles as studio_roles
from stoney_verify import member_review_ui as review
from stoney_verify import server_backup_ui as backups_ui
from stoney_verify.commands_ext import public_modlog_group as modlog
from stoney_verify.commands_ext import public_role_center as role_center
from stoney_verify.commands_ext import public_verify_reminder_ui as reminder_ui
from stoney_verify.ui import picker as _picker_mod

# getattr with an empty-tuple fallback so this file still collects
# on the pre-slice base (where DankPagedPickerView /
# DankPersistentSelect do not exist yet) and fails there with plain
# assertion errors instead of an import error.
DankChannelSelect = getattr(_picker_mod, "DankChannelSelect", ())
DankMultiPickerView = getattr(_picker_mod, "DankMultiPickerView", ())
DankPagedPickerView = getattr(_picker_mod, "DankPagedPickerView", ())
DankPersistentSelect = getattr(_picker_mod, "DankPersistentSelect", ())
DankRoleSelect = getattr(_picker_mod, "DankRoleSelect", ())
DankUserSelect = getattr(_picker_mod, "DankUserSelect", ())

ROOT = Path(__file__).resolve().parents[1]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Framework-class assertions for the whole slice
# ---------------------------------------------------------------------------


def test_slice9_classes_are_framework_classes():
    from stoney_verify import (
        embed_studio_campaign_ui as campaign_ui,
        embed_studio_live_ui as live_ui,
        permission_repair_core as repair,
        profile_card_setup_ui as profile_cards,
        profile_signature_studio as signature,
        roles_center_services as roles_services,
        welcome_event_services as welcome_events,
        welcome_setup_ui as welcome_setup,
    )
    from stoney_verify.commands_ext import (
        public_action_notification_directory as directory,
        public_cleanup_command_center as cleanup,
        public_live_captions as captions,
        public_verify_command_center as verify_center,
    )
    from stoney_verify.startup_guards import (
        owner_emergency_override_guard as emergency,
        owner_safe_members_guard as safe_members,
    )

    paged = [
        backups_ui.BackupSelect,
        backups_ui.RestoreModeSelect,
        history.ConfigHistorySelect,
        modlog.ModlogChannelSelect,
        role_center.RolesProfilesSectionSelect,
        role_center.PermissionGroupSelect,
        repair._FeatureSelect,
        repair._ModeSelect,
        welcome_setup._WelcomeSectionSelect,
        welcome_events.WelcomeEventChannelSelect,
        welcome_events.WelcomePresetSelect,
        directory.NotificationOwnerPicker,
        captions.CaptionLanguageGroupSelect,
        captions.CaptionLanguageChoiceSelect,
        captions.CaptionLanguageOutputSelect,
        review.MoreReviewActionsSelect,
        signature.PlatformSelect,
        emergency.OwnerEmergencyActionSelect,
    ]
    for cls in paged:
        assert issubclass(cls, DankPagedPickerView), cls

    # The Embed Studio pickers ride the studio session picker
    # (_StudioPickerView, a DankPickerView with the session's
    # owner+staff gate and chunk paging) from Slice 1.
    from stoney_verify.ui.picker import DankPickerView

    for cls in (campaign_ui.StudioCampaignSelect, live_ui.LiveTextSelect):
        assert issubclass(cls, DankPickerView), cls

    for cls in (history.BackupDomainSelect, history.RestoreItemSelect, role_center.PermissionToggleSelect):
        assert issubclass(cls, DankMultiPickerView), cls

    for cls in (
        cleanup.CleanupChannelSelect,
        cleanup.UserPurgeChannelSelect,
        repair._TargetChannelSelect,
        profile_cards.LiveProfileChannelSelect,
        roles_services.RolePanelChannelSelect,
        welcome_setup.WelcomeChannelSelect,
    ):
        assert issubclass(cls, DankChannelSelect), cls

    assert issubclass(cleanup.CleanupUserSelect, DankUserSelect)
    assert issubclass(verify_center.VerifyMemberSelect, DankUserSelect)
    assert issubclass(verify_center.VerifyRoleSelect, DankRoleSelect)
    assert issubclass(safe_members.AddSafeMemberSelect, DankUserSelect)
    assert issubclass(safe_members.RemoveSafeMemberSelect, DankUserSelect)


# ---------------------------------------------------------------------------
# Backups: >25 paging + pick selects exactly the same record
# ---------------------------------------------------------------------------


def _fake_backups(count: int) -> list[dict[str, Any]]:
    return [
        {
            "backup_id": 1000 + i,
            "label": f"Backup {i}",
            "created_at": "2026-11-19T00:00:00+00:00",
            "snapshot_bytes": 2048,
        }
        for i in range(count)
    ]


def test_backup_select_pages_beyond_25():
    picker = backups_ui.BackupSelect(_fake_backups(30), selected_id=1000)
    assert len(picker.options) == 25
    page2 = picker._for_page(1)
    assert len(page2.options) == 5
    values = {o.value for o in picker.options} | {o.value for o in page2.options}
    assert values == {str(1000 + i) for i in range(30)}
    # Page-suffixed stable custom_ids.
    assert picker.custom_id != page2.custom_id
    assert page2.custom_id.startswith(picker._base_custom_id)


def test_backup_pick_renders_panel_with_exact_backup_id(monkeypatch):
    rendered: dict[str, Any] = {}

    async def fake_render(interaction, *, selected_id):
        rendered["selected_id"] = selected_id

    monkeypatch.setattr(backups_ui, "_require_setup_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(backups_ui, "_safe_defer_update", AsyncMock())
    monkeypatch.setattr(backups_ui, "_render_panel", fake_render)

    picker = backups_ui.BackupSelect(_fake_backups(30), selected_id=1000)
    interaction = SimpleNamespace(user=SimpleNamespace(id=7))
    run(picker.handle_pick(interaction, "1029"))
    assert rendered["selected_id"] == 1029


def test_restore_mode_choices_unchanged():
    picker = backups_ui.RestoreModeSelect()
    assert [o.value for o in picker.options] == ["all", "missing"]


def test_backup_panel_has_opener_and_no_raw_select():
    view = backups_ui.ServerBackupsView(
        _fake_backups(2),
        selected_id=1000,
    )
    labels = {getattr(c, "label", "") for c in view.children}
    assert "Choose Backup" in labels
    assert not any(isinstance(c, discord.ui.Select) for c in view.children)


# ---------------------------------------------------------------------------
# Config history: >25 paging (versions)
# ---------------------------------------------------------------------------


def test_config_history_select_pages_beyond_25():
    versions = [
        {"version_id": i + 1, "is_manual": False}
        for i in range(30)
    ]
    picker = history.ConfigHistorySelect(versions, 0)
    assert len(picker.options) == 25
    page2 = picker._for_page(1)
    assert len(page2.options) == 5
    values = {o.value for o in picker.options} | {o.value for o in page2.options}
    assert len(values) == 30


# ---------------------------------------------------------------------------
# Modlog: >25 channels page instead of truncating
# ---------------------------------------------------------------------------


def test_modlog_channel_select_pages_beyond_25():
    channels = [
        SimpleNamespace(
            id=5000 + i,
            name=f"chan-{i}",
            mention=f"<#{5000 + i}>",
            category=None,
            guild=SimpleNamespace(me=None),
        )
        for i in range(30)
    ]
    picker = modlog.ModlogChannelSelect(channels)  # type: ignore[arg-type]
    assert len(picker.options) == 25
    page2 = picker._for_page(1)
    assert len(page2.options) == 5
    values = {o.value for o in picker.options} | {o.value for o in page2.options}
    assert values == {str(5000 + i) for i in range(30)}


# ---------------------------------------------------------------------------
# Role center: permission groups page instead of truncating
# ---------------------------------------------------------------------------


def test_permission_group_select_pages_beyond_25():
    parent = SimpleNamespace(owner_id=7, role_id=99)
    groups = [(f"g{i}", f"Group {i}", (f"perm_{i}",)) for i in range(30)]
    picker = role_center.PermissionGroupSelect(parent, groups)  # type: ignore[arg-type]
    assert len(picker.options) == 25
    page2 = picker._for_page(1)
    assert len(page2.options) == 5
    values = {o.value for o in picker.options} | {o.value for o in page2.options}
    assert values == {f"g{i}" for i in range(30)}


# ---------------------------------------------------------------------------
# Verify-reminder recipient picker
# ---------------------------------------------------------------------------


def _fake_preview(candidates: tuple[int, ...]):
    from stoney_verify.verification_reminder_manual import ManualPreview

    return ManualPreview(  # type: ignore[call-arg]
        after=0, next_after=None, scanned=60, pending=60,
        without_access=0, before_automatic_delay=60, manual_limited=0,
        potential=60, candidates=candidates, guild_id=1234,
        channel_id=44, policy_proof="proof", message="Verify",
        eligible_ids=tuple(range(100, 160)),
        eligible_names=tuple(f"Person {i}" for i in range(60)),
        role_id=10,
    )


def test_recipient_picker_defaults_and_stable_id():
    view = reminder_ui.ManualPreviewView(7, _fake_preview(tuple(range(100, 125))))
    picker = view.recipient_selector
    assert isinstance(picker, DankMultiPickerView)
    assert picker.custom_id == "dank:verify_reminders:manual_recipients:picker:v1"
    assert len(picker.options) == 25
    assert all(option.default for option in picker.options)
    labels = {getattr(c, "label", "") for c in view.children if isinstance(c, discord.ui.Button)}
    assert "Choose Recipients" in labels


def test_recipient_pick_stores_exact_ids(monkeypatch):
    view = reminder_ui.ManualPreviewView(7, _fake_preview(tuple(range(100, 125))))
    monkeypatch.setattr(reminder_ui, "_require_staff", AsyncMock(return_value=True))

    sent: list[str] = []

    async def fake_private(interaction, content, **kwargs):
        sent.append(str(content))

    monkeypatch.setattr(reminder_ui, "_private", fake_private)

    async def fake_home(interaction):
        return None

    monkeypatch.setattr(view, "_recipients_home", fake_home)

    interaction = SimpleNamespace(user=SimpleNamespace(id=7), guild=SimpleNamespace(id=1234))
    run(view.recipient_selector.handle_pick(interaction, ["100", "105", "124"]))
    assert view.selected_ids == (100, 105, 124)
    assert any("**3** selected" in text for text in sent)


# ---------------------------------------------------------------------------
# Embed Studio persistent role dropdown (Class C)
# ---------------------------------------------------------------------------


def test_member_role_dropdown_is_framework_persistent_select():
    from stoney_verify.embed_studio_core import new_project

    token = "0123456789abcdef0123456789abcdef"
    project = new_project()
    project["role_mode"] = "dropdown"
    project["role_actions"] = [
        {"role_id": str(100000000000000001 + i), "label": f"Role {i + 1}"}
        for i in range(2)
    ]
    view = studio_roles.member_role_view(project, token)
    assert view is not None
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1
    select = selects[0]
    assert isinstance(select, DankPersistentSelect)
    # Identical persistent contract: same stable custom_id + options.
    assert select.custom_id == studio_roles.role_custom_id(token, "pick")
    assert [o.label for o in select.options] == ["Role 1", "Role 2"]
    assert view.timeout is None


# ---------------------------------------------------------------------------
# FINAL SCAN — nothing raw remains outside the framework
# ---------------------------------------------------------------------------

_RAW_SELECT_NAMES = {"ChannelSelect", "RoleSelect", "UserSelect", "MentionableSelect", "Select"}

# The census's two D-class standing exceptions — surfaces that must
# stay raw because a house View cannot replace them:
# 1. member_role_browser_actions.py — `_timeout_duration_select()`
#    builds a select INSIDE a Modal; a View cannot live in a modal.
# 2. embed_studio_ui.py — the StudioReviewView disabled preview
#    dropdown is a non-interactive replica of a posted menu; it
#    never fires and exists only to render the preview faithfully.
_SCAN_EXCEPTIONS = {
    "stoney_verify/commands_ext/member_role_browser_actions.py",
    "stoney_verify/embed_studio_ui.py",
}


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Attribute):
        return base.attr
    if isinstance(base, ast.Name):
        return base.id
    return ""


def test_final_scan_no_raw_pickers_outside_framework():
    offenders: list[str] = []
    package = ROOT / "stoney_verify"
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "stoney_verify/ui/picker.py":
            continue  # the framework itself owns the raw components
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if _base_name(base) in _RAW_SELECT_NAMES:
                        offenders.append(f"{rel}:{node.lineno} class {node.name}")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in _RAW_SELECT_NAMES:
                    if rel in _SCAN_EXCEPTIONS:
                        continue  # D-class standing exception (see above)
                    offenders.append(f"{rel}:{node.lineno} raw select instantiation")
    assert offenders == [], f"raw pickers remain outside the framework: {offenders}"


def test_final_scan_exceptions_are_exactly_the_two_d_class_sites():
    # Guard the exception list itself: each excepted file must still
    # contain exactly one raw select instantiation (the D-class
    # site) — if a new raw picker appears in an excepted file, this
    # fails instead of silently hiding it.
    for rel in _SCAN_EXCEPTIONS:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _RAW_SELECT_NAMES
        ]
        assert len(calls) == 1, f"{rel} now has {len(calls)} raw select sites"
