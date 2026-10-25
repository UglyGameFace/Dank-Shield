"""Embed Studio private forms, publish routing, response privacy and validation."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from io import StringIO
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.embed_studio_core import (
    StudioValidationError,new_project,validate_project,validate_studio_form,
    trim_published_history,
)
from stoney_verify import embed_studio_forms as forms
from stoney_verify.embed_studio_ui import (
    StudioHomeView,StudioSession,StudioFormBuilderMenu,StudioFormBasicsModal,
    StudioFormQuestionModal,StudioFormQuestionMenu,StudioFormInboxView,StudioReviewView,
)
from stoney_verify.embed_studio_schedules import StudioScheduleError,safe_scheduled_project


def _project():
    p=new_project()
    p["form"]={
        "title":"Suggestion Box","button_label":"Send Feedback",
        "questions":[
            {"label":"What can we improve?","placeholder":"Tell us",
             "style":"paragraph","required":True},
            {"label":"Contact me?","placeholder":"yes/no",
             "style":"short","required":False},
        ],
    }
    return p


def test_forms_default_off_until_storage_v2_enabled(monkeypatch):
    monkeypatch.setattr(forms,"storage_v2_enabled",lambda env:env.get("V2")=="yes")
    assert not forms.forms_enabled({})
    assert not forms.forms_enabled({forms.FLAG:"true"})
    assert forms.forms_enabled({forms.FLAG:"true","V2":"yes"})


def test_validated_form_enforces_discord_components_and_real_input_limits():
    project=validate_project(_project())
    assert project["form"]["title"]=="Suggestion Box"
    assert len(project["form"]["questions"])==2
    assert validate_project(new_project())["form"] is None
    for label in ("", "X"*46):
        p=_project();p["form"]["questions"][0]["label"]=label
        with pytest.raises(StudioValidationError):
            validate_project(p)
    p=_project()
    p["form"]["questions"].extend(deepcopy(p["form"]["questions"][:1])*4)
    with pytest.raises(StudioValidationError):
        validate_project(p)
    p=_project()
    p["form"]["questions"][1]["label"]=p["form"]["questions"][0]["label"]
    with pytest.raises(StudioValidationError):
        validate_project(p)
    p=_project()
    p["links"]=[{"label":f"Site {i}","url":"https://example.com"} for i in range(5)]
    with pytest.raises(StudioValidationError,match="four URL"):
        validate_project(p)
    p=_project()
    p["role_actions"]=[{"label":"Role","role_id":"123456789012345678"}]
    with pytest.raises(StudioValidationError,match="self-role"):
        validate_project(p)


def test_private_form_view_creates_exact_bot_persistent_token_and_url_buttons():
    p=_project()
    p["links"]=[{"label":"Help","url":"https://example.com/help"}]
    token="a"*32
    view=forms.form_view(p,token)
    assert len(view.children)==2
    assert any(isinstance(c,discord.ui.Button)
               and c.custom_id==forms.PREFIX+token for c in view.children)
    assert any(isinstance(c,discord.ui.Button)
               and c.url=="https://example.com/help" for c in view.children)
    for invalid in ("", "A"*32, "x"*32):
        with pytest.raises(forms.StudioFormError):
            forms.form_view(p,invalid)
    with pytest.raises(forms.StudioFormError,match="question"):
        p["form"]["questions"]=[]
        forms.form_view(p,token)


def test_modal_questions_are_bounded_and_clean_answers():
    p=_project()
    valid=validate_project(p)["form"]
    modal=forms.StudioResponseModal(
        guild_id=1,channel_id=2,message_id=3,actor_id=4,
        token="a"*32,form=valid,
    )
    assert len(modal.children)==2
    answers=forms.clean_answers(valid,["Hello there",""])
    assert answers[0]["question"]=="What can we improve?"
    assert answers[1]["answer"]==""
    with pytest.raises(forms.StudioFormError):
        forms.clean_answers(valid,[""])
    with pytest.raises(forms.StudioFormError):
        forms.clean_answers(valid,["","extra"])
    with pytest.raises(forms.StudioFormError):
        forms.clean_answers(valid,["x"*1001,"yes"])


def test_studio_ui_forms_are_in_private_editor_and_preview_disabled():
    session=StudioSession(guild_id=1,owner_id=2)
    menu=StudioHomeView(session)
    assert "Add Buttons" in {getattr(x,"label","") for x in menu.children}
    from stoney_verify.embed_studio_ui import StudioButtonTools
    assert "Collect Answers" in {getattr(x,"label","") for x in StudioButtonTools(session).children}
    assert max(sum(1 for x in menu.children if x.row==row) for row in range(5))<=5
    session.project=validate_project(_project())
    assert len(StudioFormBasicsModal(session).children)==2
    assert len(StudioFormQuestionModal(session).children)==4
    assert len(StudioFormQuestionMenu(session).children)>0
    assert len(StudioFormBuilderMenu(session).children)<=6
    review=StudioReviewView(session)
    form_buttons=[c for c in review.children if getattr(c,"label",None)=="Send Feedback"]
    assert len(form_buttons)==1 and form_buttons[0].disabled
    with pytest.raises(StudioScheduleError):
        safe_scheduled_project(session.project)


def test_form_actions_are_protected_against_eviction():
    records={str(i):{"form":_project()["form"]} for i in range(21)}
    with pytest.raises(StudioValidationError,match="role panels or forms"):
        trim_published_history(records,max_posts=20)
    records["20"]={"form":None}
    result=trim_published_history(records,max_posts=20)
    assert len(result)==20 and "20" not in result


def test_response_storage_is_private_and_window_deduplicated(monkeypatch):
    monkeypatch.setattr(forms,"forms_enabled",lambda:True)
    values=[]
    class Table:
        def insert(self,row):
            values.append(row)
            return self
        def execute(self):return SimpleNamespace(data=[{}])
    monkeypatch.setattr(forms,"_client",lambda:SimpleNamespace(table=lambda *_:Table()))
    answers=[{"question":"Q","answer":"A"}]
    asyncio.run(forms.store_answers(1,2,3,4,answers,now=600))
    assert values[0]["guild_id"]=="1"
    assert values[0]["window_slot"]==2
    assert values[0]["answers"]==answers


def test_form_runtime_has_one_listener_and_sql_private_retention():
    from pathlib import Path
    script=Path("stoney_verify/commands.py").read_text()
    assert "install_embed_studio_form_runtime(bot)" in script
    sql=Path("supabase_fresh/migrations/202610200032_dankshield_embed_studio_forms.sql").read_text().lower()
    for clause in ("enable row level security","revoke all on public.dank_embed_studio_form_responses",
                   "unique(guild_id,message_id,user_id,window_slot)",
                   "interval '30 days'","for update skip locked",
                   "grant execute on function public.dank_embed_studio_purge_form_responses"):
        assert clause in sql
    assert "security definer" not in sql
