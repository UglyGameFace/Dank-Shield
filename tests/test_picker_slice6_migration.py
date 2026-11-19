"""Slice 6 picker migration (Protection & SpamGuard) — routing parity, saved-payload parity, paging, source scan.

Felix's standing rule: every picker uses the house framework
(``stoney_verify/ui/picker.py``). Slice 6 migrated the remaining
Protection Center pickers (AntiNuke trusted-role add/remove, the
SpamGuard response-mode select), all four SpamGuard panel pickers
(section, mode, invite-allowed roles, exempt roles), the Safety
Network pickers (subject user, incident category, original report),
and the Dead Server Clapbacks ping-role picker.

These tests prove, for every migrated surface:
- the raw select classes are gone from the source files and the
  replacements are house-framework classes with stable custom_ids;
- driving a picker routes to the same behavior the old select
  callback had — and, because Protection/SpamGuard settings are
  safety-critical, the exact config patch dict handed to the save
  function is asserted byte-for-byte against what the old callback
  produced for the same pick;
- the one list that can outgrow Discord's 25-option select limit
  (a server's own Safety Network reports, silently truncated to 25
  by the raw select) stays fully reachable through chunked paging.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify import spam_guard as spam
from stoney_verify.commands_ext import public_dead_server_clapbacks as clap
from stoney_verify.commands_ext import public_network_safety as net
from stoney_verify.commands_ext import public_protection_center as protection
from stoney_verify.ui.picker import DankPickerView, DankRoleSelect, DankUserSelect

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
        if args:
            kwargs.setdefault("content", args[0])
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
    def __init__(self, *, guild: Any = None, user: Any = None) -> None:
        self.guild = guild
        self.guild_id = getattr(guild, "id", GUILD_ID) if guild is not None else GUILD_ID
        self.user = user if user is not None else SimpleNamespace(id=OWNER_ID)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = SimpleNamespace(
            id=31337,
            embeds=[discord.Embed(title="Host Panel")],
        )
        self.channel = SimpleNamespace(id=555)
        self.original_edits: list[dict] = []

    async def edit_original_response(self, *args: Any, **kwargs: Any) -> None:
        self.original_edits.append(kwargs)


def fake_role(role_id: int, *, name: str | None = None) -> Any:
    return SimpleNamespace(id=role_id, name=name or f"role{role_id}")


def child_by_id(view: Any, custom_id: str) -> Any:
    for child in view.children:
        if getattr(child, "custom_id", None) == custom_id:
            return child
    raise AssertionError(f"no child with custom_id={custom_id!r} in {view!r}")


# ---------------------------------------------------------------------------
# Source scan — the raw classes are gone, replacements are house classes
# ---------------------------------------------------------------------------

SOURCE_FILES = {
    "stoney_verify/commands_ext/public_protection_center.py": [
        "SpamResponseModeSelect",
    ],
    "stoney_verify/spam_guard.py": [
        "SpamSectionSelect",
        "SpamModeSelect",
    ],
    "stoney_verify/commands_ext/public_network_safety.py": [
        "CategorySelect",
        "OriginalReportSelect",
    ],
}


def test_source_scan_raw_select_classes_are_gone() -> None:
    for rel, banned in SOURCE_FILES.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        for name in banned:
            assert f"class {name}(" not in source, f"{name} still defined in {rel}"
            assert f"class {name} (" not in source, f"{name} still defined in {rel}"


def test_migrated_native_selects_are_house_wrappers() -> None:
    assert issubclass(protection.AntiNukeTrustedRoleAddSelect, DankRoleSelect)
    assert issubclass(protection.AntiNukeTrustedRoleRemoveSelect, DankRoleSelect)
    assert issubclass(spam.SpamInviteAllowedRolesSelect, DankRoleSelect)
    assert issubclass(spam.SpamExemptRolesSelect, DankRoleSelect)
    assert issubclass(net.SubjectUserSelect, DankUserSelect)
    assert issubclass(clap.ClapbackRoleSelect, DankRoleSelect)
    # The house wrappers are still real Discord entity selects.
    assert issubclass(protection.AntiNukeTrustedRoleAddSelect, discord.ui.RoleSelect)
    assert issubclass(net.SubjectUserSelect, discord.ui.UserSelect)


def test_migrated_option_pickers_are_house_picker_views() -> None:
    assert issubclass(protection.SpamResponseModePickerView, DankPickerView)
    assert issubclass(spam.SpamSectionPickerView, DankPickerView)
    assert issubclass(spam.SpamModePickerView, DankPickerView)
    assert issubclass(net.CategoryPickerView, DankPickerView)
    assert issubclass(net._OriginalReportPickerView, DankPickerView)


# ---------------------------------------------------------------------------
# Protection Center — AntiNuke trusted roles (add/remove, multi-pick)
# ---------------------------------------------------------------------------


def _patch_guard(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    async def fake_guard(interaction: Any, action_name: str, action: Any, *, defer: bool = True) -> None:
        captured.setdefault("guard", []).append((action_name, defer))
        await action()

    monkeypatch.setattr(protection, "_guard_protection_action", fake_guard)


def test_antinuke_add_select_keeps_identity_and_routes_all_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    select = protection.AntiNukeTrustedRoleAddSelect()
    assert select.custom_id == "dank_protection:antinuke_trusted_roles_add"
    assert select.placeholder == "Add trusted moderator roles…"
    assert select.min_values == 1
    assert select.max_values == 10

    captured: dict[str, Any] = {}

    async def fake_edit(interaction: Any, *, selected_role_ids: list[int], remove: bool) -> None:
        captured["selected_role_ids"] = selected_role_ids
        captured["remove"] = remove

    monkeypatch.setattr(protection, "_edit_antinuke_trust_manager", fake_edit)
    _patch_guard(monkeypatch, captured)

    select._values = [fake_role(111), fake_role(222), fake_role(333)]
    run(select.callback(FakeInteraction()))

    assert captured["selected_role_ids"] == [111, 222, 333]
    assert captured["remove"] is False
    assert captured["guard"] == [("protection.antinuke.trusted_roles.add", False)]


def test_antinuke_remove_select_keeps_identity_and_routes_all_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    select = protection.AntiNukeTrustedRoleRemoveSelect()
    assert select.custom_id == "dank_protection:antinuke_trusted_roles_remove"
    assert select.placeholder == "Remove trusted roles…"
    assert select.min_values == 1
    assert select.max_values == 10

    captured: dict[str, Any] = {}

    async def fake_edit(interaction: Any, *, selected_role_ids: list[int], remove: bool) -> None:
        captured["selected_role_ids"] = selected_role_ids
        captured["remove"] = remove

    monkeypatch.setattr(protection, "_edit_antinuke_trust_manager", fake_edit)
    _patch_guard(monkeypatch, captured)

    select._values = [fake_role(444)]
    run(select.callback(FakeInteraction()))

    assert captured["selected_role_ids"] == [444]
    assert captured["remove"] is True
    assert captured["guard"] == [("protection.antinuke.trusted_roles.remove", False)]


def test_antinuke_trust_manager_view_hosts_house_selects() -> None:
    view = protection.AntiNukeTrustManagerView()
    add = child_by_id(view, "dank_protection:antinuke_trusted_roles_add")
    remove = child_by_id(view, "dank_protection:antinuke_trusted_roles_remove")
    assert isinstance(add, protection.AntiNukeTrustedRoleAddSelect)
    assert isinstance(remove, protection.AntiNukeTrustedRoleRemoveSelect)


# ---------------------------------------------------------------------------
# Protection Center — Spam Guard response mode (editor)
# ---------------------------------------------------------------------------


def test_spam_editor_opens_response_picker_and_pick_saves_exact_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    spam_cfg = {"mode": "timeout"}
    editor = protection.SpamGuardEditorView(author_id=OWNER_ID, spam=spam_cfg)
    opener = child_by_id(editor, "dank_protection:spam_response_mode:open:v1")
    assert isinstance(opener, protection.SpamResponseModeOpenButton)
    assert opener.label == "Response Action: Timeout User"

    interaction = FakeInteraction(user=SimpleNamespace(id=OWNER_ID))
    run(opener.callback(interaction))
    assert interaction.response.edits, "opener must swap the editor message"
    picker = interaction.response.edits[-1]["view"]
    assert isinstance(picker, protection.SpamResponseModePickerView)
    assert picker.custom_id == "dank_protection:spam_response_mode:v2"
    assert [c.value for c in picker.choices] == [
        "log_only", "delete_only", "timeout", "quarantine", "kick", "ban",
    ]
    assert [c.label for c in picker.choices] == [
        "Alert Only", "Delete Messages", "Timeout User",
        "Quarantine + Restore", "Kick User", "Ban User",
    ]
    assert [c.value for c in picker.choices if c.default] == ["timeout"]

    captured: dict[str, Any] = {}

    async def fake_require(interaction: Any) -> bool:
        return True

    async def fake_save(guild_id: int, patch: dict, member: Any) -> tuple[dict, bool]:
        captured["guild_id"] = guild_id
        captured["patch"] = dict(patch)
        return {"mode": patch["mode"]}, True

    async def fake_refresh(interaction: Any, **kwargs: Any) -> None:
        captured["refresh"] = kwargs

    monkeypatch.setattr(protection, "_require_setup_permission", fake_require)
    monkeypatch.setattr(protection, "_save_spam_settings", fake_save)
    monkeypatch.setattr(protection, "_refresh_panel", fake_refresh)
    _patch_guard(monkeypatch, captured)

    guild = SimpleNamespace(id=GUILD_ID)
    pick_interaction = FakeInteraction(guild=guild, user=SimpleNamespace(id=OWNER_ID))
    run(picker.handle_pick(pick_interaction, "kick"))

    # Saved-payload parity: the old select saved exactly this dict.
    assert captured["patch"] == {"enabled": True, "mode": "kick"}
    assert captured["guild_id"] == GUILD_ID
    assert captured["guard"] == [("protection.spam_response_mode", True)]
    assert "refresh" in captured


def test_spam_response_picker_back_returns_to_editor() -> None:
    picker = protection.SpamResponseModePickerView(author_id=OWNER_ID, spam={"mode": "ban"})
    interaction = FakeInteraction(user=SimpleNamespace(id=OWNER_ID))
    run(picker.on_home(interaction))
    restored = interaction.response.edits[-1]["view"]
    assert isinstance(restored, protection.SpamGuardEditorView)
    assert restored.spam == {"mode": "ban"}


# ---------------------------------------------------------------------------
# SpamGuard panel — persistent view children keep their legacy custom_ids
# ---------------------------------------------------------------------------


def _panel(page: str) -> Any:
    return spam.SpamGuardPanelView.build(page=page, settings=spam._default_settings(0))


def test_spam_panel_children_keep_stable_ids() -> None:
    overview_ids = {getattr(c, "custom_id", None) for c in _panel("overview").children}
    assert "spamguard:overview:section:open" in overview_ids
    assert "spamguard:overview:mode:open" in overview_ids

    access = _panel("access")
    access_ids = {getattr(c, "custom_id", None) for c in access.children}
    assert "spamguard:access:section:open" in access_ids
    invite = child_by_id(access, "spamguard:access:invite_roles")
    exempt = child_by_id(access, "spamguard:access:exempt_roles")
    assert isinstance(invite, spam.SpamInviteAllowedRolesSelect)
    assert isinstance(exempt, spam.SpamExemptRolesSelect)
    assert invite.min_values == 0 and invite.max_values == 10
    assert exempt.min_values == 0 and exempt.max_values == 10


def test_spam_section_picker_routes_to_switch_panel_page(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_switch(interaction: Any, *, page: str) -> None:
        captured["page"] = page

    monkeypatch.setattr(spam, "_switch_panel_page", fake_switch)
    picker = spam.SpamSectionPickerView("overview")
    assert picker.custom_id == "spamguard:overview:section:picker:v1"
    assert [c.value for c in picker.choices] == ["overview", "detection", "enforcement", "access"]
    run(picker.handle_pick(FakeInteraction(), "access"))
    assert captured["page"] == "access"


def test_spam_mode_picker_saves_exact_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_save_rerender(interaction: Any, *, patch: dict, page: str, success_text: str) -> None:
        captured["patch"] = dict(patch)
        captured["page"] = page
        captured["success_text"] = success_text

    monkeypatch.setattr(spam, "_save_patch_and_rerender", fake_save_rerender)
    picker = spam.SpamModePickerView("detection", "timeout")
    assert picker.custom_id == "spamguard:detection:mode:picker:v1"
    assert [c.value for c in picker.choices] == [
        "log_only", "delete_only", "timeout", "quarantine", "kick", "ban",
    ]
    run(picker.handle_pick(FakeInteraction(), "ban"))
    # Saved-payload parity: the old mode select saved exactly this dict.
    assert captured == {
        "patch": {"mode": "ban"},
        "page": "detection",
        "success_text": "Spam guard mode set to `ban`.",
    }


def test_spam_mode_pick_reaches_save_spam_settings_with_exact_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end through the real _save_patch_and_rerender save path."""
    captured: dict[str, Any] = {}

    async def fake_access(interaction: Any) -> bool:
        return True

    async def fake_save(guild_id: int, patch: dict, *, updated_by: Any = None) -> tuple[dict, bool]:
        captured["guild_id"] = guild_id
        captured["patch"] = dict(patch)
        return dict(patch), True

    async def fake_rerender(**kwargs: Any) -> None:
        captured["rerender"] = kwargs

    monkeypatch.setattr(spam, "_ensure_staff_panel_access", fake_access)
    monkeypatch.setattr(spam, "save_spam_settings", fake_save)
    monkeypatch.setattr(spam, "_rerender_panel_message", fake_rerender)

    guild = SimpleNamespace(id=GUILD_ID)
    interaction = FakeInteraction(guild=guild)
    interaction.channel = discord.TextChannel  # placeholder, replaced below
    # _save_patch_and_rerender requires a real TextChannel isinstance check;
    # use a minimal stand-in via object.__new__ to satisfy isinstance.
    channel = object.__new__(discord.TextChannel)
    interaction.channel = channel
    picker = spam.SpamModePickerView("overview", "timeout")
    run(picker.handle_pick(interaction, "quarantine"))
    assert captured["patch"] == {"mode": "quarantine"}
    assert captured["guild_id"] == GUILD_ID


