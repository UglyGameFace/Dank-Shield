"""Embed Studio role controls: validation, persistence and fail-closed routing."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import discord
import pytest

from stoney_verify.embed_studio_core import (
    StudioValidationError, new_project, render_embeds, validate_project,
)
from stoney_verify.embed_studio_roles import (
    ROLE_PREFIX, member_role_view, parse_role_custom_id, role_custom_id,
)
from stoney_verify.embed_studio_ui import StudioHomeView, StudioReviewView, StudioRolesMenu, StudioSession

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "0123456789abcdef0123456789abcdef"


def _model(n: int = 1, mode: str = "buttons") -> dict:
    project = new_project()
    project["role_mode"] = mode
    project["role_actions"] = [
        {"role_id": str(100000000000000001 + i), "label": f"Role {i + 1}"}
        for i in range(n)
    ]
    return project


def test_roles_are_opt_in_and_old_templates_stay_compatible():
    project = new_project()
    assert project["role_actions"] == []
    old_project = deepcopy(project)
    old_project.pop("role_actions")
    old_project.pop("role_mode")
    cleaned = validate_project(old_project)
    assert cleaned["role_actions"] == [] and cleaned["role_mode"] == "buttons"


def test_discord_role_layout_caps_and_mixed_buttons():
    p = _model(5)
    assert len(validate_project(p)["role_actions"]) == 5
    p["links"] = [{"label": "Visit", "url": "https://example.org"}]
    with pytest.raises(StudioValidationError, match="five total"):
        validate_project(p)
    p["role_mode"] = "dropdown"
    assert len(validate_project(p)["role_actions"]) == 5
    p = _model(10, "dropdown")
    assert len(validate_project(p)["role_actions"]) == 10
    p["role_actions"].append({"role_id": "100000000000000015", "label": "Extra"})
    with pytest.raises(StudioValidationError, match="ten"):
        validate_project(p)


def test_rejects_bad_duplicate_or_arbitrary_role_identifiers():
    p = _model(1)
    p["role_actions"].append(deepcopy(p["role_actions"][0]))
    with pytest.raises(StudioValidationError, match="only appear once"):
        validate_project(p)
    for invalid in ("not an id", "-1", "0", "001X"):
        p = _model(1)
        p["role_actions"][0]["role_id"] = invalid
        with pytest.raises(StudioValidationError):
            validate_project(p)
    p = _model(1)
    p["role_mode"] = "untrusted"
    with pytest.raises(StudioValidationError, match="buttons or a dropdown"):
        validate_project(p)


def test_persistent_button_custom_ids_resolve_only_exact_token_and_indices():
    assert role_custom_id(TOKEN, 0) == ROLE_PREFIX + TOKEN + ":0"
    assert parse_role_custom_id(role_custom_id(TOKEN, 0)) == (TOKEN, "0")
    assert parse_role_custom_id(role_custom_id(TOKEN, "pick")) == (TOKEN, "pick")
    for broken in ("dank:es:role:v1:other:0", ROLE_PREFIX + TOKEN + ":500",
                   ROLE_PREFIX + TOKEN + ":../pick", ROLE_PREFIX + TOKEN + ":0:extra"):
        assert parse_role_custom_id(broken) is None
    assert parse_role_custom_id("dank:profile:other") is None


def test_role_buttons_and_select_are_real_discord_components():
    buttons = member_role_view(validate_project(_model(3)), TOKEN)
    assert buttons is not None
    assert len(buttons.children) == 3
    assert all(isinstance(item, discord.ui.Button) for item in buttons.children)
    assert all(item.custom_id.startswith(ROLE_PREFIX) for item in buttons.children)
    dropdown = member_role_view(validate_project(_model(7, "dropdown")), TOKEN)
    assert dropdown is not None
    assert len(dropdown.children) == 1
    option = dropdown.children[0]
    assert isinstance(option, discord.ui.Select)
    assert option.custom_id == role_custom_id(TOKEN, "pick")
    assert len(option.options) == 7


def test_role_controls_can_coexist_with_url_buttons():
    p = _model(1)
    p["links"] = [{"label": "Site", "url": "https://example.org"}]
    view = member_role_view(validate_project(p), TOKEN)
    assert view is not None and len(view.children) == 2
    assert any(isinstance(item, discord.ui.Button) and item.style == discord.ButtonStyle.link for item in view.children)
    assert any(isinstance(item, discord.ui.Button) and item.custom_id == role_custom_id(TOKEN, 0) for item in view.children)


def test_editor_displays_role_picker_and_disables_preview_role_actions():
    session = StudioSession(guild_id=123, owner_id=456, project=validate_project(_model(2)))
    home = StudioHomeView(session)
    assert "Add Buttons" in {str(getattr(item, "label", "")) for item in home.children}
    from stoney_verify.embed_studio_ui import StudioButtonTools
    assert "Let Members Pick Roles" in {item.label for item in StudioButtonTools(session).children}
    menu = StudioRolesMenu(session)
    assert any(isinstance(item, discord.ui.RoleSelect) for item in menu.children)
    preview = StudioReviewView(session)
    disabled = [item for item in preview.children
                if isinstance(item, discord.ui.Button) and item.label.startswith("Role ")]
    assert len(disabled) == 2
    assert all(item.disabled for item in disabled)


def test_handler_is_message_and_guild_scoped_and_rechecks_safety():
    roles = (ROOT / "stoney_verify/embed_studio_roles.py").read_text()
    editor = (ROOT / "stoney_verify/embed_studio_ui.py").read_text()
    bootstrap = (ROOT / "stoney_verify/commands.py").read_text()
    assert "get_guild_config(int(guild.id), refresh=True)" in roles
    # Role clicks must read the authoritative normalized Studio library after
    # an opt-in v2 cutover, not a stale guild-config compatibility snapshot.
    assert "storage_v2_enabled()" in roles
    assert "read_library(int(guild.id), config)" in roles
    assert 'record.get("role_token") != token' in roles
    assert 'record.get("channel_id", 0)' in roles
    assert 'record.get("message_id", 0)' in roles
    assert "message.author.id != interaction.client.user.id" in roles
    assert "_profile_cosmetic_role_blocker(guild, role, config)" in roles
    assert "guild.fetch_member(user.id)" in roles
    assert "community_member_lock(int(guild.id), int(user.id))" in roles
    assert "timeout=None" in roles
    assert 'bot.add_listener(_listener, "on_interaction")' in roles
    assert "install_embed_studio_role_runtime(bot, strict=True)" in bootstrap
    assert "role_token" in editor and "role_actions" in editor
    assert "await message.edit(view=link_view(draft)" in editor
    assert "self.preview_project" in editor
    assert "async def _role_controls(" in editor


def test_existing_discord_embed_limits_still_apply():
    p = _model(2)
    p["embeds"][0]["description"] = "Valid text"
    assert len(render_embeds(validate_project(p))) == 1
    p["embeds"][0]["description"] = "x" * 4097
    with pytest.raises(StudioValidationError):
        validate_project(p)



def test_click_throttle_is_bounded_and_respects_member_isolation(monkeypatch):
    from stoney_verify import embed_studio_roles as roles
    monkeypatch.setattr(roles, "_ROLE_LAST_CLICK", {})
    assert not roles._rate_limited(1, 10, now=100.0)
    assert roles._rate_limited(1, 10, now=101.0)
    assert not roles._rate_limited(1, 11, now=101.0)
    assert not roles._rate_limited(2, 10, now=101.0)
    assert not roles._rate_limited(1, 10, now=103.0)
    assert len(roles._ROLE_LAST_CLICK) == 3



def test_saved_post_limit_never_evicts_active_role_panels():
    from stoney_verify.embed_studio_core import trim_published_history
    rows = {
        str(i): {"channel_id": "11", "message_id": str(i),
                 "role_actions": ([{"label": "Member", "role_id": "123"}] if i < 20 else [])}
        for i in range(21)
    }
    trimmed = trim_published_history(rows)
    assert len(trimmed) == 20
    assert all(str(i) in trimmed for i in range(20))
    assert "20" not in trimmed
    rows = {
        str(i): {"role_actions": [{"label": "Member", "role_id": "123"}]}
        for i in range(21)
    }
    with pytest.raises(StudioValidationError, match="active role panels"):
        trim_published_history(rows)



def test_search_safe_role_browser_uses_existing_alias_aware_picker():
    studio = (ROOT / "stoney_verify/embed_studio_ui.py").read_text()
    assert "from .ui.resource_browser import DankGuildResourceBrowserView" in studio
    assert 'resource_kinds=("role",)' in studio
    assert "predicate=allowed" in studio
    assert "await _add_studio_role(pick_interaction, session, resource)" in studio
    assert 'label="Find Role"' in studio
    assert "get_guild_config(int(guild.id), refresh=True)" in studio
