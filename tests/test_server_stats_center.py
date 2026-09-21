from __future__ import annotations

from pathlib import Path

import discord

from stoney_verify import security_stats
from stoney_verify.commands_ext.public_command_surface_v2 import CompactDankHomeView
from stoney_verify.commands_ext.public_server_stats import ServerStatsView


ROOT = Path(__file__).resolve().parents[1]


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def _custom_ids(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "custom_id", "") or "")
        for item in view.children
        if str(getattr(item, "custom_id", "") or "")
    }


def test_dank_home_has_first_class_server_stats_destination() -> None:
    assert "Server Stats" in _labels(CompactDankHomeView(1))


def test_server_stats_center_exposes_management_and_customization_controls() -> None:
    view = ServerStatsView(owner_id=1, cfg={})
    labels = _labels(view)
    assert {
        "Enable Stats",
        "Disable & Remove",
        "Refresh Now",
        "Category Name",
        "Numbers: Compact",
        "Placement: Top",
        "Reset Look",
        "Dank Shield Home",
        "Close",
    } <= labels

    assert {
        "dank_server_stats:visible",
        "dank_server_stats:label",
        "dank_server_stats:enable",
        "dank_server_stats:disable",
        "dank_server_stats:refresh",
        "dank_server_stats:category_name",
        "dank_server_stats:number_style",
        "dank_server_stats:placement",
        "dank_server_stats:reset",
        "dank_server_stats:home",
        "dank_server_stats:close",
    } <= _custom_ids(view)


def test_server_stats_center_offers_every_authoritative_counter() -> None:
    view = ServerStatsView(owner_id=1, cfg={})
    visible = next(
        item
        for item in view.children
        if str(getattr(item, "custom_id", "")) == "dank_server_stats:visible"
    )
    assert {str(option.value) for option in visible.options} == set(
        security_stats.DEFAULT_SECURITY_STATS_VISIBLE_KEYS
    )


def test_protection_routes_legacy_live_stats_button_to_canonical_stats_center() -> None:
    source = (
        ROOT / "stoney_verify/commands_ext/public_protection_center.py"
    ).read_text(encoding="utf-8")
    assert 'custom_id="dank_protection:live_stats"' in source
    assert 'label="Server Stats"' in source
    assert "open_server_stats_center(interaction)" in source
    assert "ensure_security_stats_display(guild)" not in source


def test_server_stats_does_not_expand_compact_dank_slash_children() -> None:
    source = (
        ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py"
    ).read_text(encoding="utf-8")
    assert 'dank_children != ["home", "setup", "upload"]' in source
    assert 'label="Server Stats"' in source
