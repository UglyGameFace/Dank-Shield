"""Slice 5 picker migration (members & moderation) — routing parity, paging, source scan.

Felix's standing rule: every picker uses the house framework
(``stoney_verify/ui/picker.py``). Slice 5 migrated the member and
moderation surfaces: Member Command Center, Role Member Browser
(roster / actions / bulk / bulk confirmation), Mod Command Center,
member connection controls (protected channels), and the Member
Activity Review selects in the members group.

These tests prove, for every migrated surface:
- the raw select classes are gone from the source files;
- host panels construct with house-framework pickers / opener buttons
  carrying stable semantic custom_ids;
- driving a picker routes to the same behavior the old select
  callback had (same destinations, modals, and saved values);
- lists longer than Discord's 25-option select limit stay fully
  reachable through chunked paging — including the protected-channel
  list the raw select silently truncated;
- the Member Activity Review family keeps the PR #874 contract:
  every control id stays in the ``dank:members_review:`` namespace
  (the lifecycle suite pins the full contract unmodified).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import member_command_center as center
from stoney_verify.commands_ext import member_connection_controls_ui as conn
from stoney_verify.commands_ext import member_role_browser_actions as actions
from stoney_verify.commands_ext import member_role_browser_bulk as bulk
from stoney_verify.commands_ext import member_role_browser_bulk_role_confirmation as confirm
from stoney_verify.commands_ext import member_role_browser_roster as roster
from stoney_verify.commands_ext import public_members_group as pmg
from stoney_verify.commands_ext import public_mod_command_center as mod
from stoney_verify.members_new.activity_service import (
    InactiveMemberCandidate,
    InactiveScanOptions,
    InactiveScanReport,
    ScanLockRecord,
)
from stoney_verify.ui.picker import (
    DankMultiPickerView,
    DankPickerView,
    DankRoleSelect,
    DankUserSelect,
)

ROOT = Path(__file__).resolve().parents[1]
OWNER_ID = 424242
GUILD_ID = 777


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.modals: list[Any] = []
        self.deferred = False

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        self.sent.append(kwargs)
        self.done = True

    async def edit_message(self, *args: Any, **kwargs: Any) -> None:
        self.edits.append(kwargs)
        self.done = True

    async def defer(self, *args: Any, **kwargs: Any) -> None:
        self.deferred = True
        self.done = True

    async def send_modal(self, modal: Any) -> None:
        self.modals.append(modal)
        self.done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, *args: Any, **kwargs: Any) -> None:
        self.sent.append(kwargs)


class FakeInteraction:
    def __init__(self, *, guild: Any = None, user_id: int = OWNER_ID) -> None:
        self.guild = guild
        self.guild_id = getattr(guild, "id", GUILD_ID) if guild is not None else GUILD_ID
        self.user = SimpleNamespace(id=user_id)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = SimpleNamespace(embeds=[discord.Embed(title="Host Panel")])
        self.original_edits: list[dict] = []

    async def edit_original_response(self, *args: Any, **kwargs: Any) -> None:
        self.original_edits.append(kwargs)


def fake_member(user_id: int, *, name: str | None = None) -> Any:
    return SimpleNamespace(
        id=user_id,
        name=name or f"user{user_id}",
        display_name=name or f"user{user_id}",
        global_name="",
        bot=False,
        joined_at=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        is_timed_out=lambda: False,
        mention=f"<@{user_id}>",
        roles=[],
    )


def fake_role(role_id: int, members: Any = ()) -> Any:
    return SimpleNamespace(
        id=role_id,
        name=f"role{role_id}",
        mention=f"<@&{role_id}>",
        members=list(members),
        is_default=lambda: False,
    )


def fake_guild(*, members: Any = None, channels: Any = None, roles: Any = None) -> Any:
    member_map = {int(m.id): m for m in (members or [])}
    channel_map = {int(c.id): c for c in (channels or [])}
    role_map = {int(r.id): r for r in (roles or [])}
    return SimpleNamespace(
        id=GUILD_ID,
        get_member=lambda uid: member_map.get(int(uid)),
        get_channel=lambda cid: channel_map.get(int(cid)),
        get_role=lambda rid: role_map.get(int(rid)),
    )


def child_by_custom_id(view: Any, custom_id: str) -> Any:
    return next(item for item in view.children if getattr(item, "custom_id", None) == custom_id)


# ---------------------------------------------------------------------------
# Source scan: the migrated raw classes are gone
# ---------------------------------------------------------------------------

MIGRATED_FILES = {
    "stoney_verify/commands_ext/member_command_center.py": [
        "CenterRoleSelect", "DirectMemberSelect", "CleanupMemberSelect",
        "ActivityLookupMemberSelect", "IntelligenceMemberSelect",
        "OwnerAffiliationUserSelect",
    ],
    "stoney_verify/commands_ext/member_role_browser_actions.py": ["MemberRoleSelect"],
    "stoney_verify/commands_ext/member_role_browser_bulk.py": ["BulkRoleSelect"],
    "stoney_verify/commands_ext/member_role_browser_bulk_role_confirmation.py": ["ConfirmedBulkRoleSelect"],
    "stoney_verify/commands_ext/member_role_browser_roster.py": ["BrowserRoleSelect"],
    "stoney_verify/commands_ext/public_mod_command_center.py": ["ModMemberSelect"],
}

REMOVED_CLASSES = {
    "stoney_verify/commands_ext/member_command_center.py": [
        "OwnerAffiliationChoiceSelect", "RecentBanConnectionSelect",
    ],
    "stoney_verify/commands_ext/member_role_browser_bulk.py": ["BulkMemberSelect"],
    "stoney_verify/commands_ext/member_role_browser_roster.py": [
        "MemberRosterSelect", "BrowserSortSelect", "BrowserFilterSelect",
    ],
    "stoney_verify/commands_ext/member_connection_controls_ui.py": ["ProtectedChannelSelect"],
    "stoney_verify/commands_ext/public_members_group.py": ["MemberSelect", "LockedUserSelect"],
}

RAW_BASES = ("(discord.ui.Select)", "(discord.ui.RoleSelect)", "(discord.ui.UserSelect)", "(discord.ui.ChannelSelect)")


def test_source_scan_raw_picker_classes_are_gone() -> None:
    for rel, class_names in MIGRATED_FILES.items():
        source = (ROOT / rel).read_text()
        for base in RAW_BASES:
            assert base not in source, f"{rel} still defines a raw {base} picker"
        for name in class_names:
            assert f"class {name}(Dank" in source, f"{rel}: {name} is not on the house framework"
    for rel, class_names in REMOVED_CLASSES.items():
        source = (ROOT / rel).read_text()
        for base in RAW_BASES:
            assert base not in source, f"{rel} still defines a raw {base} picker"
        for name in class_names:
            assert f"class {name}(" not in source, f"{rel}: raw {name} still defined"


# ---------------------------------------------------------------------------
# Member Command Center — native pickers on the framework wrappers
# ---------------------------------------------------------------------------


def test_center_native_pickers_are_house_wrappers_with_stable_ids() -> None:
    view = center.LiveMembersMenuView(OWNER_ID)
    role_select = child_by_custom_id(view, "dank_members_center:role")
    member_select = child_by_custom_id(view, "dank_members_center:direct_member")
    assert isinstance(role_select, DankRoleSelect)
    assert isinstance(role_select, center.CenterRoleSelect)
    assert isinstance(member_select, DankUserSelect)
    assert isinstance(member_select, center.DirectMemberSelect)

    cleanup_view = center.ActivityCleanupMenuView(OWNER_ID)
    lookup = child_by_custom_id(cleanup_view, "dank_members_center:activity_lookup_member")
    cleanup = child_by_custom_id(cleanup_view, "dank_members_center:cleanup_member")
    assert isinstance(lookup, center.ActivityLookupMemberSelect)
    assert isinstance(cleanup, center.CleanupMemberSelect)
    assert isinstance(lookup, DankUserSelect) and isinstance(cleanup, DankUserSelect)

    intel_view = center.IntelligenceMenuView(OWNER_ID)
    intel = child_by_custom_id(intel_view, "dank_members_center:intelligence_member")
    assert isinstance(intel, center.IntelligenceMemberSelect)
    assert isinstance(intel, DankUserSelect)


def test_direct_member_pick_routes_to_rest_verified_open(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []

    async def fake_require_review(interaction: Any) -> bool:
        return True

    async def fake_resolve(guild: Any, member_id: int) -> Any:
        events.append(("resolve", member_id))
        return None, "unavailable"

    async def fake_reply(interaction: Any, text: str) -> None:
        events.append(("reply", text))

    monkeypatch.setattr(center, "require_review", fake_require_review)
    monkeypatch.setattr(center, "reply_ephemeral", fake_reply)
    import stoney_verify.commands_ext.public_mod_command_center as mod_module
    monkeypatch.setattr(mod_module, "resolve_current_mod_member", fake_resolve)

    view = center.LiveMembersMenuView(OWNER_ID)
    picker = child_by_custom_id(view, "dank_members_center:direct_member")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.on_pick(interaction, SimpleNamespace(id=31337)))
    assert ("resolve", 31337) in events
    assert any("reply" == kind for kind, *_ in events)


def test_cleanup_pick_routes_through_bounded_precheck(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []

    async def fake_resolve(guild: Any, member_id: int) -> Any:
        events.append(("resolve", member_id))
        return None, "unavailable"

    async def fake_reply(interaction: Any, text: str) -> None:
        events.append(("reply", text))

    monkeypatch.setattr(center, "reply_ephemeral", fake_reply)
    import stoney_verify.commands_ext.public_mod_command_center as mod_module
    monkeypatch.setattr(mod_module, "resolve_current_mod_member", fake_resolve)

    view = center.ActivityCleanupMenuView(OWNER_ID)
    picker = child_by_custom_id(view, "dank_members_center:cleanup_member")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.on_pick(interaction, SimpleNamespace(id=2222)))
    assert ("resolve", 2222) in events
    assert any(kind == "reply" for kind, *_ in events)


def test_activity_lookup_pick_shows_stored_history(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []

    async def fake_resolve(guild: Any, user_id: int) -> Any:
        return None, "absent"

    async def fake_history(interaction: Any, user_id: int, **kwargs: Any) -> None:
        events.append((user_id, kwargs.get("presence")))

    monkeypatch.setattr(center, "_resolve_intelligence_member", fake_resolve)
    monkeypatch.setattr(center, "_show_stored_member_history", fake_history)

    view = center.ActivityCleanupMenuView(OWNER_ID)
    picker = child_by_custom_id(view, "dank_members_center:activity_lookup_member")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.on_pick(interaction, SimpleNamespace(id=5555)))
    assert events == [(5555, "absent")]


def test_intelligence_pick_keeps_single_panel_resolver_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[Any] = []

    async def fake_require_review(interaction: Any) -> bool:
        return True

    async def fake_resolve(guild: Any, user_id: int) -> Any:
        return None, "absent"

    async def fake_history(interaction: Any, user_id: int, **kwargs: Any) -> None:
        events.append((user_id, kwargs.get("presence"), type(kwargs.get("view")).__name__))

    monkeypatch.setattr(center, "require_review", fake_require_review)
    monkeypatch.setattr(center, "_resolve_intelligence_member", fake_resolve)
    monkeypatch.setattr(center, "_show_stored_member_history", fake_history)

    view = center.IntelligenceMenuView(OWNER_ID)
    picker = child_by_custom_id(view, "dank_members_center:intelligence_member")
    picker._values = [SimpleNamespace(id=6666)]
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.callback(interaction))
    assert events == [(6666, "absent", "HistoricalIntelligenceTargetView")]


# ---------------------------------------------------------------------------
# Member Command Center — affiliation + recent-ban house pickers
# ---------------------------------------------------------------------------


def test_affiliation_choice_picker_values_and_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    view = center.OwnerAffiliationChoiceView(OWNER_ID, 9999)
    opener = child_by_custom_id(view, "dank_members_center:affiliation_choice:open:v1")
    assert getattr(opener, "label", None) == "Choose relationship review"
    assert len(view.children) == 2

    picker = view._choice_picker()
    assert isinstance(picker, DankPickerView)
    assert {choice.value for choice in picker._all_choices} == {"known", "possible", "cleared"}

    replaced: list[Any] = []

    async def fake_replace(interaction: Any, *, embed: Any, view: Any) -> None:
        replaced.append(view)

    monkeypatch.setattr(center, "_replace_panel", fake_replace)
    owner_interaction = FakeInteraction(user_id=OWNER_ID)
    owner_interaction.guild = SimpleNamespace(owner_id=OWNER_ID)
    owner_interaction.user = SimpleNamespace(id=OWNER_ID)
    run(view._pick_choice(owner_interaction, "known"))
    assert replaced and isinstance(replaced[0], center.OwnerAffiliationTargetView)
    assert replaced[0].mode == "known"
    assert replaced[0].target_id == 9999


def test_affiliation_member_pick_opens_evidence_modal() -> None:
    target_view = center.OwnerAffiliationTargetView(OWNER_ID, 9999, "known")
    picker = next(
        item for item in target_view.children
        if isinstance(item, center.OwnerAffiliationUserSelect)
    )
    assert isinstance(picker, DankUserSelect)
    assert picker.custom_id == "dank_members_center:affiliation_member"
    assert len(target_view.children) == 3

    interaction = FakeInteraction(user_id=OWNER_ID)
    interaction.guild = SimpleNamespace(owner_id=OWNER_ID)
    interaction.user = SimpleNamespace(id=OWNER_ID)
    run(picker.on_pick(interaction, SimpleNamespace(id=7777)))
    assert len(interaction.response.modals) == 1
    modal = interaction.response.modals[0]
    assert isinstance(modal, center.OwnerAffiliationModal)
    assert str(modal.other_account.default) == "7777"


class _NamedUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id

    def __str__(self) -> str:
        return f"banned{self.id}"


def test_recent_ban_picker_pages_past_25(monkeypatch: pytest.MonkeyPatch) -> None:
    bans = [SimpleNamespace(user=_NamedUser(1000 + i)) for i in range(30)]
    view = center.RecentBanConnectionView(OWNER_ID, bans)
    opener = child_by_custom_id(view, "dank_members_center:recent_bans:open:v1")
    assert getattr(opener, "label", "").startswith("Choose a banned account")

    picker = view._ban_picker()
    assert isinstance(picker, DankPickerView)
    assert picker.pages == 2
    assert len(picker._all_choices) == 30
    assert len(picker.choices) == 25
    page_two = picker._for_page(1)
    assert len(page_two.choices) == 5
    assert page_two.children[0].custom_id == "dank_members_center:recent_bans:v1:1"
    assert {c.value for c in picker._all_choices} == {str(1000 + i) for i in range(30)}

    events: list[Any] = []

    async def fake_require_review(interaction: Any) -> bool:
        return True

    async def fake_report(interaction: Any, owner_id: int, target_id: int) -> None:
        events.append(target_id)

    monkeypatch.setattr(center, "require_review", fake_require_review)
    monkeypatch.setattr(center, "_show_connection_report", fake_report)
    interaction = FakeInteraction(guild=fake_guild())
    run(view._pick_ban(interaction, "1029"))
    assert events == [1029]


# ---------------------------------------------------------------------------
# Role Member Browser — roster pickers
# ---------------------------------------------------------------------------


def _browser(member_count: int = 30) -> Any:
    members = [fake_member(2000 + i) for i in range(member_count)]
    role = fake_role(42, members)
    guild = fake_guild(members=members)
    return roster.RoleMemberBrowserView(
        owner_id=OWNER_ID, guild=guild, role=role,  # type: ignore[arg-type]
    )


def test_roster_panel_uses_opener_buttons_with_stable_ids() -> None:
    browser = _browser()
    ids = {getattr(item, "custom_id", None) for item in browser.children}
    assert "dank_members_browser:member" in ids
    assert "dank_members_browser:sort" in ids
    assert "dank_members_browser:filter" in ids
    assert not any(isinstance(item, discord.ui.Select) for item in browser.children)


def test_roster_member_picker_pages_all_filtered_members() -> None:
    browser = _browser(30)
    picker = browser._member_picker()
    assert isinstance(picker, DankPickerView)
    assert picker.pages == 2
    assert len(picker._all_choices) == 30
    assert len(picker._for_page(1).choices) == 5
    assert picker.children[0].custom_id == "dank_members_browser:member_pick:0"


def test_roster_sort_and_filter_picks_update_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = _browser()
    monkeypatch.setattr(roster, "role_browser_embed", lambda view: discord.Embed(title="Browser"))

    interaction = FakeInteraction(guild=browser.guild)
    run(browser._sort_picker().handle_pick(interaction, "joined_newest"))
    assert browser.sort_mode == "joined_newest"
    assert browser.page == 0
    assert interaction.response.edits and interaction.response.edits[-1]["view"] is browser

    interaction = FakeInteraction(guild=browser.guild)
    run(browser._filter_picker().handle_pick(interaction, "bots"))
    assert browser.filter_mode == "bots"
    assert interaction.response.edits[-1]["view"] is browser


def test_roster_member_pick_missing_member_keeps_wording(monkeypatch: pytest.MonkeyPatch) -> None:
    browser = _browser()
    replies: list[str] = []

    async def fake_reply(interaction: Any, text: str) -> None:
        replies.append(text)

    monkeypatch.setattr(roster, "reply_ephemeral", fake_reply)
    interaction = FakeInteraction(guild=fake_guild())
    run(browser._pick_roster_member(interaction, "999999"))
    assert replies == ["\u274c That member is no longer in the server. Refresh the roster."]


def test_browser_role_select_is_house_wrapper() -> None:
    home = roster.MemberBrowserHomeView(OWNER_ID)
    picker = child_by_custom_id(home, "dank_members_browser:role")
    assert isinstance(picker, roster.BrowserRoleSelect)
    assert isinstance(picker, DankRoleSelect)


# ---------------------------------------------------------------------------
# Role Member Browser — actions / bulk / confirmation role pickers
# ---------------------------------------------------------------------------


def test_member_role_select_is_house_wrapper_with_action_id() -> None:
    parent = SimpleNamespace(owner_id=OWNER_ID)
    action_view = SimpleNamespace(owner_id=OWNER_ID, parent=parent, action="add_role")
    picker = actions.MemberRoleSelect(action_view)  # type: ignore[arg-type]
    assert isinstance(picker, DankRoleSelect)
    assert picker.custom_id == "dank_members_browser:add_role"
    assert picker.parent_view is action_view


def test_bulk_select_view_is_house_multi_picker() -> None:
    browser = _browser(5)
    view = bulk.BulkSelectView(browser)
    assert isinstance(view, DankMultiPickerView)
    select = view.children[0]
    assert select.custom_id == "dank_members_browser:bulk_select"
    assert len(view.choices) == 5
    labels = {getattr(item, "label", None) for item in view.children}
    assert "Back" in labels


def _real_member(user_id: int) -> Any:
    member = object.__new__(discord.Member)
    member._user = SimpleNamespace(id=user_id, name=f"user{user_id}", global_name=None)
    member.nick = None
    return member


def test_bulk_member_pick_routes_to_confirmation_panel() -> None:
    browser = _browser(3)
    view = bulk.BulkSelectView(browser)
    members = browser.page_members()
    real_members = [_real_member(int(m.id)) for m in members]
    interaction = FakeInteraction(guild=fake_guild(members=real_members))
    run(view._pick_members(interaction, [str(members[0].id), str(members[1].id)]))
    assert interaction.response.edits
    target_view = interaction.response.edits[-1]["view"]
    assert isinstance(target_view, bulk.BulkActionView)
    assert len(target_view.members) == 2
    assert "Selected **2** member(s)" in interaction.response.edits[-1]["embed"].description


def test_bulk_role_select_is_house_wrapper() -> None:
    parent = SimpleNamespace(owner_id=OWNER_ID, action="remove_role")
    picker = bulk.BulkRoleSelect(parent)  # type: ignore[arg-type]
    assert isinstance(picker, DankRoleSelect)
    # member_role_browser_bulk_role_confirmation replaces bulk.BulkRoleSelect
    # with ConfirmedBulkRoleSelect at import time (pre-existing production
    # behavior), so accept either variant's stable id.
    assert picker.custom_id.startswith("dank_members_browser:bulk_")
    assert picker.custom_id.endswith("_remove_role")


def test_confirmed_bulk_role_pick_opens_confirmation_modal() -> None:
    bulk_parent = SimpleNamespace(owner_id=OWNER_ID, members=[fake_member(1), fake_member(2)])
    parent = SimpleNamespace(owner_id=OWNER_ID, action="add_role", parent=bulk_parent)
    picker = confirm.ConfirmedBulkRoleSelect(parent)  # type: ignore[arg-type]
    assert isinstance(picker, DankRoleSelect)
    assert picker.custom_id == "dank_members_browser:bulk_confirmed_add_role"

    role = object.__new__(discord.Role)
    role.id = 606
    role.name = "Muted"
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.on_pick(interaction, role))
    assert len(interaction.response.modals) == 1
    modal = interaction.response.modals[0]
    assert isinstance(modal, confirm.ConfirmedBulkRoleModal)
    assert modal.role_id == 606


def test_confirmed_installer_swaps_bulk_role_select() -> None:
    assert confirm.install_confirmed_bulk_role_actions() is True
    assert bulk.BulkRoleSelect is confirm.ConfirmedBulkRoleSelect


# ---------------------------------------------------------------------------
# Mod Command Center
# ---------------------------------------------------------------------------


def test_mod_member_select_is_house_wrapper_and_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    view = mod.ModerationCenterView(OWNER_ID)
    picker = child_by_custom_id(view, "dank:mod:center:member:v1")
    assert isinstance(picker, mod.ModMemberSelect)
    assert isinstance(picker, DankUserSelect)

    events: list[Any] = []

    async def fake_staff(interaction: Any) -> bool:
        return True

    async def fake_open(interaction: Any, member_id: int, **kwargs: Any) -> None:
        events.append((member_id, kwargs["replace_original"], kwargs["browser"]))

    monkeypatch.setattr(mod, "_require_staff", fake_staff)
    monkeypatch.setattr(mod, "open_member_actions_by_id", fake_open)
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.on_pick(interaction, SimpleNamespace(id=1531117218291646605)))
    assert events == [(1531117218291646605, True, view)]


# ---------------------------------------------------------------------------
# Connection controls — protected channel picker no longer truncates
# ---------------------------------------------------------------------------


def test_protected_channel_picker_reaches_all_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn, "channel_protection_status", lambda guild, cid, entry: "active")
    channels = [
        SimpleNamespace(id=2000 + i, name=f"chan-{i}", mention=f"<#{2000 + i}>")
        for i in range(30)
    ]
    guild = fake_guild(channels=channels)
    entries = {str(2000 + i): {"role_id": "3000"} for i in range(30)}

    view = conn.RemoveProtectionView(OWNER_ID, guild, entries)  # type: ignore[arg-type]
    opener = child_by_custom_id(view, "dank:connection_controls:protected_channel:open:v1")
    assert getattr(opener, "label", "").startswith("Choose a protected channel")

    picker = conn._protected_channel_picker(OWNER_ID, guild, entries)  # type: ignore[arg-type]
    assert isinstance(picker, DankPickerView)
    assert picker.pages == 2
    assert len(picker._all_choices) == 30
    assert {c.value for c in picker._all_choices} == set(entries)
    assert picker._for_page(1).children[0].custom_id == (
        "dank:connection_controls:protected_channel:v1:1"
    )


def test_protected_channel_pick_routes_to_restore_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(conn, "channel_protection_status", lambda guild, cid, entry: "active")

    async def fake_authorized(interaction: Any) -> bool:
        return True

    async def fake_load(guild_id: int) -> Any:
        return None, {"protected": {"2001": {"role_id": "3000"}}}

    shown: list[Any] = []

    async def fake_display(interaction: Any, embed: Any, view: Any) -> None:
        shown.append(view)

    monkeypatch.setattr(conn, "_authorized", fake_authorized)
    monkeypatch.setattr(conn, "load_controls", fake_load)
    monkeypatch.setattr(conn, "_display", fake_display)

    channel = SimpleNamespace(id=2001, name="chan-1", mention="<#2001>")
    role = SimpleNamespace(id=3000, mention="<@&3000>")
    guild = fake_guild(channels=[channel], roles=[role])
    interaction = FakeInteraction(guild=guild)
    run(conn._review_protected_channel(interaction, OWNER_ID, "2001"))
    assert shown and isinstance(shown[0], conn.ConfirmRestoreView)
    assert shown[0].channel_id == "2001"


# ---------------------------------------------------------------------------
# Members group — Member Activity Review pickers (PR #874 namespace)
# ---------------------------------------------------------------------------


def _report(n_candidates: int = 30) -> InactiveScanReport:
    now = datetime(2026, 11, 19, 4, 0, tzinfo=timezone.utc)
    candidates = [
        InactiveMemberCandidate(
            user_id=1000 + i,
            display_name=f"user{i}",
            mention=f"<@{1000 + i}>",
            joined_at=now,
            last_seen_at=now,
            inactivity_days=120,
            activity_score=0,
            confidence="high",
            status="Purge-safe" if i % 2 == 0 else "Needs review",
            removable=(i % 2 == 0),
            protected=False,
            cannot_remove=False,
            reasons=["No tracked activity"],
        )
        for i in range(n_candidates)
    ]
    return InactiveScanReport(
        guild_id=GUILD_ID,
        scanned_at=now,
        options=InactiveScanOptions(),
        total_members_seen=100,
        candidates=candidates,
        protected=[],
        cannot_remove=[],
        data_warnings=[],
    )


def test_review_picker_pages_all_candidates_in_namespace() -> None:
    view = pmg.MemberActivityReviewView(_report(30), page=0)
    opener = child_by_custom_id(view, f"{pmg.MEMBER_REVIEW_CUSTOM_ID_PREFIX}select")
    assert getattr(opener, "label", None) == "Select a user to inspect manually"

    picker = pmg._member_inspect_picker(view)
    assert isinstance(picker, DankPickerView)
    assert picker.pages == 2
    assert len(picker._all_choices) == 30
    assert picker.children[0].custom_id == f"{pmg.MEMBER_REVIEW_CUSTOM_ID_PREFIX}inspect:0"
    assert picker._for_page(1).children[0].custom_id == f"{pmg.MEMBER_REVIEW_CUSTOM_ID_PREFIX}inspect:1"
    assert [c.value for c in picker._all_choices] == [str(1000 + i) for i in range(30)]


def test_review_pick_routes_to_user_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report(3)
    view = pmg.MemberActivityReviewView(report, page=0)

    async def fake_require(interaction: Any) -> bool:
        return True

    async def fake_locked(guild_id: int, user_id: int) -> bool:
        return False

    monkeypatch.setattr(pmg, "_require_review_permission", fake_require)
    monkeypatch.setattr(pmg, "is_scan_user_locked", fake_locked)

    interaction = FakeInteraction(guild=fake_guild())
    run(pmg._inspect_scanned_member(view, interaction, "1001"))
    assert interaction.response.sent
    detail_view = interaction.response.sent[-1]["view"]
    assert isinstance(detail_view, pmg.UserDetailLockView)


def test_locked_picker_pages_all_records_in_namespace() -> None:
    records = [
        ScanLockRecord(guild_id=GUILD_ID, user_id=2000 + i)
        for i in range(30)
    ]
    view = pmg.LockedUsersReviewView(records, "test", guild=None, scan_report=_report(1), page=0)
    opener = child_by_custom_id(view, f"{pmg.MEMBER_REVIEW_CUSTOM_ID_PREFIX}locked_select")
    assert getattr(opener, "label", None) == "Select a locked user to review"

    picker = pmg._locked_user_picker(view)
    assert isinstance(picker, DankPickerView)
    assert picker.pages == 2
    assert len(picker._all_choices) == 30
    assert picker.children[0].custom_id.startswith(pmg.MEMBER_REVIEW_CUSTOM_ID_PREFIX)


def test_locked_pick_marks_selection_for_confirm() -> None:
    records = [ScanLockRecord(guild_id=GUILD_ID, user_id=2000 + i) for i in range(3)]
    view = pmg.LockedUsersReviewView(records, "test", guild=None, scan_report=_report(1), page=0)

    async def fake_require(interaction: Any) -> bool:
        return True

    import stoney_verify.commands_ext.public_members_group as pmg_module
    original = pmg_module._require_review_permission
    pmg_module._require_review_permission = fake_require  # type: ignore[assignment]
    try:
        interaction = FakeInteraction(guild=fake_guild())
        run(pmg._review_locked_user(view, interaction, "2002"))
    finally:
        pmg_module._require_review_permission = original  # type: ignore[assignment]
    assert view.selected_user_id == 2002
    assert view.confirm_unlock_ready is True
    assert interaction.response.edits  # picker message confirms the update
