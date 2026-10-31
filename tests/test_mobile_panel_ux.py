from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.commands_ext import public_protection_center as protection
from stoney_verify.commands_ext import member_command_center, public_ticket_command_center
from stoney_verify.commands_ext import public_verify_command_center


def test_every_protection_action_remains_in_a_short_section():
    groups = protection._PROTECTION_SECTION_BUTTONS
    mapped = [item for actions in groups.values() for item in actions]
    assert len(mapped) == len(set(mapped))
    assert len(mapped) == 19
    assert {key for key, _, _ in protection._PROTECTION_SECTIONS} == set(groups)
    for section, labels in groups.items():
        view = protection.ProtectionCenterView(author_id=123, cfg={}, spam={}, section=section)
        child_ids = {str(getattr(item, "custom_id", "")) for item in view.children}
        assert child_ids == {
            "dank_protection:section:v2",
            "dank_protection:refresh",
            "dank_protection:close",
            *(f"dank_protection:{label}" for label in labels),
        }
        assert len(view.children) <= 9
        assert all(getattr(item, "row", 0) <= 4 for item in view.children)
        assert len(child_ids) == len(view.children)


def test_protection_home_and_sections_are_compact_and_keep_live_data(monkeypatch):
    monkeypatch.setattr(
        protection.anti_nuke_service,
        "antinuke_permission_health",
        lambda *_args, **_kwargs: [],
    )
    guild = SimpleNamespace(me=None)
    for section, _, _ in protection._PROTECTION_SECTIONS:
        embed = protection._protection_embed(
            guild, {}, {"enabled": False, "mode": "timeout"}, "loaded",
            section=section,
        )
        assert section != "overview" or not embed.fields
        assert len(embed.fields) <= 2
        assert len(embed.description or "") <= 600
        assert all(len(field.value) <= 1024 for field in embed.fields)
        assert "Protection" in embed.title
        assert "What buttons do" not in str(embed.to_dict())
        assert "Quick recommendation" not in str(embed.to_dict())
    overview = protection._protection_embed(guild, {}, {}, "loaded")
    assert "Automod" in overview.description
    assert "AntiNuke" in overview.description


def test_protection_section_picker_switches_without_mutating(monkeypatch):
    events = []

    async def allowed(_interaction):
        return True

    async def refresh(_interaction, *, section=None, **_kwargs):
        events.append(section)

    monkeypatch.setattr(protection, "_require_setup_permission", allowed)
    monkeypatch.setattr(protection, "_refresh_panel", refresh)
    picker = protection._ProtectionSectionSelect("overview")
    picker._values = ["antinuke"]
    asyncio.run(picker.callback(SimpleNamespace(user=SimpleNamespace(id=1))))
    assert events == ["antinuke"]


def test_degraded_section_disables_all_actions_except_retry_and_close():
    for section in protection._PROTECTION_SECTION_BUTTONS:
        view = protection.ProtectionCenterView(
            author_id=1, cfg={}, spam={}, section=section, degraded=True,
        )
        active = {
            str(getattr(item, "custom_id", ""))
            for item in view.children
            if not item.disabled
        }
        assert active == {"dank_protection:refresh", "dank_protection:close"}


def test_core_entry_screens_explain_next_action_not_repeat_commands():
    for build in (
        member_command_center._center_embed,
        public_verify_command_center._center_embed,
    ):
        embed = build()
        assert not embed.fields
        assert len(embed.description) <= 280
        assert "choose" in embed.description.lower()
    ticket = public_ticket_command_center._ticket_center_embed(None)
    assert len(ticket.fields) == 0
    assert len(ticket.description) <= 280


