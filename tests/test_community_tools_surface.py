from __future__ import annotations

from pathlib import Path

import discord

from stoney_verify.commands_ext.public_command_surface_v2 import CompactDankHomeView
from stoney_verify.commands_ext.public_community_tools import (
    CommunityToolsView,
    FunLookupView,
    StickyCenterView,
)

ROOT = Path(__file__).resolve().parents[1]


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def test_home_exposes_community_tools_without_new_slash_child() -> None:
    assert "Community Tools" in _labels(CompactDankHomeView(1))

    surface = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
    contract = (ROOT / "stoney_verify/command_surface_contract.py").read_text(encoding="utf-8")
    assert 'dank_children != ["home", "upload"]' in surface
    assert 'PUBLIC_DANK_CHILDREN: frozenset[str] = frozenset({"home", "purge", "upload"})' in contract


def test_community_center_keeps_sticky_poll_info_permissions_and_lookup_paths() -> None:
    labels = _labels(CommunityToolsView(1))
    assert {
        "Sticky Messages",
        "Create Poll",
        "Embed Builder",
        "Member / Server Info",
        "Permission Check",
        "Fun & Lookup",
        "Dank Shield Home",
        "Close",
    } <= labels


def test_sticky_center_keeps_full_management_surface() -> None:
    labels = _labels(StickyCenterView(1, config=None, poll=None))
    assert {
        "Create / Edit",
        "Pause / Resume",
        "Remove",
        "Server Stickies",
        "Speed / Cadence",
        "Custom Sender",
        "Sticky Poll",
        "Poll Controls",
        "Community Tools",
    } <= labels


def test_fun_lookup_surface_includes_stickybot_family_and_provider_truth() -> None:
    labels = _labels(FunLookupView(1))
    assert {
        "Weather",
        "Wikipedia",
        "Random Wikipedia",
        "Random WikiHow",
        "Urban Dictionary",
        "Roll Dice",
        "Coin Flip",
        "Compatibility",
        "Image AI Status",
    } <= labels


def test_runtime_has_one_canonical_message_listener_registration_and_no_monkey_patch() -> None:
    runtime = (ROOT / "stoney_verify/community_tools_runtime.py").read_text(encoding="utf-8")
    assert 'bot.add_listener(runtime.on_message, "on_message")' in runtime
    assert runtime.count('add_listener(runtime.on_message, "on_message")') == 1
    assert "@bot.event" not in runtime
    assert "bot.on_message =" not in runtime
    assert "webhook_url" not in runtime


def test_custom_sender_uses_bot_managed_webhook_not_user_webhook_secret() -> None:
    runtime = (ROOT / "stoney_verify/community_tools_runtime.py").read_text(encoding="utf-8")
    ui = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
    assert 'MANAGED_WEBHOOK_NAME = "Dank Shield Sticky"' in runtime
    assert ".create_webhook(" in runtime
    assert "no webhook URL was saved" in ui
    assert "webhook_url" not in ui


def test_migration_is_service_role_only_and_persists_both_sticky_tables() -> None:
    migration = (ROOT / "supabase/migrations/202608110001_community_tools.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.dank_stickies" in migration
    assert "create table if not exists public.dank_sticky_polls" in migration
    assert "enable row level security" in migration
    assert "revoke all on table public.dank_stickies from anon, authenticated" in migration
    assert "grant all on table public.dank_stickies to service_role" in migration
    assert "webhook_url" not in migration


def test_network_utilities_use_no_key_primary_apis_and_nsfw_guard() -> None:
    lookups = (ROOT / "stoney_verify/community_lookup_service.py").read_text(encoding="utf-8")
    ui = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
    assert "geocoding-api.open-meteo.com/v1/search" in lookups
    assert "api.open-meteo.com/v1/forecast" in lookups
    assert "en.wikipedia.org/w/api.php" in lookups
    assert "channel.is_nsfw()" in ui
    assert "vision provider" in ui.lower()
