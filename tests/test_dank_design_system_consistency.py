from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as design_v2
from stoney_verify.services import server_design_repair_confidence as confidence
from stoney_verify.services import server_design_studio as studio


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SOURCE = (ROOT / "stoney_verify" / "commands_ext" / "public_design_studio.py").read_text(encoding="utf-8")
V2_SOURCE = (ROOT / "stoney_verify" / "commands_ext" / "public_design_studio_v2.py").read_text(encoding="utf-8")
RUNTIME_SOURCE = (ROOT / "stoney_verify" / "commands_ext" / "public_runtime_ux_repairs.py").read_text(encoding="utf-8")


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(child, "label", "") or "")
        for child in view.children
        if isinstance(child, discord.ui.Button)
    }


def test_mod_chat_formatting_only_drift_is_safe_even_in_safety_zone() -> None:
    styled, _ = studio.transform_text_safe("mod-chat", "fraktur")
    result = confidence.score_repair_item(
        {
            "status": "changed",
            "kind": "text",
            "before": "mod-chat",
            "after": f"「💬」{styled}",
        },
        context="smart_category_auto_detect",
    )

    assert result["classification"] == confidence.SAFE_AUTO_FIX
    assert result["confidence"] >= 90
    assert "semantic channel name is unchanged" in result["reason"]


def test_actual_system_surface_still_requires_manual_review() -> None:
    styled, _ = studio.transform_text_safe("mod-log", "fraktur")
    result = confidence.score_repair_item(
        {
            "status": "changed",
            "kind": "text",
            "before": "mod-log",
            "after": f"「🛡️」{styled}",
        },
        context="smart_category_auto_detect",
    )

    assert result["classification"] == confidence.REVIEW_ONLY


def test_repair_plan_does_not_call_review_rows_blocked() -> None:
    items = [
        {
            "status": "failed",
            "before": "mod-log",
            "after": "styled-mod-log",
            "repair_confidence_classification": confidence.REVIEW_ONLY,
            "repair_confidence_reason": "Safety / staff logs zone needs review before rename.",
        }
    ]
    state = design_v2._repair_plan_state(items)
    assert state["review"] == 1
    assert state["blocked"] == 0
    assert state["ready"] == 0


def test_review_button_only_exists_when_preview_has_real_issues() -> None:
    clean = design_v2.ReviewedPreviewView(can_apply=True, pending_created_at=1.0, issue_count=0)
    issues = design_v2.ReviewedPreviewView(can_apply=False, pending_created_at=1.0, issue_count=1)

    assert not any(label.startswith("Review Issues") for label in _labels(clean))
    assert "Review Issues (1)" in _labels(issues)
    assert "Review Protected Items" not in _labels(clean)
    assert "Review Protected Items" not in _labels(issues)


def test_manual_review_approval_changes_only_that_pending_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild_id = 551
    user_id = 991
    key = legacy._key(guild_id, user_id)
    item = {
        "status": "failed",
        "channel_id": "42",
        "before": "mod-log",
        "after": "styled-mod-log",
        "blockers": [
            "Smart Auto-Detect confidence is too low for this row: Safety / staff logs zone needs review before rename."
        ],
        "repair_confidence_classification": confidence.REVIEW_ONLY,
        "repair_confidence_score": 55,
        "repair_confidence_reason": "Safety / staff logs zone needs review before rename.",
    }
    created_at = legacy._store_pending(
        guild_id,
        user_id,
        {
            "items": [item],
            "options": {},
            "analysis": {},
            "mode": "consistency_check_v2",
        },
    )

    async def allow(_interaction: Any) -> bool:
        return True

    rendered: dict[str, Any] = {}

    async def fake_show(_interaction: Any, payload: Any, pending_created_at: float) -> None:
        rendered["payload"] = payload
        rendered["created_at"] = pending_created_at

    class Response:
        async def send_message(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("manual approval unexpectedly failed")

    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=guild_id),
        user=SimpleNamespace(id=user_id),
        response=Response(),
    )

    monkeypatch.setattr(design_v2, "_require_design_permission", allow)
    monkeypatch.setattr(design_v2, "_show_pending_preview", fake_show)

    button = design_v2.RepairReviewApproveButton(
        item_index=0,
        label="mod-log",
        pending_created_at=created_at,
        row=0,
    )
    asyncio.run(button.callback(interaction))

    pending = legacy._PENDING[key]
    approved = pending["items"][0]
    assert approved["status"] == "changed"
    assert approved["repair_manual_approved"] is True
    assert approved["repair_confidence_classification"] == confidence.SAFE_AUTO_FIX
    assert approved["repair_original_confidence_classification"] == confidence.REVIEW_ONLY
    assert not approved["blockers"]
    assert rendered["created_at"] == created_at

    legacy._PENDING.pop(key, None)


def test_channel_editor_customizations_are_owned_natively() -> None:
    view = legacy.ChannelEditorActionView(
        123,
        category_id=456,
        editor_page=4,
        editor_category_filter_id=None,
    )
    labels = _labels(view)

    assert "Change Icon / Emoji" in labels
    assert "Protection / Skip Rule" in labels
    assert "Protection Mode" not in labels
    assert view.editor_page == 4
    assert view.editor_category_filter_id is None
    assert legacy.EDITOR_PAGE_SIZE == 6


def test_channel_page_jump_is_native_and_exposes_current_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    groups = [
        {
            "category": None,
            "category_id": None,
            "channels": [],
            "part": 1,
            "parts": 1,
            "label": f"Category {index + 1}",
        }
        for index in range(7)
    ]
    monkeypatch.setattr(legacy, "_channel_editor_groups", lambda _guild: groups)

    select = legacy.ChannelPageJumpSelect(
        SimpleNamespace(),
        page=4,
        category_id=None,
    )
    assert [option.value for option in select.options] == [str(index) for index in range(7)]
    assert [option.value for option in select.options if option.default] == ["4"]
    assert "5/7" in str(select.placeholder)


def test_all_primary_server_design_customizations_remain_canonical() -> None:
    for marker in (
        "class DesignServerThemeSelect",
        "class DesignServerFontSelect",
        "class DesignServerStrengthSelect",
        "class DesignServerSeparatorSelect",
        "class DesignServerCategoryFrameSelect",
        "Clean Redesign",
        "Saved Rules & Protection",
        "Undo Last Apply",
    ):
        assert marker in V2_SOURCE

    for marker in (
        "Custom Format",
        "Change Icon / Emoji",
        "Lock Channel Rule",
        "Protection / Skip Rule",
        "Reset This Channel",
        "ChannelPageJumpSelect",
        "DirectRenameModal",
    ):
        assert marker in LEGACY_SOURCE

    assert "public_design_studio" not in RUNTIME_SOURCE
    assert "public_design_studio_v2" not in RUNTIME_SOURCE
