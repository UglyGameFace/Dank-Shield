"""Reusable media and Smart Text contracts for the one canonical Embed Studio."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.embed_studio_core import (
    MAX_MEDIA_ITEMS, SMART_TEXT_TOKENS, StudioValidationError,
    apply_media_url, library_payload, new_project, parse_library,
    resolve_smart_text, validate_media_item,
)
from stoney_verify.embed_studio_ui import (
    ConfirmMediaRemoval, MediaLibraryMenu, MediaLinkSelect,
    MediaPlacementMenu, MediaSaveModal, SmartTextHelp, StudioHomeView,
    StudioReviewView, StudioSession,
)


def test_old_library_preserves_templates_themes_posts_and_drafts():
    original = {
        "revision": 3,
        "templates": {"a": {"name": "Announcement", "project": new_project()}},
        "themes": {"b": {"name": "Green", "style": {"color": "123456"}}},
        "drafts": {},
        "published": {"12:13": {"channel_id": "12", "message_id": "13"}},
    }
    lib = parse_library({"embed_studio_v1": original})
    assert lib["media"] == {}
    lib["media"]["media1"] = {"name": "Logo", "url": "https://example.com/logo.png"}
    payload = library_payload(lib)
    for key in ("templates", "themes", "drafts", "published"):
        assert payload[key] == original[key]
    assert parse_library({"embed_studio_v1": payload})["media"] == lib["media"]


@pytest.mark.parametrize("bad", [
    {}, {"name": "", "url": "https://example.com/p.png"},
    {"name": "Bad", "url": "http://example.com/p.png"},
    {"name": "Bad", "url": "https://localhost/image.png"},
    {"name": "Bad", "url": "https://a.example/x", "secret": "extra"},
    {"name": "x"*61, "url": "https://example.com/x"},
])
def test_saved_media_rejects_invalid_names_urls_and_unknown_metadata(bad):
    with pytest.raises(StudioValidationError):
        validate_media_item(bad)


def test_media_storage_limit_prevents_unbounded_entries():
    lib = parse_library(None)
    for i in range(MAX_MEDIA_ITEMS):
        lib["media"][str(i)] = {"name": f"Art {i}", "url": "https://example.com/art.png"}
    assert len(library_payload(lib)["media"]) == MAX_MEDIA_ITEMS
    lib["media"]["over"] = {"name": "Overflow", "url": "https://example.com/x.png"}
    with pytest.raises(StudioValidationError, match="full"):
        library_payload(lib)
    lib["media"].pop("over")
    lib["media"]["0"]["url"] = "http://insecure"
    with pytest.raises(StudioValidationError):
        library_payload(lib)


def test_media_apply_only_changes_one_field_on_selected_embed():
    project = new_project()
    other = deepcopy(project["embeds"][0])
    other.update(title="Second", description="Second image")
    project["embeds"].append(other)
    project["links"] = [{"label": "Visit", "url": "https://example.com"}]
    original = deepcopy(project)
    for slot in ("image", "thumbnail", "author_icon", "footer_icon"):
        result = apply_media_url(project, embed_index=1, target=slot, url="https://example.com/art.png")
        assert result["embeds"][1][slot] == "https://example.com/art.png"
        assert result["embeds"][0][slot] == original["embeds"][0][slot]
        assert result["links"] == original["links"]
    assert project == original
    for field in ("", "title", "content", "fields"):
        with pytest.raises(StudioValidationError):
            apply_media_url(project, embed_index=1, target=field, url="https://example.com/x.png")
    with pytest.raises(StudioValidationError):
        apply_media_url(project, embed_index=8, target="image", url="https://example.com/x.png")


def test_media_mobile_control_rows_and_confirmation_are_safe():
    session = StudioSession(guild_id=5, owner_id=10)
    home = StudioHomeView(session)
    labels = {getattr(item, "label", "") for item in home.children}
    assert {"Media Library", "Smart Text", "Preview & Publish"} <= labels
    assert max(sum(1 for item in home.children if item.row == row) for row in range(5)) <= 5
    lib = parse_library(None)
    lib["media"]["saved"] = {"name": "Banner", "url": "https://example.com/banner.png"}
    media = MediaLibraryMenu(session, lib)
    selects = [item for item in media.children if isinstance(item, MediaLinkSelect)]
    assert len(selects) == 1
    assert selects[0].options[0].label == "Banner"
    assert len(MediaSaveModal(session).children) == 2
    place = MediaPlacementMenu(session, "saved")
    assert {"Large Image", "Thumbnail", "Author Icon", "Footer Icon", "Remove Saved Link"} <= {
        item.label for item in place.children
    }
    assert any(item.style == discord.ButtonStyle.danger for item in ConfirmMediaRemoval(session, "saved").children)
    assert "Back to Studio" in {item.label for item in SmartTextHelp(session).children}


def test_smart_text_only_replaces_known_tokens_and_never_changes_source():
    original = new_project()
    original["content"] = "{server.name}: {server.member_count} people"
    original["embeds"][0]["title"] = "Welcome to {server.name}"
    original["embeds"][0]["author_name"] = "{channel.name}"
    original["embeds"][0]["footer"] = "{today.utc}"
    original["embeds"][0]["fields"] = [
        {"name": "Current members", "value": "{server.member_count}", "inline": True},
        {"name": "Literal", "value": "{unknown.call} {user.secret}", "inline": False},
    ]
    resolved = resolve_smart_text(
        original, guild_name="A @everyone Server", channel_name="news",
        member_count=1234, now=datetime(2026, 10, 24, tzinfo=timezone.utc),
    )
    assert "@everyone" not in resolved["content"]
    assert "1234 people" in resolved["content"]
    assert "news" == resolved["embeds"][0]["author_name"]
    assert resolved["embeds"][0]["footer"] == "2026-10-24"
    assert resolved["embeds"][0]["fields"][1]["value"] == "{unknown.call} {user.secret}"
    assert original["content"] == "{server.name}: {server.member_count} people"
    assert len(SMART_TEXT_TOKENS) == 4


def test_member_count_missing_is_a_real_preview_blocker_only_when_used():
    project = new_project()
    assert resolve_smart_text(project, guild_name="Community", channel_name="rules", member_count=None)
    project["embeds"][0]["description"] = "{server.member_count}"
    with pytest.raises(StudioValidationError, match="unavailable"):
        resolve_smart_text(project, guild_name="Community", channel_name="rules", member_count=None)


def test_expansion_is_revalidated_after_preview_substitution():
    original = new_project()
    # The raw tokenized title is valid (234 chars); the expanded one is not.
    original["embeds"][0]["title"] = "{server.name}" * 18
    before = deepcopy(original)
    with pytest.raises(StudioValidationError, match="256"):
        resolve_smart_text(original, guild_name="Long Server Name", channel_name="news", member_count=1)
    assert original == before


def test_preview_snapshot_frozen_and_draft_change_rejected_by_guard():
    session = StudioSession(guild_id=5, owner_id=10)
    session.project["embeds"][0]["description"] = "Hello {server.name}"
    resolved = resolve_smart_text(session.project, guild_name="The 420 Lobby", channel_name="announcements", member_count=45)
    review = StudioReviewView(session, resolved_project=resolved)
    assert review.preview_project["embeds"][0]["description"] == "Hello {server.name}"
    assert review.preview_resolved_project["embeds"][0]["description"] == "Hello The 420 Lobby"
    session.apply(lambda draft: draft["embeds"][0].update(title="Changed after preview"))
    assert session.project != review.preview_project
    assert review.preview_resolved_project["embeds"][0]["description"] == "Hello The 420 Lobby"


def test_media_save_conflict_and_storage_outage_do_not_mutate_draft(monkeypatch):
    from stoney_verify import embed_studio_ui as ui
    session = StudioSession(guild_id=5, owner_id=10)
    modal = MediaSaveModal(session)
    modal.name_input._value = "My banner"
    modal.url_input._value = "https://example.com/banner.png"
    notes = []

    async def defer(**_kw):
        pass

    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=5),
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(defer=defer),
    )

    async def say(_interaction, text, **_kwargs):
        notes.append(text)

    async def load(_guild_id):
        return {"embed_studio_v1": {}}, parse_library(None)

    async def conflict(*_args):
        return False

    monkeypatch.setattr(ui, "_staff", lambda *_: True)
    monkeypatch.setattr(ui, "_say", say)
    monkeypatch.setattr(ui, "_load_library", load)
    monkeypatch.setattr(ui, "_save_library", conflict)
    asyncio.run(modal.on_submit(interaction))
    assert "updated the media library" in notes[-1]

    async def offline(_guild_id):
        raise RuntimeError("storage offline")
    monkeypatch.setattr(ui, "_load_library", offline)
    asyncio.run(modal.on_submit(interaction))
    assert "not saved" in notes[-1]
    assert not session.dirty


def test_real_private_review_uses_resolved_exact_preview_and_frozen_project(monkeypatch):
    from stoney_verify import embed_studio_ui as ui

    session = StudioSession(guild_id=5, owner_id=10, channel_id=44)
    session.project["content"] = "{server.member_count} community members"
    session.project["embeds"][0]["description"] = "Welcome to {server.name}"
    channel = SimpleNamespace(
        mention="<#44>", guild=SimpleNamespace(name="The Community", member_count=900),
        name="announcements",
    )
    output = {}

    async def capture(**kwargs):
        output.update(kwargs)

    interaction = SimpleNamespace(
        response=SimpleNamespace(is_done=lambda: True),
        edit_original_response=capture,
    )
    monkeypatch.setattr(ui, "_target_channel", lambda *_: channel)
    monkeypatch.setattr(ui, "_post_blockers", lambda *_: [])
    asyncio.run(ui._review(interaction, session))
    assert output["embeds"][0].description == "Welcome to The Community"
    assert "900 community members" in output["content"]
    assert output["allowed_mentions"] == discord.AllowedMentions.none()
    confirmation = output["view"]
    assert confirmation.preview_project["content"] == "{server.member_count} community members"
    assert confirmation.preview_resolved_project["content"] == "900 community members"
    assert session.project["content"] == "{server.member_count} community members"


def test_preview_missing_member_count_never_displays_a_false_value(monkeypatch):
    from stoney_verify import embed_studio_ui as ui

    session = StudioSession(guild_id=5, owner_id=10, channel_id=44)
    session.project["content"] = "{server.member_count}"
    channel = SimpleNamespace(
        mention="<#44>", guild=SimpleNamespace(name="Server", member_count=None), name="news",
    )
    errors = []

    async def say(_interaction, note, **kwargs):
        errors.append(note)

    monkeypatch.setattr(ui, "_target_channel", lambda *_: channel)
    monkeypatch.setattr(ui, "_post_blockers", lambda *_: [])
    monkeypatch.setattr(ui, "_say", say)
    asyncio.run(ui._review(SimpleNamespace(), session))
    assert "unavailable" in errors[-1]
    assert session.project["content"] == "{server.member_count}"