def test_no_laundry_list_on_reworked_core_screen_sources():
    root = Path(__file__).resolve().parents[1] / "stoney_verify" / "commands_ext"
    sources = (
        "public_protection_center.py",
        "member_command_center.py",
        "public_verify_command_center.py",
        "public_ticket_command_center.py",
        "public_design_studio_v2.py",
        "public_server_stats.py",
    )
    for name in sources:
        assert (root / name).is_file()
    protection_source = (root / sources[0]).read_text()
    assert "class _ProtectionSectionSelect" in protection_source
    assert "self.clear_items()" in protection_source
    assert "self.add_item(_ProtectionSectionSelect(self.section))" in protection_source
    # Direct callbacks still belong to the original source, with stable IDs.
    assert 'custom_id="dank_protection:antinuke_restore_member"' in protection_source
    assert 'custom_id="dank_protection:import_pack"' in protection_source


def test_member_setup_every_admin_action_is_visible_in_exactly_one_section():
    from stoney_verify.commands_ext import public_member_setup as setup
    labels = [name for page in setup._MEMBER_SETUP_SECTIONS.values() for name in page]
    assert len(labels) == len(set(labels)) == 15
    for key, names in setup._MEMBER_SETUP_SECTIONS.items():
        view = setup.MemberSetupAdminView(123, section=key)
        buttons = [
            item.label for item in view.children
            if isinstance(item, __import__("discord").ui.Button)
        ]
        assert set(buttons) == set(names) | {"Refresh", "Close"}
        assert len(view.children) <= 9
        assert all((getattr(item, "row", 0) or 0) < 5 for item in view.children)
    assert setup.MemberSetupAdminView(123).section == "roles"


def test_community_hub_every_home_control_is_kept_in_one_section():
    from stoney_verify.commands_ext import public_community_hub as hub
    keys = [name for values in hub._COMMUNITY_HOME_SECTIONS.values() for name in values]
    assert len(keys) == len(set(keys)) == 14
    for section, names in hub._COMMUNITY_HOME_SECTIONS.items():
        view = hub.CommunityHubView(123, section=section)
        buttons = [
            item.label for item in view.children
            if isinstance(item, __import__("discord").ui.Button)
        ]
        assert set(buttons) == set(names) | {"Back to Dank Home", "Close"}
        assert len(view.children) <= 9
        embed = hub._hub_embed(section=section)
        assert len(embed.fields) == 0
        assert len(embed.description) < 380


def test_community_hub_settings_are_short_and_complete():
    from stoney_verify.commands_ext import public_community_hub as hub
    labels = [name for page in hub._HUB_SETTINGS_GROUPS.values() for name in page]
    assert len(labels) == len(set(labels)) == 15
    fake_bot = SimpleNamespace(intents=SimpleNamespace(presences=False))
    for section, names in hub._HUB_SETTINGS_GROUPS.items():
        view = hub.HubSettingsView(123, {}, section=section)
        buttons = [
            item.label for item in view.children
            if isinstance(item, __import__("discord").ui.Button)
        ]
        assert set(buttons) == set(names) | {"Staff Dashboard"}
        assert len(view.children) <= 9
        embed = hub._settings_embed({}, fake_bot, section=section)
        assert len(embed.fields) <= 2
        assert len(embed.description) < 220
        assert all(len(field.value) <= 1024 for field in embed.fields)


def test_community_pings_manager_keeps_every_control_without_wall_of_buttons():
    from stoney_verify.commands_ext import public_community_pings as pings
    labels = [name for page in pings._PINGS_MANAGER_SECTIONS.values() for name in page]
    assert len(labels) == len(set(labels)) == 12
    for section, names in pings._PINGS_MANAGER_SECTIONS.items():
        view = pings.CommunityPingsManagerView(123, section=section)
        buttons = [
            item.label for item in view.children
            if isinstance(item, __import__("discord").ui.Button)
        ]
        assert set(buttons) == set(names) | {"Refresh", "Home", "Close"}
        assert len(view.children) <= 9
        assert max((item.row or 0) for item in view.children) < 5


