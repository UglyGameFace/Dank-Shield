from __future__ import annotations

from pathlib import Path

import discord

from stoney_verify.commands_ext.public_command_surface_v2 import FeatureCategoryView
from stoney_verify.commands_ext.public_community_tools import (
    CommunityToolsView,
    FunLookupView,
)

ROOT = Path(__file__).resolve().parents[1]


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def test_community_navigation_exposes_tools_without_new_slash_child() -> None:
    assert "Community Tools" in _labels(FeatureCategoryView(1, "community"))

    surface = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text(encoding="utf-8")
    contract = (ROOT / "stoney_verify/command_surface_contract.py").read_text(encoding="utf-8")
    assert 'expected_children = sorted(["home", "setup", "upload", "cheers", "birthdays"] + [name for name, _, _ in DANK_SHORTCUTS])' in surface
    assert '"voice-safety"' in contract
    assert '("community", "Open the community hub' in surface


def test_community_center_keeps_core_tools_without_command_sprawl() -> None:
    labels = _labels(CommunityToolsView(1))
    assert {
        "Quiet Server Notice",
        "Create Poll",
        "Embed Studio",
        "Member / Server Info",
        "Permission Check",
        "Fun & Lookup",
        "Dank Shield Home",
        "Close",
    } <= labels


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
