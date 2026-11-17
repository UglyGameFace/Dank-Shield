"""Post Builder: fast Discord ACK, usable gaming presets and private editing."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import discord
import pytest

from stoney_verify import embed_studio_ui as ui
from stoney_verify.embed_studio_core import StudioValidationError, render_embeds, validate_project
from stoney_verify.embed_studio_presets import PRESETS, PRESET_DESCRIPTIONS, project_for_preset


GAMING = {
    "minecraft", "modded", "game_night",
    "tournament", "server_update", "recruitment",
}


def test_game_presets_are_distinct_editable_and_discord_safe():
    assert GAMING <= {key for key, _name in PRESETS}
    assert len(PRESETS) == len({key for key, _name in PRESETS}) == len(PRESET_DESCRIPTIONS)
    colors = set()
    titles = set()
    for key in GAMING:
        project = project_for_preset(key)
        embed = render_embeds(project)[0]
        colors.add(embed.color.value)
        titles.add(embed.title)
        assert embed.fields, f"{key} should give players useful join/event details"
        assert embed.footer.text
        assert project["content"] == ""
        assert project["links"] == []  # No invented server or invite URLs.
        assert all(field.name and field.value for field in embed.fields)
        assert len(embed) <= 6000
        assert validate_project(project) == project
    assert len(colors) >= 5
    assert len(titles) == len(GAMING)
    assert "SERVER ADDRESS" in " ".join(f.name for f in render_embeds(project_for_preset("minecraft"))[0].fields)


def test_mobile_layout_picker_explains_each_choice_and_preserves_existing_options():
    session = ui.StudioSession(guild_id=111, owner_id=222)
    choices = ui.StarterSelect(session).options
    assert len(choices) == 20
    assert {opt.value for opt in choices} == {key for key, _ in PRESETS}
    assert all(opt.description and len(opt.description) <= 100 for opt in choices)
    assert all(len(opt.label) <= 100 for opt in choices)


@pytest.mark.parametrize("key", GAMING)
def test_new_layout_replacement_keeps_undo_and_does_not_publish(key):
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.apply(lambda project: project["embeds"][0].update(title="private work"))
    old = deepcopy(session.project)
    # Same canonical model used by the real one-tap layout picker.
    session.history.append(deepcopy(session.project))
    session.project = project_for_preset(key)
    session.post_key = ""
    assert session.project != old
    assert session.undo()
    assert session.project == old


def test_game_details_update_only_known_fields_without_duplication():
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.project = project_for_preset("minecraft")
    session.apply(lambda p: ui._update_game_server_fields(p, 0, {
        "address": "23.109.125.116:20229",
        "platforms": "Java and Bedrock",
        "features": "Cobblemon; crossplay via Geyser / Floodgate",
        "join": "Add the server in Multiplayer and enter the address.",
    }))
    fields = session.project["embeds"][0]["fields"]
    values = {x["name"]: x["value"] for x in fields}
    assert values["🌍 SERVER ADDRESS"] == "23.109.125.116:20229"
    assert values["🎮 SUPPORTED EDITIONS"] == "Java and Bedrock"
    assert "Cobblemon" in values["🧩 WHAT'S INSIDE"]
    assert "Multiplayer" in values["🚪 START PLAYING"]
    assert len(fields) == 4
    session.apply(lambda p: ui._update_game_server_fields(p, 0, {
        "address": "play.example.org:25565", "features": "", "platforms": "", "join": "",
    }))
    assert len(session.project["embeds"][0]["fields"]) == 4
    assert session.project["embeds"][0]["fields"][0]["value"] == "play.example.org:25565"


def test_game_details_preserve_custom_copy_and_fail_closed_on_invalid_size():
    session = ui.StudioSession(guild_id=111, owner_id=222)
    session.project = project_for_preset("game_night")
    session.apply(lambda p: ui._update_game_server_fields(p, 0, {
        "address": "games.example.net:25565",
        "platforms": "Android and PC",
        "features": "Minecraft night",
        "join": "See #events",
    }))
    current = deepcopy(session.project)
    assert any(field["name"] == "📅 WHEN" for field in current["embeds"][0]["fields"])
    assert "GAME / MODE" in " ".join(field["name"] for field in current["embeds"][0]["fields"])
    with pytest.raises(StudioValidationError):
        session.apply(lambda p: ui._update_game_server_fields(p, 0, {
            "address": "x" * 2000,
        }))
    assert session.project == current
    assert session.undo()


class _Response:
    def __init__(self, events, *, done=False):
        self.events = events
        self.done = done

    def is_done(self):
        return self.done

    async def defer(self, **kwargs):
        assert not self.done, "Discord interaction may only be acknowledged once"
        self.events.append(("ack", kwargs))
        self.done = True


@pytest.mark.parametrize("route", ["menu", "home"])
def test_post_builder_acknowledges_before_autosave_or_render(monkeypatch, route):
    events = []
    async def fake_autosave(interaction, session):
        assert interaction.response.is_done(), "DB work cannot precede the Discord ACK"
        events.append(("autosave", None))
        return ""

    async def edit(**kwargs):
        events.append(("edit", kwargs))
    monkeypatch.setattr(ui, "_autosave", fake_autosave)
    session = ui.StudioSession(guild_id=111, owner_id=222)
    interaction = SimpleNamespace(
        message=SimpleNamespace(id=123),
        response=_Response(events),
        edit_original_response=edit,
    )
    if route == "menu":
        action = ui._menu(interaction, heading="Menu", description="Details", view=ui.StudioDesignTools(session))
    else:
        action = ui._home(interaction, session)
    asyncio.run(action)
    assert [kind for kind, _value in events] == ["ack", "autosave", "edit"]
    assert events[0][1] == {}  # Deferred component update, no public message.
    assert events[-1][1]["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


def test_modal_editor_navigation_deferred_ephemerally_and_not_twice(monkeypatch):
    events = []
    async def fake_autosave(*args, **kwargs):
        events.append(("autosave", None))
        return ""
    async def edit(**kwargs):
        events.append(("edit", kwargs))
    monkeypatch.setattr(ui, "_autosave", fake_autosave)
    interaction = SimpleNamespace(
        message=None, response=_Response(events), edit_original_response=edit,
    )
    session = ui.StudioSession(guild_id=111, owner_id=222)
    asyncio.run(ui._home(interaction, session))
    assert events[0] == ("ack", {"ephemeral": True, "thinking": True})
    assert events[1][0] == "autosave"
    events.clear()
    asyncio.run(ui._menu(
        interaction, heading="Menu", description="Again", view=ui.StudioDesignTools(session)
    ))
    assert events[0][0] == "autosave"
    assert events[1][0] == "edit"
