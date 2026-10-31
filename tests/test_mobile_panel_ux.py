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
