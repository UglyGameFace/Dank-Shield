from __future__ import annotations

from pathlib import Path

import discord

from stoney_verify.commands_ext.public_command_surface_v2 import CompactDankHomeView
from stoney_verify.commands_ext.public_community_tools import (
    CommunityToolsView,
    FunLookupView,
    StickyCenterView,
    StickySettingsView,
    StickyTypeView,
)
from stoney_verify.community_tools_service import StickyConfig

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


def test_community_center_keeps_core_tools_without_command_sprawl() -> None:
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


def test_sticky_center_is_compact_and_routes_advanced_management() -> None:
    labels = _labels(StickyCenterView(1, config=None, poll=None))
    assert {
        "Create / Edit",
        "Preview / Test",
        "Sticky Settings",
        "Sticky Poll",
        "Quiet Server Notice",
        "Server Stickies",
        "Community Tools",
    } <= labels
    assert {"Pause / Resume", "Remove", "Speed / Cadence", "Custom Sender"}.isdisjoint(labels)

    config = StickyConfig(guild_id=1, channel_id=2, content="hello")
    settings = _labels(StickySettingsView(1, config, None))
    assert {"Pause / Resume", "Remove", "Speed / Cadence", "Custom Sender", "Back to Sticky"} <= settings


def test_sticky_editor_is_guided_instead_of_free_text_mode_selection() -> None:
    labels = _labels(StickyTypeView(1, None, None))
    assert {"Message Sticky", "Embed Sticky", "Back"} <= labels
    ui = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
    assert 'label="Mode: plain or embed"' not in ui
    assert 'label="Hex color"' in ui


def test_fun_lookup_surface_contains_real_features_only() -> None:
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
    } <= labels
    assert "Image AI Status" not in labels


def test_runtime_has_one_canonical_message_listener_registration_and_no_monkey_patch() -> None:
    runtime = (ROOT / "stoney_verify/community_tools_runtime.py").read_text(encoding="utf-8")
    assert 'bot.add_listener(runtime.on_message, "on_message")' in runtime
    assert runtime.count('add_listener(runtime.on_message, "on_message")') == 1
    assert "@bot.event" not in runtime
    assert "bot.on_message =" not in runtime
    assert "webhook_url" not in runtime


def test_custom_sender_uses_managed_webhook_and_fails_closed() -> None:
    runtime = (ROOT / "stoney_verify/community_tools_runtime.py").read_text(encoding="utf-8")
    ui = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
    assert 'MANAGED_WEBHOOK_NAME = "Dank Shield Sticky"' in runtime
    assert ".create_webhook(" in runtime
    assert "No webhook URL/token is stored" in ui
    assert "cannot manage its sticky webhook" in runtime
    assert "webhook_url" not in ui


def test_migrations_are_service_role_only_and_atomic_bundle_exists() -> None:
    migration = (ROOT / "supabase/migrations/20260811122504_community_tools.sql").read_text(encoding="utf-8")
    hardening = (ROOT / "supabase/migrations/20260905121500_community_tools_hardening.sql").read_text(encoding="utf-8")
    assert "create table if not exists public.dank_stickies" in migration
    assert "create table if not exists public.dank_sticky_polls" in migration
    assert "enable row level security" in migration
    assert "revoke all on table public.dank_stickies from anon, authenticated" in migration
    assert "grant all on table public.dank_stickies to service_role" in migration
    assert "create or replace function public.save_dank_sticky_bundle" in hardening
    assert "delete from public.dank_sticky_polls" in hardening
    assert "grant execute on function public.save_dank_sticky_bundle(jsonb, jsonb) to service_role" in hardening
    assert "webhook_url" not in migration
    assert "webhook_url" not in hardening


def test_network_utilities_use_bounded_no_key_apis_and_nsfw_guard() -> None:
    lookups = (ROOT / "stoney_verify/community_lookup_service.py").read_text(encoding="utf-8")
    ui = (ROOT / "stoney_verify/commands_ext/public_community_tools.py").read_text(encoding="utf-8")
    assert "geocoding-api.open-meteo.com/v1/search" in lookups
    assert "api.open-meteo.com/v1/forecast" in lookups
    assert "en.wikipedia.org/w/api.php" in lookups
    assert "MAX_CONCURRENT_LOOKUPS" in lookups
    assert "precipitation_probability_max" in lookups
    assert "channel.is_nsfw()" in ui
    assert "vision provider" not in ui.lower()
