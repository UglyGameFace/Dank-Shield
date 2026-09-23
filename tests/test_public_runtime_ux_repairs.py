from __future__ import annotations

from pathlib import Path

import discord

from stoney_verify.commands_ext import public_runtime_ux_repairs as repairs


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "stoney_verify" / "commands_ext" / "public_runtime_ux_repairs.py").read_text(encoding="utf-8")


def _button_labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    }


def _custom_ids(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "custom_id", "") or "")
        for child in view.children
        if getattr(child, "custom_id", None)
    }


def test_advanced_setup_removes_redundant_manage_features_route() -> None:
    view = repairs.CompactAdvancedWithoutDuplicateFeatures()
    assert _button_labels(view) == {
        "Repair / Restart",
        "Help",
        "Setup Home",
        "Close",
    }
    assert "Manage Features" not in _button_labels(view)
    assert "dank_setup_advanced:features" not in _custom_ids(view)


def test_late_runtime_ux_layer_is_setup_only() -> None:
    assert "public_design_studio" not in SOURCE
    assert "public_design_studio_v2" not in SOURCE
    assert "ChannelEditorPickerView =" not in SOURCE
    assert "ChannelEditorActionView =" not in SOURCE
    assert "ReviewedPreviewView =" not in SOURCE
    assert "DesignPreviewView =" not in SOURCE
    assert "_preview_embed =" not in SOURCE
    assert "_channel_action_embed =" not in SOURCE


def test_runtime_patch_only_replaces_compact_setup_view() -> None:
    names = set(repairs.apply_runtime_ux_repairs.__code__.co_names)
    assert "CompactAdvancedView" in names
    assert "EDITOR_PAGE_SIZE" not in names
    assert "ChannelEditorPickerView" not in names
    assert "ReviewedPreviewView" not in names
