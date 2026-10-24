"""Shared brand themes for the existing Embed Studio, without a second editor."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import asyncio

import discord
import pytest

from stoney_verify.embed_studio_core import (
    MAX_THEMES, STUDIO_KEY, StudioValidationError, THEME_PRESETS,
    apply_theme, library_payload, new_project, parse_library,
    theme_from_project, theme_name, validate_theme, validate_project,
)
from stoney_verify.embed_studio_ui import (
    BrandThemeMenu, BrandThemeSelect, ConfirmThemeRemovalView,
    StudioHomeView, StudioSession, ThemeNameModal,
)


def _saved_lib():
    return parse_library({STUDIO_KEY: {
        "revision": 3,
        "templates": {"a": {"name": "Rules", "project": new_project()}},
        "published": {"5:6": {"channel_id": "5", "message_id": "6"}},
        "drafts": {},
    }})


def test_builtins_are_valid_and_only_change_color():
    draft = new_project()
    draft["embeds"][0].update(author_name="Keep me", footer="Keep footer")
    for key, _name, style in THEME_PRESETS:
        assert key
        new = apply_theme(draft, style)
        assert new["embeds"][0]["color"] == style["color"]
        assert new["embeds"][0]["author_name"] == "Keep me"
        assert new["embeds"][0]["footer"] == "Keep footer"
        assert new["content"] == draft["content"]
        assert draft["embeds"][0]["color"] == "5865F2"


def test_style_reuse_updates_all_embeds_without_touching_fields_or_buttons():
    draft = new_project()
    draft["embeds"][0].update(color="123456", author_name="Team", footer="Events",
                              author_icon="https://example.com/logo.png")
    other = deepcopy(draft["embeds"][0])
    other.update(color="FFFFFF", author_name="Other", footer="Other footer",
                 description="Different text", fields=[{"name": "One", "value": "Two", "inline": True}])
    draft["embeds"].append(other)
    draft["links"] = [{"label": "More", "url": "https://example.com"}]
    draft["content"] = "No new pings"
    before = validate_project(draft)
    style = theme_from_project(draft)
    result = apply_theme(draft, style)
    assert all(e["color"] == "123456" and e["author_name"] == "Team" for e in result["embeds"])
    assert result["embeds"][1]["description"] == "Different text"
    assert result["embeds"][1]["fields"] == before["embeds"][1]["fields"]
    assert result["links"] == before["links"]
    assert result["content"] == before["content"]
    assert draft["embeds"][1]["color"] == "FFFFFF"


@pytest.mark.parametrize("style", [
    {}, {"bogus": "123"}, {"color": "not-hex"},
    {"author_url": "http://example.com"}, {"author_icon": "https://localhost/test"},
    {"footer": "x"*2049}, "bad",
])
def test_bad_styles_rejected_without_draft_mutation(style):
    old = new_project()
    with pytest.raises(StudioValidationError):
        apply_theme(old, style)
    assert old == new_project()


def test_invalid_active_page_and_bad_theme_names_are_rejected():
    with pytest.raises(StudioValidationError):
        theme_from_project(new_project(), embed_index=10)
    for value in ["", " "*6, "hello\nname", "x"*61, None]:
        with pytest.raises(StudioValidationError):
            theme_name(value)
    assert theme_name("  Staff Style  ") == "Staff Style"


def test_shared_library_round_trip_keeps_legacy_templates_posts_drafts():
    lib = _saved_lib()
    original = deepcopy(lib)
    lib["themes"]["abc"] = {"name": "Green", "style": theme_from_project(new_project())}
    payload = library_payload(lib)
    assert payload["revision"] == original["revision"] + 1
    for section in ("templates", "published", "drafts"):
        assert payload[section] == original[section]
    same = parse_library({STUDIO_KEY: payload})
    assert same["themes"] == lib["themes"]
    assert parse_library({STUDIO_KEY: {}})["themes"] == {}


def test_theme_cap_storage_validation_and_no_foreign_style_data():
    lib = _saved_lib()
    for i in range(MAX_THEMES):
        lib["themes"][str(i)] = {"name": "Style "+str(i), "style": {"color": "123456"}}
    assert len(library_payload(lib)["themes"]) == MAX_THEMES
    lib["themes"]["overflow"] = {"name": "Too many", "style": {"color": "123456"}}
    with pytest.raises(StudioValidationError, match="full"):
        library_payload(lib)
    lib["themes"].pop("overflow")
    lib["themes"]["0"]["style"] = {"author_url": "http://bad.example"}
    with pytest.raises(StudioValidationError):
        library_payload(lib)


def test_mobile_controls_reuse_editor_and_stay_within_discord_limits():
    session = StudioSession(guild_id=5, owner_id=9)
    home = StudioHomeView(session)
    assert "Brand Themes" in {getattr(item, "label", "") for item in home.children}
    counts = {}
    for item in home.children:
        counts[item.row] = counts.get(item.row, 0) + 1
    assert max(counts.values()) <= 5
    lib = _saved_lib()
    lib["themes"]["foo"] = {"name": "Stored", "style": {"color": "ABCDEF"}}
    view = BrandThemeMenu(session, lib)
    assert isinstance(view.children[0], BrandThemeSelect)
    assert any(o.label == "Stored" for o in view.children[0].options)
    assert len(view.children[0].options) == len(THEME_PRESETS) + 1
    assert len(ThemeNameModal(session).children) == 1
    assert any(isinstance(c, discord.ui.Button) and c.style == discord.ButtonStyle.danger
               for c in ConfirmThemeRemovalView(session, "foo").children)


def test_apply_uses_existing_draft_history_and_can_undo():
    session = StudioSession(guild_id=5, owner_id=9)
    original = deepcopy(session.project)
    session.apply(lambda p: p.update(apply_theme(p, {"color": "123456"})))
    assert session.project["embeds"][0]["color"] == "123456"
    assert session.dirty
    assert session.undo()
    assert session.project == original


def test_save_theme_storage_conflict_and_offline_fail_closed(monkeypatch):
    from stoney_verify import embed_studio_ui as ui
    session = StudioSession(guild_id=5, owner_id=9)
    modal = ThemeNameModal(session)
    modal.name_input._value = "Staff branding"
    calls = []
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=5, owner_id=9),
        user=SimpleNamespace(id=9),
        response=SimpleNamespace(defer=lambda **kw: asyncio.sleep(0)),
    )
    async def say(_i, text, **_kwargs):
        calls.append(text)
    async def load(_guild_id):
        return {STUDIO_KEY: {}}, _saved_lib()
    async def conflict(*_args):
        return False
    monkeypatch.setattr(ui, "_staff", lambda *_: True)
    monkeypatch.setattr(ui, "_say", say)
    monkeypatch.setattr(ui, "_load_library", load)
    monkeypatch.setattr(ui, "_save_library", conflict)
    asyncio.run(modal.on_submit(interaction))
    assert "updated server themes" in calls[-1]
    async def offline(_guild):
        raise RuntimeError("private database unavailable")
    monkeypatch.setattr(ui, "_load_library", offline)
    asyncio.run(modal.on_submit(interaction))
    assert "not saved" in calls[-1]
    assert not session.dirty
