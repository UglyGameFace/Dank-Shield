from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord

from stoney_verify import config_history_ui
from stoney_verify.commands_ext import public_setup_cleanup as cleanup
from stoney_verify.commands_ext import public_setup_full_customization as customization
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_recovery as recovery
from stoney_verify.commands_ext import public_setup_solid as solid


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def labels(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def test_generic_nested_setup_navigation_is_predictable() -> None:
    assert labels(solid.SetupNavView()) == [
        "Back to All Features",
        "Setup Home",
        "Close",
    ]


def test_full_customization_pages_share_parent_home_close() -> None:
    views = (
        customization.FullChooseExistingView(),
        customization.RoleCustomizationPageOne(),
        customization.RoleCustomizationPageTwo(),
        customization.DiscordCategoryCustomizationView(),
        customization.ChannelCustomizationPageOne(),
        customization.ChannelCustomizationPageTwo(),
        customization.LogStatusCustomizationView(),
    )
    for view in views:
        view_labels = labels(view)
        assert "Back to All Features" in view_labels
        assert "Setup Home" in view_labels
        assert "Close" in view_labels
        row_counts: dict[int, int] = {}
        for child in view.children:
            row = int(getattr(child, "row", 0) or 0)
            row_counts[row] = row_counts.get(row, 0) + 1
        assert all(count <= 5 for count in row_counts.values())
        assert len(view.children) <= 25


def test_full_customization_registration_does_not_replace_solid_classes() -> None:
    before = solid.ChooseExistingView
    customization._PATCHED = False
    customization.install_full_customization()
    assert solid.ChooseExistingView is before


def test_recovery_registration_does_not_replace_setup_home() -> None:
    before = solid._build_main_setup_payload
    recovery._PATCHED = False
    recovery.register_public_setup_recovery_commands(None, None)
    assert solid._build_main_setup_payload is before


def test_cleanup_registration_does_not_replace_recovery_owners() -> None:
    before_embed = recovery._build_recovery_embed
    before_view = recovery.RecoveryCenterView
    cleanup._PATCHED = False
    cleanup.register_public_setup_cleanup_commands(None, None)
    assert recovery._build_recovery_embed is before_embed
    assert recovery.RecoveryCenterView is before_view


def test_existing_server_route_uses_direct_customization_view(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def allowed(interaction: Any) -> bool:
        return True

    class Response:
        async def edit_message(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(solid, "_require_setup_permission", allowed)
    interaction = SimpleNamespace(response=Response())
    run(recommend._open_existing_server(interaction))
    assert isinstance(captured.get("view"), customization.FullChooseExistingView)


def test_config_history_navigation_matches_aio_hierarchy() -> None:
    main = config_history_ui.ConfigHistoryView([])
    assert labels(main) == [
        "Choose Backup Contents",
        "Refresh",
        "Back to All Features",
        "Setup Home",
        "Close",
    ]

    backup = config_history_ui.BackupContentsView()
    assert "Setup Home" in labels(backup)
    assert "Close" in labels(backup)

    detail = config_history_ui.ConfigVersionDetailView(
        1,
        {
            "changed_items": ["ticket_prefix"],
            "missing_items": ["ticket_prefix"],
        },
    )
    assert "Back to All Features" in labels(detail)
    assert "Setup Home" in labels(detail)
    assert "Close" in labels(detail)


def test_repair_and_cleanup_views_keep_navigation_available() -> None:
    for view in (
        recovery.RecoveryCenterView(),
        cleanup.PatchedRecoveryCenterView(),
        cleanup.CleanupPreviewView(),
    ):
        view_labels = labels(view)
        assert "Back to All Features" in view_labels
        assert "Setup Home" in view_labels
        assert "Close" in view_labels
        assert len(view.children) <= 25
