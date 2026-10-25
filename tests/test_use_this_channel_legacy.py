"""Coverage of older single-purpose and multi-slot channel setup screens."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.commands_ext import public_setup_picker as setup
from stoney_verify.roles_center_services import RolesCenterView
from stoney_verify.commands_ext import public_setup_recommend as guided
from stoney_verify.commands_ext import public_live_captions as captions


def test_setup_legacy_wizard_has_unambiguous_text_shortcuts_and_no_auto_save():
    cfg=SimpleNamespace()
    cases=[
        (setup.TicketPicker,"transcripts_channel_id"),
        (setup.VerifyPicker,"verify_channel_id"),
        (setup.VCPicker,"vc_verify_queue_channel_id"),
        (setup.LogPicker,None),
    ]
    for kind,key in cases:
        view=kind(owner_id=7,cfg=cfg)
        buttons=[item for item in view.children if isinstance(item,discord.ui.Button)]
        assert "Use This Channel" in [item.label for item in buttons]
        assert len(view.children) <= 25
        assert max(sum(1 for item in view.children if item.row==row) for row in range(5))<=5
        if key:
            assert key in view.state


def test_multi_channel_wizard_asks_which_log_field(monkeypatch):
    parent=setup.LogPicker(owner_id=7,cfg=SimpleNamespace())
    menu=setup._CurrentLogTargetMenu(parent)
    labels={getattr(item,"label","") for item in menu.children}
    assert {"Mod Logs Here","Security Logs Here","Join & Leave Logs Here",
            "Verification Logs Here","Back"} <= labels
    assert len(menu.children)==5
    names=[x.key for x in menu.children if isinstance(x,setup._UseCurrentChannelForLogButton)]
    assert names==["modlog_channel_id","raidlog_channel_id","join_log_channel_id",
                   "force_verify_log_channel_id"]


def test_wizard_stages_only_chosen_field_no_implicit_save(monkeypatch):
    picker=setup.VerifyPicker(owner_id=7,cfg=SimpleNamespace())
    channel=SimpleNamespace(id=121,mention="<#121>")
    calls=[]
    async def owner(_interaction):return True
    async def edit(_interaction,**payload):
        calls.append(payload)
    monkeypatch.setattr(picker,"interaction_check",owner)
    monkeypatch.setattr(setup,"current_text_channel",lambda _interaction,**kwargs:channel)
    monkeypatch.setattr(setup,"_edit_or_send",edit)
    asyncio.run(setup._stage_current_setup_channel(
        SimpleNamespace(guild=SimpleNamespace(
            id=1,
            get_channel=lambda cid:channel if cid==channel.id else None,
            get_role=lambda rid:None,
        )),
        picker,"verify_channel_id"
    ))
    assert picker.state["verify_channel_id"]=="121"
    assert len(calls)==1 and calls[0]["view"] is picker
    # Public setup writer is intentionally not invoked by a channel shortcut.
    assert "upsert_guild_config" not in setup._stage_current_setup_channel.__code__.co_names


def test_roles_center_buttons_have_explicit_destination_kind():
    view=RolesCenterView()
    labels={getattr(item,"label","") for item in view.children}
    assert "Use This Channel: Pronouns" in labels
    assert "Use This Channel: Identity" in labels
    assert sum(isinstance(item,discord.ui.ChannelSelect) for item in view.children)==2
    assert len(view.children)==4


def test_live_captions_uses_original_output_pick_callback():
    view=captions.CaptionOutputPickerView(owner_id=7)
    assert "Use This Channel" in {
        getattr(item,"label","") for item in view.children
    }
    assert callable(view._save_output_channel)
    assert any(isinstance(item,discord.ui.ChannelSelect) for item in view.children)
    assert len(view.children) <= 5


def test_guided_setup_only_offers_current_shortcut_for_text_requirements(monkeypatch):
    cls=guided.GuidedOneItemView
    source=Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text()
    assert 'if kind == "text":' in source
    assert "GuidedUseCurrentChannelButton(" in source
    assert "_guided_save_existing_item(interaction, self.requirement_key, channel)" in source
    assert "current_text_channel(interaction, require_bot_send=False)" in source
    assert "_guided_step_is_current(" in source


def test_no_unsafe_current_target_for_category_or_voice_picker():
    for path in (
        "stoney_verify/commands_ext/public_live_captions.py",
        "stoney_verify/commands_ext/public_setup_recommend.py",
    ):
        contents=Path(path).read_text()
        assert "Use This Channel" in contents
    source=Path("stoney_verify/commands_ext/public_live_captions.py").read_text()
    only_voice=source.split("class CaptionVoiceRulePickerView",1)[1].split(
        "class CaptionLanguageOutputSelect",1
    )[0]
    assert "Use This Channel" not in only_voice


def test_migration_does_not_add_slash_commands_or_database_columns():
    for file in ("stoney_verify/commands_ext/public_setup_picker.py",
                 "stoney_verify/commands_ext/public_setup_recommend.py",
                 "stoney_verify/commands_ext/public_live_captions.py"):
        content=Path(file).read_text()
        assert "current_text_channel" in content
    roles=Path("stoney_verify/roles_center_services.py").read_text()
    assert "await _post_default_panel(interaction, channel, kind=" in roles