def test_all_section_selectors_are_read_only_navigation(monkeypatch):
    from stoney_verify.commands_ext import public_community_hub as hub
    from stoney_verify.commands_ext import public_member_setup as setup

    events = []

    async def allowed(*_args, **_kwargs):
        return True

    async def pending(_interaction):
        return None

    async def load_setup(*_args, **_kwargs):
        return {}

    async def replace(_interaction, **kwargs):
        events.append(kwargs["view"])

    monkeypatch.setattr(setup, "_staff_authorized", allowed)
    monkeypatch.setattr(setup, "_defer_panel_update", pending)
    monkeypatch.setattr(setup, "load_guild_setup_state", load_setup)
    monkeypatch.setattr(setup, "_admin_embed", lambda *_args: "summary")
    monkeypatch.setattr(setup, "_replace", replace)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        guild_id=77, guild=SimpleNamespace(id=77),
    )
    select = setup._MemberSetupSectionSelect("roles")
    select._values = ["gate"]
    asyncio.run(select.callback(interaction))
    assert isinstance(events[-1], setup.MemberSetupAdminView)
    assert events[-1].section == "gate"


def test_ticket_operations_sections_keep_all_actions_and_history():
    from stoney_verify.commands_ext import public_ticket_command_center as tickets
    keys = [name for items in tickets._TICKET_OPERATIONS_SECTIONS.values() for name in items]
    assert len(keys) == len(set(keys)) == 11
    for section, names in tickets._TICKET_OPERATIONS_SECTIONS.items():
        view = tickets.TicketOperationsView(123, section=section)
        button_labels = [item.label for item in view.children if isinstance(item, __import__("discord").ui.Button)]
        assert set(button_labels) == set(names)
        assert len(view.children) <= 9
        assert max((item.row or 0) for item in view.children) < 5
        assert sum(isinstance(item, tickets.TicketHistoryUserSelect) for item in view.children) == (1 if section == "queues" else 0)
        assert len(tickets._operations_embed(section=section).fields) == 0


def test_ticket_section_navigation_does_not_execute_actions(monkeypatch):
    from stoney_verify.commands_ext import public_ticket_command_center as tickets
    events = []

    async def allowed(_): return True
    async def replace(_, **kwargs): events.append(kwargs)
    monkeypatch.setattr(tickets, "_require_staff", allowed)
    monkeypatch.setattr(tickets, "_replace", replace)
    select = tickets.TicketOperationsSectionSelect(123, "queues")
    select._values = ["setup"]
    asyncio.run(select.callback(SimpleNamespace(user=SimpleNamespace(id=123))))
    assert isinstance(events[0]["view"], tickets.TicketOperationsView)
    assert events[0]["view"].section == "setup"


def test_roles_and_profiles_sections_preserve_permission_gated_controls():
    from stoney_verify.commands_ext import public_role_center as roles
    groups = [name for values in roles._ROLES_PROFILES_SECTIONS.values() for name in values]
    assert len(groups) == len(set(groups)) == 11
    for section, names in roles._ROLES_PROFILES_SECTIONS.items():
        view = roles.RolesProfilesView(
            123, section=section, staff=True, role_manager=True, setup_manager=True,
        )
        actual = {
            item.label for item in view.children
            if isinstance(item, __import__("discord").ui.Button)
        }
        expected = set(names) | {"Dank Shield Home"}
        if section == "profile":
            expected.discard("My Community & Pings")  # staff use manager entry instead
        assert actual == expected
        assert len(view.children) <= 6
        assert len(roles._center_embed(
            section=section, staff=True, role_manager=True, setup_manager=True,
        ).fields) == 0
    member = roles.RolesProfilesView(
        123, staff=False, role_manager=False, setup_manager=False, section="roles",
    )
    assert member.section == "profile"
    assert {item.value for item in member.children[0].options} == {"profile"}
    assert all(item.label not in {"Server Role Editor", "Create Role", "Role Health"}
               for item in member.children if getattr(item, "label", None))
