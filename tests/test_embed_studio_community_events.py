"""Discord community event presets and mobile detail editor regressions."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import discord
import pytest

from stoney_verify import embed_studio_ui as ui
from stoney_verify.embed_studio_core import StudioValidationError, render_embeds, validate_project
from stoney_verify.embed_studio_presets import PRESETS, PRESET_DESCRIPTIONS, project_for_preset


EVENTS = {
    "movie_night", "watch_party", "trivia", "karaoke",
    "hangout", "giveaway", "ama", "art_contest",
}


@pytest.mark.parametrize("key", sorted(EVENTS))
def test_every_discord_event_layout_is_distinct_safe_and_editable(key):
    assert key in dict(PRESETS)
    assert PRESET_DESCRIPTIONS[key]
    project = project_for_preset(key)
    assert project == validate_project(project)
    assert project["links"] == [] and project["role_actions"] == []
    assert project["form"] is None
    assert project["content"] == ""
    assert len(project["embeds"]) == 1
    e = render_embeds(project)[0]
    assert e.title and e.description and e.footer.text
    assert e.author.name
    assert len(e.fields) >= 4
    assert all(field.value and field.name for field in e.fields)
    assert any(field.name == "📅 WHEN" for field in e.fields)
    assert any(field.name == "📍 WHERE" for field in e.fields)
    assert any(field.name == "✅ HOW TO JOIN" for field in e.fields)
    assert len(e) < 6000
    assert "Add date, time and timezone." in {f.value for f in e.fields}
    assert project_for_preset(key) == project  # Deterministic, no live lookup.


def test_event_picker_remains_under_discord_25_options():
    assert len(PRESETS) == 20
    assert len({key for key, _name in PRESETS}) == len(PRESETS)
    assert len(PRESETS) <= 25
    options = ui.StarterSelect(ui.StudioSession(guild_id=111, owner_id=222)).options
    assert len(options) == len(PRESETS)
    assert EVENTS <= {x.value for x in options}
    assert all(x.label and x.description and len(x.description) <= 100 for x in options)
    assert len({project_for_preset(key)["embeds"][0]["title"] for key in EVENTS}) == len(EVENTS)
    assert len({project_for_preset(key)["embeds"][0]["color"] for key in EVENTS}) >= 6


def test_event_details_form_is_visible_in_look_and_images():
    session = ui.StudioSession(guild_id=111, owner_id=222)
    controls = ui.StudioDesignTools(session).children
    labels = {item.label for item in controls if isinstance(item, discord.ui.Button)}
    assert {"Event Details", "Game Server Details", "Colors & Style", "Add Pictures"} <= labels
    assert all(item.row in range(5) for item in controls)
    assert max(sum(1 for item in controls if item.row == row) for row in range(5)) <= 5


@pytest.mark.parametrize("key", sorted(EVENTS))
def test_quick_event_details_replace_existing_fields_without_duplication(key):
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.project = project_for_preset(key)
    original = deepcopy(session.project)
    session.apply(lambda p: ui._update_event_details_fields(p, 0, {
        "when": "Friday 8 PM ET",
        "where": "#movie-night",
        "topic": "Selected title or confirmed contest details",
        "join": "Join the channel by 7:55 PM.",
    }))
    fields = session.project["embeds"][0]["fields"]
    assert len(fields) == len(original["embeds"][0]["fields"])
    assert {x["value"] for x in fields} >= {
        "Friday 8 PM ET", "#movie-night", "Selected title or confirmed contest details",
        "Join the channel by 7:55 PM.",
    }
    updated = deepcopy(session.project)
    # Submitting again cannot accumulate duplicate details or change unrelated copy.
    session.apply(lambda p: ui._update_event_details_fields(p, 0, {
        "when": "Saturday 9 PM ET", "where": "", "topic": "", "join": "",
    }))
    assert len(session.project["embeds"][0]["fields"]) == len(fields)
    assert session.project["embeds"][0]["description"] == original["embeds"][0]["description"]
    assert session.project["embeds"][0]["fields"][0]["value"] == "Saturday 9 PM ET"
    assert session.undo()
    assert session.project == updated


def test_event_details_support_legacy_community_event_fields():
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.project = project_for_preset("event")
    session.apply(lambda p: ui._update_event_details_fields(p, 0, {
        "when": "Sunday 6 PM", "where": "#events",
        "topic": "An art showcase", "join": "Send your art in #art",
    }))
    fields = session.project["embeds"][0]["fields"]
    names = {x["name"] for x in fields}
    assert len(fields) == 4
    assert "When" in names and "Where" in names and "How to join" in names
    assert "✨ EVENT DETAILS" in names


def test_invalid_event_details_fail_closed_and_leave_draft_unchanged():
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.project = project_for_preset("movie_night")
    original = deepcopy(session.project)
    with pytest.raises(StudioValidationError):
        session.apply(lambda p: ui._update_event_details_fields(p, 0, {
            "when": "a" * 300, "topic": "", "where": "", "join": "",
        }))
    assert session.project == original
    assert not session.history


def test_event_modal_loads_real_values_and_never_invents_a_date():
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.project = project_for_preset("movie_night")
    form = ui.StudioEventDetailsModal(session)
    assert len(form.children) == 4
    assert all(not x.required for x in form.children)
    assert form.when.default == "Add date, time and timezone."
    assert form.where.default == "Discord voice/text channel or event location."
    session.apply(lambda p: ui._update_event_details_fields(p, 0, {
        "when": "Dec 18, 8:00 PM EST", "where": "#movie-night",
        "topic": "An announced family-friendly movie", "join": "RSVP in #events",
    }))
    refreshed = ui.StudioEventDetailsModal(session)
    assert refreshed.when.default == "Dec 18, 8:00 PM EST"
    assert refreshed.where.default == "#movie-night"
    assert refreshed.topic.default == "An announced family-friendly movie"
    assert refreshed.join.default == "RSVP in #events"


def test_quick_event_modal_preserves_long_existing_custom_copy():
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.project = project_for_preset("giveaway")
    # Simulate content authored earlier in the full Extra Text Sections editor.
    # The compact Event Details modal is intentionally limited to 700 chars.
    session.apply(lambda p: p["embeds"][0]["fields"][2].update(value="x" * 900))
    form = ui.StudioEventDetailsModal(session)
    assert not form.topic.default
    assert session.project["embeds"][0]["fields"][2]["value"] == "x" * 900


def test_layout_selection_is_private_and_undoable(monkeypatch):
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.apply(lambda p: p["embeds"][0].update(title="My draft"))
    old = deepcopy(session.project)
    session.post_key = "111:555"
    events = []

    async def fake_menu(*_args, **kwargs):
        events.append(kwargs)
    monkeypatch.setattr(ui, "_menu", fake_menu)
    picker = ui.StarterSelect(session)
    picker._values = ["movie_night"]
    asyncio.run(picker.callback(object()))
    assert session.project == old
    assert session.post_key == "111:555"
    assert isinstance(events[0]["view"], ui.ConfirmStarterLayout)
