"""Real discoverability and safe slash interaction dispatch invariants."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from stoney_verify.command_surface_contract import PUBLIC_DANK_CHILDREN, PUBLIC_HIDDEN_DANK_CHILDREN
from stoney_verify.commands_ext import public_command_surface_v2 as surface
from stoney_verify.navigation_registry import feature_by_key


def test_shortcuts_are_useful_curated_registered_and_permission_owned():
    shortcuts = dict((name, key) for name, _, key in surface.DANK_SHORTCUTS)
    assert set(shortcuts) == {
        "community", "design", "diagnostics", "help", "logs", "pings",
        "profile", "protection", "share", "stats", "status", "voice-safety", "welcome",
    }
    assert len(shortcuts) + 5 <= 25
    assert set(shortcuts).isdisjoint(PUBLIC_HIDDEN_DANK_CHILDREN)
    assert PUBLIC_DANK_CHILDREN == set(shortcuts) | {"home", "setup", "upload", "purge", "cheers", "birthdays"}
    assert "cheers" not in shortcuts  # CHEERS is an owner-only command group, not a regular shortcut.
    assert len(shortcuts) == len(surface.DANK_SHORTCUTS)
    for name, _, feature_key in surface.DANK_SHORTCUTS:
        assert feature_key in surface._FEATURE_ROUTE_KEYS
        feature = feature_by_key(feature_key)
        assert feature is not None, name
    for staff_key in ("protection", "voice_safety", "logs_activity", "server_stats", "server_design", "share_router", "community_pings_manager", "diagnostics"):
        assert feature_by_key(staff_key).manager_only


def test_normal_slash_shortcuts_delegate_to_exact_existing_feature_owner():
    async def run():
        interaction = SimpleNamespace()
        with patch.object(surface, "_route_feature", new_callable=AsyncMock) as route:
            await surface._shortcut_callback("protection")(interaction)
            await surface._shortcut_callback("voice_safety")(interaction)
            await surface._shortcut_callback("welcome")(interaction)
            await surface._shortcut_callback("help")(interaction)
            assert route.await_count == 4
            assert [args.args[1] for args in route.await_args_list] == [
                "protection", "voice_safety", "welcome", "help"
            ]
    asyncio.run(run())


def test_component_only_ui_is_opened_with_private_slash_message():
    async def run():
        interaction = SimpleNamespace()
        paths = (
            ("community_hub", "stoney_verify.commands_ext.public_community_hub.open_community_hub"),
            ("share_router", "stoney_verify.commands_ext.public_share_router.open_share_router"),
        )
        for key, path in paths:
            with patch(path, new_callable=AsyncMock) as owner:
                await surface._shortcut_callback(key)(interaction)
                owner.assert_awaited_once_with(interaction, replace_message=False)
    asyncio.run(run())


def test_pings_shortcut_routes_by_manage_guild_authority():
    """Phase 0: /dank pings must not route members into the staff manager."""
    async def run():
        staff = SimpleNamespace(
            permissions=SimpleNamespace(administrator=False, manage_guild=True),
            user=SimpleNamespace(id=7),
            guild=SimpleNamespace(owner_id=999),
        )
        member = SimpleNamespace(
            permissions=SimpleNamespace(administrator=False, manage_guild=False),
            user=SimpleNamespace(id=7),
            guild=SimpleNamespace(owner_id=999),
        )
        with patch(
            "stoney_verify.commands_ext.public_toke.open_community_ping_setup",
            new_callable=AsyncMock,
        ) as staff_owner, patch(
            "stoney_verify.commands_ext.public_toke.open_member_community_pings",
            new_callable=AsyncMock,
        ) as member_owner:
            await surface._shortcut_callback("community_pings_manager")(staff)
            staff_owner.assert_awaited_once_with(staff, replace_message=False)
            member_owner.assert_not_awaited()

            await surface._shortcut_callback("community_pings_manager")(member)
            member_owner.assert_awaited_once_with(member, replace_message=False)
            staff_owner.assert_awaited_once()
    asyncio.run(run())


def test_server_stats_slash_defers_before_canonical_editor():
    async def run():
        response = SimpleNamespace(is_done=lambda: False, defer=AsyncMock())
        interaction = SimpleNamespace(response=response)
        with patch.object(surface, "_route_feature", new_callable=AsyncMock) as owner:
            await surface._shortcut_callback("server_stats")(interaction)
            response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            owner.assert_awaited_once_with(interaction, "server_stats")
    asyncio.run(run())
