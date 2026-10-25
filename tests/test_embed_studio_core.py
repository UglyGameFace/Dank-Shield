"""Rich Embed Studio contract and mobile UI regression tests."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import discord
import pytest

from stoney_verify.embed_studio_core import (
    MAX_EMBEDS, MAX_FIELDS, MAX_LINK_BUTTONS, MAX_TEMPLATES,
    StudioValidationError, default_embed, new_project, validate_project,
    render_embeds, link_view, library_payload, parse_library, https_url,
)
from stoney_verify.embed_studio_ui import (
    StudioSession, StudioHomeView, StudioReviewView, StudioTextModal,
    StudioImagesModal, StudioAppearanceModal, FieldListView, LinkMenu,
    TemplateMenu, PublishedMenu, _from_message,
)

ROOT = Path(__file__).resolve().parents[1]


def test_default_preview_is_safe_and_uses_no_mentions() -> None:
    model = validate_project(new_project())
    built = render_embeds(model)
    assert len(built) == 1
    assert built[0].title == "Your headline"
    assert built[0].color.value == 0x5865F2
    assert model["links"] == []


def test_rich_author_media_footer_and_inline_fields() -> None:
    model = new_project()
    embed = model["embeds"][0]
    embed.update({
        "author_name": "Our team", "author_url": "https://example.com/about",
        "author_icon": "https://example.com/a.png",
        "footer": "Thanks for reading", "footer_icon": "https://example.com/f.png",
        "title_url": "https://example.com",
        "image": "https://example.com/full.png",
        "thumbnail": "https://example.com/thumb.png",
        "timestamp": True,
        "fields": [{"name": "Time", "value": "8 PM", "inline": True}],
    })
    valid = validate_project(model)
    display = render_embeds(valid)[0].to_dict()
    assert display["author"]["name"] == "Our team"
    assert display["footer"]["text"] == "Thanks for reading"
    assert display["image"]["url"].endswith("full.png")
    assert display["thumbnail"]["url"].endswith("thumb.png")
    assert display["fields"][0]["inline"] is True
    assert display["timestamp"]


def test_url_links_are_real_discord_link_buttons() -> None:
    project = new_project()
    project["links"] = [{"label": "Server site", "url": "https://example.com"}]
    view = link_view(project)
    assert isinstance(view, discord.ui.View)
    assert len(view.children) == 1
    button = view.children[0]
    assert isinstance(button, discord.ui.Button)
    assert button.style == discord.ButtonStyle.link
    assert button.url == "https://example.com"


@pytest.mark.parametrize("bad", [
    "http://example.com", "javascript:alert(1)",
    "https://user:pass@example.com", "https://localhost/home",
    "https://127.0.0.1/", "https://example.com/a b",
    "https://", "ftp://example.com",
])
def test_invalid_button_and_media_urls_fail_closed(bad: str) -> None:
    with pytest.raises(StudioValidationError):
        https_url(bad, "URL")


def test_enforces_discord_project_limits_without_truncation() -> None:
    project = new_project()
    project["embeds"][0]["description"] = "x" * 4096
    project["embeds"][0]["fields"] = [
        {"name": "a", "value": "y" * 1024, "inline": False}
        for _ in range(2)
    ]
    with pytest.raises(StudioValidationError, match="6000"):
        validate_project(project)
    project = new_project()
    project["embeds"] = [deepcopy(default_embed()) for _ in range(MAX_EMBEDS + 1)]
    with pytest.raises(StudioValidationError, match="1 and 10"):
        validate_project(project)
    project = new_project()
    project["embeds"][0]["fields"] = [
        {"name": "a", "value": "b", "inline": False} for _ in range(MAX_FIELDS + 1)
    ]
    with pytest.raises(StudioValidationError, match="25"):
        validate_project(project)
    project = new_project()
    project["links"] = [
        {"label": str(i), "url": "https://example.com"} for i in range(MAX_LINK_BUTTONS + 1)
    ]
    with pytest.raises(StudioValidationError, match="five"):
        validate_project(project)


def test_invalid_edit_keeps_previous_draft_and_undo_works() -> None:
    session = StudioSession(guild_id=111, owner_id=222)
    original = deepcopy(session.project)
    with pytest.raises(StudioValidationError):
        session.apply(lambda p: p["embeds"][0].update(color="this is not hex"))
    assert session.project == original
    assert not session.history
    session.apply(lambda p: p["embeds"][0].update(title="Updated"))
    assert session.project["embeds"][0]["title"] == "Updated"
    assert session.undo()
    assert session.project == original


def test_templates_are_per_server_bounded_and_versioned() -> None:
    project = new_project()
    lib = {
        "revision": 4, "templates": {
            "abc": {"name": "Rules", "project": project}
        }, "published": {},
    }
    payload = library_payload(lib)
    assert payload["revision"] == 5
    assert payload["templates"]["abc"]["name"] == "Rules"
    parsed = parse_library({"embed_studio_v1": payload})
    assert len(parsed["templates"]) == 1
    bloated = {
        "revision": 0, "templates": {
            str(i): {"name": str(i), "project": project}
            for i in range(MAX_TEMPLATES + 1)
        }, "published": {},
    }
    with pytest.raises(StudioValidationError, match="full"):
        library_payload(bloated)


def test_mobile_studio_has_clear_separate_sections_and_one_confirmation() -> None:
    session = StudioSession(guild_id=111, owner_id=222)
    view = StudioHomeView(session)
    labels = {str(getattr(item, "label", "") or "") for item in view.children}
    assert {
        "Pick a Layout", "Write Message", "Look & Images",
        "Add Buttons", "Saved Designs", "More Tools",
        "Edit Existing Post", "Undo", "Preview & Post",
    } <= labels
    assert len([item for item in view.children if isinstance(item, discord.ui.Button)]) == 9
    assert not {"JSON Files", "Smart Text", "Role Controls", "Form Builder"} & labels
    from stoney_verify.embed_studio_ui import (
        StudioDesignTools, StudioButtonTools, StudioSavedTools, StudioAdvancedTools,
    )
    assert {"Colors & Style", "Add Pictures", "Saved Pictures", "Reuse Server Style"} <= {
        item.label for item in StudioDesignTools(session).children
    }
    assert {"Website Buttons", "Let Members Pick Roles", "Collect Answers"} <= {
        item.label for item in StudioButtonTools(session).children
    }
    assert {"Saved Post Layouts", "Restore My Draft", "Share With Staff", "Import / Export"} <= {
        item.label for item in StudioSavedTools(session).children
    }
    assert {"Extra Text Sections", "Multiple Post Cards", "Automatic Server Details",
            "Upcoming Scheduled Posts"} <= {
        item.label for item in StudioAdvancedTools(session).children
    }
    assert any(isinstance(item, discord.ui.ChannelSelect) for item in view.children)
    review = StudioReviewView(session)
    assert "Publish / Save Edit" in {getattr(item, "label", "") for item in review.children}
    assert "Back & Edit" in {getattr(item, "label", "") for item in review.children}
    assert len(StudioTextModal(session).children) == 3
    assert len(StudioAppearanceModal(session).children) == 5
    assert len(StudioImagesModal(session).children) == 4
    assert isinstance(FieldListView(session), discord.ui.View)
    assert isinstance(LinkMenu(session), discord.ui.View)


def test_one_canonical_editor_replaces_parallel_community_and_setup_modals() -> None:
    community = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text()
    setup = (ROOT / "stoney_verify/embed_builder_services.py").read_text()
    studio = (ROOT / "stoney_verify/embed_studio_ui.py").read_text()
    assert "from ..embed_studio_ui import open_embed_studio" in community
    assert "from .embed_studio_ui import open_embed_studio" in setup
    assert "class EmbedBuilderModal" not in community
    assert "class EmbedDraftView" not in community
    assert "class EmbedDraftModal" not in setup
    assert "class EmbedBuilderCenterView" not in setup
    assert 'label="Publish / Save Edit"' in studio
    assert "compare_and_swap_guild_config_key(" in studio
    assert "get_guild_config(guild_id, refresh=True)" in studio
    assert "message.author.id != interaction.client.user.id" in studio
    assert "allowed_mentions=_MENTIONS" in studio
    assert "async def open_embed_studio(" in studio



def test_all_one_tap_layouts_are_editable_and_within_limits() -> None:
    from stoney_verify.embed_studio_presets import PRESETS, project_for_preset
    assert len(PRESETS) == 6
    for key, name in PRESETS:
        project = project_for_preset(key)
        assert project["embeds"][0]["title"]
        assert project["embeds"][0]["description"]
        assert isinstance(name, str)
        assert len(render_embeds(project)) == 1
