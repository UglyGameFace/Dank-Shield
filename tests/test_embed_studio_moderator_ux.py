"""Post Builder accessibility: beginner flow, grouped navigation and draft protection."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import discord

from stoney_verify import embed_studio_ui as ui
from stoney_verify.embed_studio_core import new_project
from stoney_verify.embed_studio_presets import PRESETS


def _labels(view) -> set[str]:
    return {getattr(child,"label","") for child in view.children}


def test_home_is_a_9_action_guided_post_instead_of_20_technical_buttons():
    session=ui.StudioSession(guild_id=111,owner_id=222)
    home=ui.StudioHomeView(session)
    buttons=[item for item in home.children if isinstance(item,discord.ui.Button)]
    assert len(buttons)==9
    assert len([item for item in home.children if isinstance(item,discord.ui.ChannelSelect)])==1
    assert max(sum(1 for item in home.children if item.row==row) for row in range(5))<=5
    assert {"Pick a Layout","Write Message","Look & Images","Add Buttons",
            "Saved Designs","More Tools","Edit Existing Post","Undo",
            "Preview & Post"} == {item.label for item in buttons}
    assert "JSON Files" not in _labels(home)
    guide=ui._summary(session)
    assert "four easy steps" in guide.description.lower()
    assert "Nothing goes public until you confirm" in guide.description
    assert "Choose a channel first." in "
".join(str(field.value) for field in guide.fields)


def test_all_existing_features_remain_accessible_through_task_groups():
    session=ui.StudioSession(guild_id=111,owner_id=222)
    groups=[
        ui.StudioDesignTools(session),
        ui.StudioButtonTools(session),
        ui.StudioSavedTools(session),
        ui.StudioAdvancedTools(session),
    ]
    expected=[
        {"Colors & Style","Add Pictures","Saved Pictures","Reuse Server Style"},
        {"Website Buttons","Let Members Pick Roles","Collect Answers"},
        {"Saved Post Layouts","Restore My Draft","Share With Staff","Import / Export"},
        {"Extra Text Sections","Multiple Post Cards","Automatic Server Details",
         "Upcoming Scheduled Posts"},
    ]
    for view,labels in zip(groups,expected):
        assert labels <= _labels(view)
        assert "Back to My Post" in _labels(view)
        assert max(sum(1 for c in view.children if c.row==row) for row in range(5))<=5


def test_layout_choice_asks_before_replacing_existing_draft(monkeypatch):
    session=ui.StudioSession(guild_id=111,owner_id=222)
    session.apply(lambda p:p["embeds"][0].update(title="Important announcement"))
    snapshot=deepcopy(session.project)
    actions=[]
    async def fake_menu(*args,**kwargs):
        actions.append(kwargs)
    monkeypatch.setattr(ui,"_menu",fake_menu)
    select=ui.StarterSelect(session)
    select._values=[PRESETS[0][0]]
    asyncio.run(select.callback(object()))
    assert session.project==snapshot
    assert isinstance(actions[0]["view"],ui.ConfirmStarterLayout)
    assert {"Use New Layout","Keep My Draft"} <= _labels(actions[0]["view"])
    assert session.post_key==""


def test_layout_confirmation_preserves_undo_and_discards_old_edit_target(monkeypatch):
    session=ui.StudioSession(guild_id=111,owner_id=222)
    session.apply(lambda p:p["embeds"][0].update(title="Old message"))
    original=deepcopy(session.project)
    session.post_key="111:555"
    view=ui.ConfirmStarterLayout(session,PRESETS[0][0])
    home=[]
    async def fake_home(*args,**kwargs):
        home.append(kwargs)
    monkeypatch.setattr(ui,"_home",fake_home)
    asyncio.run(view.confirm(object(),None))
    assert len(home)==1
    assert session.post_key==""
    assert session.project!=original
    assert session.undo()
    assert session.project==original


def test_stale_layout_confirmation_cannot_destroy_newer_draft(monkeypatch):
    session=ui.StudioSession(guild_id=111,owner_id=222)
    session.apply(lambda p:p["embeds"][0].update(title="Original title"))
    view=ui.ConfirmStarterLayout(session,PRESETS[0][0])
    session.apply(lambda p:p["embeds"][0].update(title="Updated in other menu"))
    current=deepcopy(session.project)
    feedback=[]
    async def fake_say(*args,**kwargs):
        feedback.append(args[1])
    monkeypatch.setattr(ui,"_say",fake_say)
    asyncio.run(view.confirm(object(),None))
    assert session.project==current
    assert len(feedback)==1 and "draft changed" in feedback[0].lower()


def test_post_confirmation_uses_clear_words_for_both_modes():
    new=ui.StudioSession(guild_id=111,owner_id=222)
    posted=ui.StudioSession(guild_id=111,owner_id=222,post_key="111:555")
    assert "Post Now" in _labels(ui.StudioReviewView(new))
    assert "Save Changes" in _labels(ui.StudioReviewView(posted))
    assert "Publish / Save Edit" not in _labels(ui.StudioReviewView(new))
