from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_setup_cleanup as cleanup
from stoney_verify.commands_ext import public_setup_recovery as recovery


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def button_labels(view: discord.ui.View) -> list[str]:
    return [
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    ]


def test_safe_start_over_clears_canonical_setup_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def snapshot(guild_id: int, user: Any) -> dict[str, Any]:
        return {
            "guild_id": str(guild_id),
            "config": {"setup_choice": "basic_server"},
            "ticket_categories": [],
        }

    def write(
        guild_id: int,
        patch: dict[str, Any],
        saved_snapshot: dict[str, Any],
    ) -> str:
        captured["guild_id"] = guild_id
        captured["patch"] = dict(patch)
        captured["snapshot"] = dict(saved_snapshot)
        return "guild_configs"

    def clear_categories(guild_id: int) -> tuple[int, str]:
        captured["cleared_categories_for"] = guild_id
        return 2, ""

    monkeypatch.setattr(recovery, "_current_snapshot_sync", snapshot)
    monkeypatch.setattr(recovery, "_write_config_patch_sync", write)
    monkeypatch.setattr(
        recovery,
        "_delete_ticket_categories_sync",
        clear_categories,
    )
    monkeypatch.setattr(
        recovery,
        "invalidate_guild_config",
        lambda guild_id: captured.setdefault("invalidated", guild_id),
    )

    guild = SimpleNamespace(id=123)
    user = SimpleNamespace(id=456)

    message, ok = run(
        recovery._reset_saved_setup(
            guild,
            user,
            include_menu=True,
        )
    )

    assert ok is True
    patch = captured["patch"]

    assert patch["setup_choice"] is None
    assert patch["setup_choice_label"] is None
    assert patch["tickets_enabled"] is None
    assert patch["verification_enabled"] is None
    assert patch["spam_guard_enabled"] is None
    assert patch["moderation_enabled"] is None
    assert patch["setup_completed"] is None
    assert patch["ticket_category_id"] is None
    assert captured["cleared_categories_for"] == 123
    assert "saved Quick Setup plan" in message


def test_clear_saved_roles_and_channels_preserves_plan_and_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        recovery,
        "_current_snapshot_sync",
        lambda guild_id, user: {
            "guild_id": str(guild_id),
            "config": {
                "setup_choice": "basic_server",
                "tickets_enabled": True,
            },
            "ticket_categories": [],
        },
    )

    def write(
        guild_id: int,
        patch: dict[str, Any],
        saved_snapshot: dict[str, Any],
    ) -> str:
        captured["patch"] = dict(patch)
        return "guild_configs"

    monkeypatch.setattr(recovery, "_write_config_patch_sync", write)
    monkeypatch.setattr(
        recovery,
        "_delete_ticket_categories_sync",
        lambda guild_id: pytest.fail(
            "mapping-only reset must not clear ticket choices"
        ),
    )
    monkeypatch.setattr(
        recovery,
        "invalidate_guild_config",
        lambda guild_id: None,
    )

    guild = SimpleNamespace(id=321)
    user = SimpleNamespace(id=654)

    message, ok = run(
        recovery._reset_saved_setup(
            guild,
            user,
            include_menu=False,
        )
    )

    assert ok is True
    patch = captured["patch"]

    assert patch["ticket_category_id"] is None
    assert patch["verified_role_id"] is None
    assert "setup_choice" not in patch
    assert "tickets_enabled" not in patch
    assert "spam_guard_enabled" not in patch
    assert "setup_completed" not in patch
    assert "keeping the current setup plan" in message


def test_repair_and_restart_actions_explain_destructive_scope() -> None:
    labels = button_labels(cleanup.PatchedRecoveryCenterView())

    assert "Preview Cleanup" in labels
    assert "Safe Start Over" in labels
    assert "Start Over & Remove Bot Setup" in labels
    assert "Clear Saved Roles & Channels" in labels
    assert "Clear Ticket Choices Only" in labels
    assert "Restore Last Reset" in labels
    assert "Rebuild Default Ticket Choices" in labels

    assert "Reset Saved Setup Only" not in labels
    assert "Reset Ticket Menu Only" not in labels
    assert "Rebuild Recommended Menu" not in labels


def test_cleanup_preview_uses_plain_folder_language() -> None:
    labels = button_labels(cleanup.CleanupPreviewView())

    assert "Remove Empty Setup Folders" in labels
    assert "Remove All Detected Setup Items" in labels
    assert "Remove Empty Setup Categories" not in labels
    assert "Delete All Bot-Created Setup Items" not in labels


def test_fallback_recovery_view_matches_current_recovery_language() -> None:
    labels = button_labels(recovery.RecoveryCenterView())
    expected = {
        "Safe Start Over",
        "Clear Saved Roles & Channels",
        "Clear Ticket Choices Only",
        "Restore Last Reset",
        "Rebuild Default Ticket Choices",
    }

    assert expected.issubset(set(labels))
    assert "Setup Home" in labels
    assert "Close" in labels
