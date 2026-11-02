from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from stoney_verify.commands_ext.public_role_center import (
    RoleEditorHomeView,
    RolesProfilesView,
)
from stoney_verify.commands_ext.public_self_roles_group import (
    ProfileCosmeticRoleManagerView,
)
from stoney_verify.commands_ext.role_center_navigation import (
    BackToRoleCenterButton,
    CloseRolePanelButton,
    close_panel,
    defer_panel,
)


def _labels(view: discord.ui.View) -> list[str]:
    return [str(getattr(item, "label", "")) for item in view.children]


def test_role_center_close_is_available_once_for_each_authorized_section() -> None:
    for section in ("profile", "staff", "roles"):
        view = RolesProfilesView(
            123, staff=True, role_manager=True, setup_manager=True, section=section,
        )
        assert _labels(view).count("Close") == 1
        assert _labels(view).count("Dank Shield Home") == 1
        assert len(view.children) <= 25
        assert all(0 <= int(item.row or 0) <= 4 for item in view.children)
    assert _labels(RoleEditorHomeView(123)).count("Close") == 1


def test_profile_builder_retains_back_and_close_without_duplicating_controls() -> None:
    view = ProfileCosmeticRoleManagerView(author_id=123)
    assert _labels(view).count("Roles & Profiles") == 1
    assert _labels(view).count("Close") == 1
    assert any(isinstance(item, BackToRoleCenterButton) for item in view.children)
    assert any(isinstance(item, CloseRolePanelButton) for item in view.children)


def test_close_panel_acknowledges_component_and_deletes_original() -> None:
    response = SimpleNamespace(is_done=lambda: False, defer=AsyncMock())
    interaction = SimpleNamespace(
        response=response,
        message=object(),
        delete_original_response=AsyncMock(),
        edit_original_response=AsyncMock(),
    )
    asyncio.run(close_panel(interaction))
    response.defer.assert_awaited_once_with()
    interaction.delete_original_response.assert_awaited_once_with()
    interaction.edit_original_response.assert_not_awaited()


def test_close_panel_falls_back_to_disabling_controls_when_delete_is_forbidden() -> None:
    response = SimpleNamespace(is_done=lambda: True, defer=AsyncMock())
    error = discord.HTTPException(SimpleNamespace(status=403, reason="Forbidden"), "Cannot delete")
    interaction = SimpleNamespace(
        response=response,
        message=object(),
        delete_original_response=AsyncMock(side_effect=error),
        edit_original_response=AsyncMock(),
    )
    asyncio.run(close_panel(interaction))
    response.defer.assert_not_awaited()
    interaction.edit_original_response.assert_awaited_once()
    assert interaction.edit_original_response.await_args.kwargs["view"] is None
    assert interaction.edit_original_response.await_args.kwargs["embed"] is None


def test_defer_panel_distinguishes_component_and_slash_origins() -> None:
    response = SimpleNamespace(is_done=lambda: False, defer=AsyncMock())
    interaction = SimpleNamespace(response=response, message=object())
    asyncio.run(defer_panel(interaction))
    response.defer.assert_awaited_once_with()
    response.defer.reset_mock()
    interaction.message = None
    asyncio.run(defer_panel(interaction))
    response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