def test_spam_role_selects_save_exact_string_id_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_save_rerender(interaction: Any, *, patch: dict, page: str, success_text: str) -> None:
        captured.setdefault("calls", []).append((dict(patch), page, success_text))

    monkeypatch.setattr(spam, "_save_patch_and_rerender", fake_save_rerender)

    invite = spam.SpamInviteAllowedRolesSelect("access")
    invite._values = [fake_role(111), fake_role(222)]
    run(invite.callback(FakeInteraction()))

    exempt = spam.SpamExemptRolesSelect("access")
    exempt._values = []
    run(exempt.callback(FakeInteraction()))

    # Saved-payload parity: string ids, and an empty pick clears the list.
    assert captured["calls"] == [
        ({"invite_allowed_role_ids": ["111", "222"]}, "access", "Invite-allowed roles updated."),
        ({"exempt_role_ids": []}, "access", "Fully exempt roles updated."),
    ]


def test_spam_openers_gate_on_staff_access(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_access(interaction: Any) -> bool:
        return False

    monkeypatch.setattr(spam, "_ensure_staff_panel_access", fake_access)
    interaction = FakeInteraction()
    run(spam.SpamSectionOpenButton("overview").callback(interaction))
    assert not interaction.response.edits
    run(spam.SpamModeOpenButton("overview", "timeout").callback(interaction))
    assert not interaction.response.edits


# ---------------------------------------------------------------------------
# Safety Network — subject user, category, original reports
# ---------------------------------------------------------------------------


def _net_guild(*, member: Any = None) -> Any:
    return SimpleNamespace(
        id=GUILD_ID,
        owner_id=OWNER_ID,
        get_member=lambda user_id: member,
    )


def test_subject_user_select_routes_to_account_review() -> None:
    select = net.SubjectUserSelect()
    assert isinstance(select, DankUserSelect)
    assert select.custom_id == "dank_network_safety:subject_user:v1"
    assert select.placeholder == "Choose a current member"

    member = SimpleNamespace(id=999)
    interaction = FakeInteraction(guild=_net_guild(member=member))
    select._values = [SimpleNamespace(id=999)]
    run(select.callback(interaction))
    edit = interaction.response.edits[-1]
    assert isinstance(edit["view"], net.SubjectActionsView)
    assert edit["view"].subject == 999


def test_subject_user_select_rejects_departed_member() -> None:
    select = net.SubjectUserSelect()
    interaction = FakeInteraction(guild=_net_guild(member=None))
    select._values = [SimpleNamespace(id=999)]
    run(select.callback(interaction))
    sent = interaction.response.sent[-1]
    assert sent["content"] == "Choose a current member or use the departed-account ID option."


def test_category_picker_opens_event_modal_for_pick() -> None:
    picker = net.CategoryPickerView(999)
    assert picker.custom_id == "dank_network_safety:category:v1"
    assert [c.value for c in picker.choices] == list(net.network.CATEGORIES)
    assert [c.label for c in picker.choices] == [x.title() for x in net.network.CATEGORIES]

    interaction = FakeInteraction(guild=_net_guild())
    run(picker.handle_pick(interaction, "scam"))
    modal = interaction.response.modals[-1]
    assert isinstance(modal, net.NewEventModal)
    assert modal.subject == 999
    assert modal.category == "scam"


def _reports(count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": 1000 + i,
            "category": "spam",
            "occurred_at": "2026-11-01",
            "reason": f"reason {i}",
        }
        for i in range(count)
    ]


def test_original_reports_view_opens_paged_picker() -> None:
    view = net.OriginalReportsView(999, _reports(3))
    opener = child_by_id(view, "dank_network_safety:original_reports:open:v1")
    assert isinstance(opener, net._OriginalReportOpenButton)

    interaction = FakeInteraction(guild=_net_guild())
    run(opener.callback(interaction))
    edit = interaction.response.edits[-1]
    assert isinstance(edit["view"], net._OriginalReportPickerView)


def test_original_report_picker_pages_beyond_25_and_routes_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    reports = _reports(30)
    picker = net._OriginalReportPickerView(999, reports)
    assert picker.pages == 2
    assert len(picker.choices) == 25
    assert picker.custom_id == "dank_network_safety:original_reports:v1:0"

    page2 = picker._for_page(1)
    assert len(page2.choices) == 5
    assert page2.custom_id == "dank_network_safety:original_reports:v1:1"

    # Every report — including the five the raw select truncated away —
    # is reachable across the two pages, with the old label format.
    all_values = [c.value for c in picker.choices] + [c.value for c in page2.choices]
    assert all_values == [str(1000 + i) for i in range(30)]
    first = picker.choices[0]
    assert first.label == "Spam • 2026-11-01"
    assert first.description == "reason 0"

    captured: dict[str, Any] = {}

    async def fake_edit(interaction: Any, view: Any, embed: Any) -> None:
        captured["view"] = view

    monkeypatch.setattr(net, "_edit", fake_edit)
    interaction = FakeInteraction(guild=_net_guild())
    run(page2.handle_pick(interaction, "1029"))
    assert isinstance(captured["view"], net.AmendmentActionsView)
    assert captured["view"].report["id"] == 1029
    assert captured["view"].subject == 999


def test_original_report_picker_single_page_keeps_base_custom_id() -> None:
    picker = net._OriginalReportPickerView(999, _reports(4))
    assert picker.pages == 1
    assert picker.custom_id == "dank_network_safety:original_reports:v1"
    assert len(picker.choices) == 4


# ---------------------------------------------------------------------------
# Dead Server Clapbacks — ping role picker saves the exact config payload
# ---------------------------------------------------------------------------


def test_clapback_role_picker_saves_exact_config_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    view = clap.ClapbackRoleSelectView(OWNER_ID)
    select = next(c for c in view.children if isinstance(c, clap.ClapbackRoleSelect))
    assert isinstance(select, DankRoleSelect)
    assert select.custom_id == "dank_clapbacks:ping_role:v1"
    assert select.placeholder == "Choose the Stoners / sesh ping role"

    captured: dict[str, Any] = {}

    async def fake_upsert(guild_id: int, payload: dict) -> None:
        captured["guild_id"] = guild_id
        captured["payload"] = dict(payload)

    monkeypatch.setattr(clap, "upsert_guild_config", fake_upsert)
    # The Manage Server gate itself is unchanged code (covered by the
    # deny test below); stub it so the fake user can reach the save.
    monkeypatch.setattr(clap, "_manage_clapbacks", lambda interaction: True)

    member = SimpleNamespace(
        id=OWNER_ID,
        guild_permissions=SimpleNamespace(administrator=True, manage_guild=True),
    )
    guild = SimpleNamespace(id=GUILD_ID)
    interaction = FakeInteraction(guild=guild, user=member)
    # Drive the framework wrapper's callback, as Discord would. The
    # wrapper isinstance-checks discord.Role, so build a bare one.
    role = object.__new__(discord.Role)
    role.id = 555
    role.name = "Stoners"
    select._values = [role]
    run(select.callback(interaction))

    # Saved-payload parity: the old raw select wrote exactly this dict.
    assert captured == {
        "guild_id": GUILD_ID,
        "payload": {
            clap.PING_ROLE_KEY: "555",
            "__config_write_mode": "explicit_override",
            "__config_write_source": "dead_server_clapbacks_panel",
        },
    }
    sent = interaction.response.sent[-1]
    assert "Ping role set to **@Stoners**" in sent["content"]


def test_clapback_role_picker_denies_without_manage_server(monkeypatch: pytest.MonkeyPatch) -> None:
    select = clap.ClapbackRoleSelect(OWNER_ID)

    async def fake_upsert(guild_id: int, payload: dict) -> None:  # pragma: no cover
        raise AssertionError("must not save")

    monkeypatch.setattr(clap, "upsert_guild_config", fake_upsert)
    member = SimpleNamespace(
        id=OWNER_ID,
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )
    interaction = FakeInteraction(guild=SimpleNamespace(id=GUILD_ID), user=member)
    run(select._save_role(interaction, fake_role(555)))
    sent = interaction.response.sent[-1]
    assert sent["content"] == "❌ Dead Server Clapbacks require **Manage Server**."
