from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as studio_v2
from stoney_verify.services import server_design_apply_service as apply_service
from stoney_verify.services import server_design_plan_service as plan_service


ROOT = Path(__file__).resolve().parents[1]
GROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
ENHANCEMENTS = (ROOT / "stoney_verify/commands_ext/public_design_enhancements.py").read_text(encoding="utf-8")
COMMAND_GUARD = (ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py").read_text(encoding="utf-8")
SETUP_GUARD = (ROOT / "stoney_verify/startup_guards/setup_overview_command_guard.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
PLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")
APPLY = (ROOT / "stoney_verify/services/server_design_apply_service.py").read_text(encoding="utf-8")
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def _labels(view: Any) -> list[str]:
    return [str(getattr(child, "label", "") or "") for child in view.children]


def test_home_is_five_plain_language_workflows_not_a_mixed_control_panel() -> None:
    view = studio_v2.DesignHomeView({"theme_id": "gothic_clean", "strength": 4})
    assert _labels(view) == [
        "Design Entire Server",
        "Edit One Category / Channel",
        "Fix Inconsistent Names",
        "Saved Rules & Protection",
        "Undo Last Apply",
    ]
    assert all(child.__class__.__name__ != "DesignServerThemeSelect" for child in view.children)
    assert all(child.__class__.__name__ != "DesignServerStrengthSelect" for child in view.children)


def test_home_copy_explains_the_only_immediate_rename_exception() -> None:
    assert "The home screen never changes a Discord name" in V2
    assert "Only one action is immediate" in V2
    assert "Edit One Category / Channel → **Rename**" in V2
    assert "Nothing renames a channel/category until a reviewed preview is applied" not in V2
    assert "Saved Rules & Protection" in V2
    assert "does not rename anything by itself" in V2


def test_server_design_controls_live_only_inside_design_entire_server_workflow() -> None:
    view = studio_v2.DesignServerView({"theme_id": "gothic_clean", "strength": 4})
    class_names = [child.__class__.__name__ for child in view.children]
    assert "DesignServerThemeSelect" in class_names
    assert "DesignServerStrengthSelect" in class_names
    assert "Preview Server Changes" in _labels(view)
    assert "Change Separators Only" in _labels(view)
    assert "saved as settings immediately" in V2
    assert "do **not** rename a single Discord channel/category" in V2


def test_item_edit_workflow_distinguishes_immediate_rename_from_preview_flows() -> None:
    assert "**Rename** is the only immediate name change" in V2
    assert "**Preview Fixes** and **Custom Format** show a preview" in V2
    assert "**Rename applies immediately. No Apply button appears after Rename.**" in LEGACY
    assert "One exact Rename" in V2


def test_fix_inconsistent_names_is_scan_then_explicit_smart_preview() -> None:
    view = studio_v2.ReviewRepairView()
    assert _labels(view) == ["Scan Saved Design", "Build Smart Repair Preview", "Back"]
    assert "Scan Saved Design** is read-only" in V2
    assert "Read-only scan. Nothing was renamed." in V2
    assert "analyzed each category independently" in V2
    assert "blocks Apply when confidence is not high enough" in V2


def test_active_registration_does_not_activate_design_runtime_monkey_patch_guards() -> None:
    assert "public_design_studio_v2 as design" in GROUP
    assert "activate_public_design_enhancements" not in GROUP
    assert "server_design_strict_layout_guard" not in GROUP
    assert "server_design_majority_layout_guard" not in GROUP
    assert "server_design_strict_layout_guard" not in ENHANCEMENTS
    assert "server_design_majority_layout_guard" not in ENHANCEMENTS
    assert "command_guard.build_design_plan =" not in PLAN
    assert "DesignDoctorView =" not in PLAN


def test_legacy_bridge_is_small_explicit_navigation_help_and_apply_boundary() -> None:
    assert legacy._home_embed is studio_v2._home_embed
    assert legacy._start_here_embed is studio_v2._compat_help_embed
    assert legacy._design_help_embed is studio_v2._compat_help_embed
    assert legacy.DesignHomeView is studio_v2.DesignHomeView
    assert legacy.DesignPreviewView is studio_v2.ReviewedPreviewView
    assert legacy.StyleChangePreviewView is studio_v2.LegacyStyleChangePreviewView

    bridge_start = V2.index("def _install_legacy_compatibility_bridge")
    bridge_end = V2.index("\n\n_install_legacy_compatibility_bridge()", bridge_start)
    bridge = V2[bridge_start:bridge_end]
    for required in (
        "legacy._home_embed = _home_embed",
        "legacy._start_here_embed = _compat_help_embed",
        "legacy.DesignHomeView = DesignHomeView",
        "legacy.DesignPreviewView = ReviewedPreviewView",
        "legacy.StyleChangePreviewView = LegacyStyleChangePreviewView",
    ):
        assert required in bridge
    for forbidden in (
        "legacy.build_design_plan =",
        "legacy.DesignDoctorView =",
        "legacy._load_design_options =",
        "legacy.register_public_design_studio_command =",
    ):
        assert forbidden not in bridge


def test_all_legacy_back_paths_now_resolve_the_consolidated_home() -> None:
    assert LEGACY.count("view=DesignHomeView(options)") >= 8
    assert legacy.DesignHomeView is studio_v2.DesignHomeView
    assert legacy._home_embed is studio_v2._home_embed


def test_design_command_guard_is_validation_only_not_registry_mutation() -> None:
    assert "validation-only" in COMMAND_GUARD or "validation shim" in COMMAND_GUARD
    for forbidden in (
        "commands_ext.COMMAND_MODULES =",
        "commands_ext.COMMAND_PROFILES =",
        "commands_ext._selected_command_modules =",
        "_install_selected_module_wrapper",
    ):
        assert forbidden not in COMMAND_GUARD


def test_setup_guard_no_longer_attaches_deprecated_design_command_shim() -> None:
    assert "server_design_studio_command_guard" not in SETUP_GUARD
    assert 'allowed.add("overview")' in SETUP_GUARD
    assert 'allowed.update({"overview", "design"})' not in SETUP_GUARD


def test_plan_defaults_preserve_gothic_pipe_and_visual_name_policy_without_global_mutation() -> None:
    before_protected = set(plan_service.studio.DEFAULT_PROTECTED_NAMES)
    options = plan_service.normalize_plan_options(
        {"theme_id": "gothic_clean", "strength": 4, "protection_rules": {"staff": "never"}},
        strict=True,
    )
    assert options["separator_id"] == "pipe_spaced"
    assert options["exact_match"] is True
    assert options["protection_rules"]["staff"] == "never"
    assert options["protection_rules"]["logs"] == "full"
    assert set(plan_service.studio.DEFAULT_PROTECTED_NAMES) == before_protected


def test_strict_plan_marks_saved_rule_layers_exact_without_changing_precedence() -> None:
    options = plan_service.normalize_plan_options(
        {
            "format_lock_global": {"enabled": True, "font": "fraktur", "exact_match": False},
            "category_format_locks": {"10": {"font": "bold_sans", "exact_match": False}},
            "channel_format_locks": {"20": {"font": "monospace", "exact_match": False}},
        },
        strict=True,
    )
    assert options["format_lock_global"]["exact_match"] is True
    assert options["category_format_locks"]["10"]["font"] == "bold_sans"
    assert options["category_format_locks"]["10"]["exact_match"] is True
    assert options["channel_format_locks"]["20"]["font"] == "monospace"
    assert options["channel_format_locks"]["20"]["exact_match"] is True


def _patch_category_aware_path(monkeypatch: pytest.MonkeyPatch, *, apply_allowed: bool) -> list[str]:
    events: list[str] = []
    monkeypatch.setattr(plan_service, "live_records", lambda _guild: [{"id": "1", "category_id": "9", "kind": "text", "name": "chat"}])

    def build_category_aware(studio: Any, options: Any, records: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        events.append("category-aware")
        assert list(records)[0]["category_id"] == "9"
        return ({**dict(options), "category_format_locks": {"9": {"font": "bold_sans"}}}, {"9": {"font": "bold_sans"}})

    def annotate(studio: Any, items: Any, options: Any) -> list[dict[str, Any]]:
        events.append("annotate")
        return list(items)

    async def build_plan(_guild: Any, options: Any) -> list[dict[str, Any]]:
        events.append("legacy-plan")
        assert options["__use_live_majority_layout"] is True
        assert options["category_format_locks"]["9"]["exact_match"] is True
        return [{"channel_id": "1", "before": "chat", "after": "💬|chat", "status": "changed", "blockers": []}]

    monkeypatch.setattr(plan_service.majority, "build_category_aware_options", build_category_aware)
    monkeypatch.setattr(plan_service.majority, "annotate_category_aware_plan_items", annotate)
    monkeypatch.setattr(legacy, "build_design_plan", build_plan)
    monkeypatch.setattr(
        plan_service.repair_confidence,
        "evaluate_repair_plan",
        lambda items, context: {"apply_allowed": apply_allowed, "level": "high" if apply_allowed else "low"},
    )
    return events


def test_drift_plan_calls_category_aware_native_service_not_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _patch_category_aware_path(monkeypatch, apply_allowed=True)
    items, options, analysis = run(plan_service.build_drift_repair_plan(SimpleNamespace(id=1), {"theme_id": "gothic_clean"}))
    assert events == ["category-aware", "legacy-plan", "annotate"]
    assert items[0]["status"] == "changed"
    assert options["__respect_saved_rules"] is True
    assert options["__repair_confidence_result"]["apply_allowed"] is True
    assert analysis["mode"] == "category_aware"


def test_smart_auto_detect_confidence_blocks_styled_heading_simplification() -> None:
    scored = plan_service.repair_confidence.score_repair_item(
        {"kind": "category", "status": "changed", "before": "╭─ 𝕊𝕋𝔸𝔽𝔽 ─╮", "after": "staff"},
        context="smart_category_auto_detect",
    )
    assert scored["classification"] == plan_service.repair_confidence.BLOCKED_AESTHETIC_DOWNGRADE
    assert scored["confidence"] == 0


def test_low_confidence_drift_plan_fails_closed_before_ui_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_category_aware_path(monkeypatch, apply_allowed=False)
    items, options, analysis = run(plan_service.build_drift_repair_plan(SimpleNamespace(id=1), {"theme_id": "gothic_clean"}))
    assert plan_service.confidence_allows_apply(options) is False
    assert analysis["confidence"]["apply_allowed"] is False
    assert items[0]["status"] == "failed"
    assert "confidence is too low" in items[0]["blockers"][0]


class ReturningChannel:
    """Fake discord.py 2.x edit behavior: return a new object, do not mutate self."""

    def __init__(
        self,
        channel_id: int,
        name: str,
        guild: "ReturningGuild",
        *,
        fail_edit: bool = False,
        edits: list[str] | None = None,
    ) -> None:
        self.id = channel_id
        self.name = name
        self.guild = guild
        self.fail_edit = fail_edit
        self.edits = edits if edits is not None else []

    async def edit(self, *, name: str, reason: str) -> "ReturningChannel":
        self.edits.append(name)
        if self.fail_edit:
            raise RuntimeError("edit failed")
        new = ReturningChannel(self.id, name, self.guild, fail_edit=self.fail_edit, edits=self.edits)
        self.guild.replace(new)
        return new


class ReturningGuild:
    def __init__(self) -> None:
        self.channels: list[ReturningChannel] = []
        self.categories: list[Any] = []
        self._by_id: dict[int, ReturningChannel] = {}

    def add(self, channel_id: int, name: str, *, fail_edit: bool = False) -> ReturningChannel:
        channel = ReturningChannel(channel_id, name, self, fail_edit=fail_edit)
        self.replace(channel)
        return channel

    def replace(self, channel: ReturningChannel) -> None:
        self._by_id[channel.id] = channel
        self.channels = [current for current in self.channels if current.id != channel.id] + [channel]

    def get_channel(self, channel_id: int) -> ReturningChannel | None:
        return self._by_id.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> ReturningChannel:
        return self._by_id[channel_id]

    async def fetch_channels(self) -> list[ReturningChannel]:
        return list(self.channels)


def _row(channel_id: int, before: str, after: str) -> dict[str, Any]:
    return {"channel_id": str(channel_id), "before": before, "after": after, "status": "changed", "blockers": []}


def test_apply_preflight_rejects_one_stale_row_before_any_edit() -> None:
    guild = ReturningGuild()
    first = guild.add(1, "one")
    second = guild.add(2, "changed-by-admin")
    ready, skipped, errors = run(
        apply_service.preflight_plan(
            guild,
            [_row(1, "one", "ONE"), _row(2, "two", "TWO")],
            name_limit=100,
        )
    )
    assert skipped == 0
    assert len(ready) == 1
    assert errors == ["`two` is now `changed-by-admin`."]
    assert first.edits == []
    assert second.edits == []


def test_apply_tracks_returned_channel_object_instead_of_assuming_in_place_edit() -> None:
    guild = ReturningGuild()
    original = guild.add(1, "one")
    ready, _skipped, errors = run(apply_service.preflight_plan(guild, [_row(1, "one", "ONE")], name_limit=100))
    assert errors == []
    result = run(apply_service.apply_prepared(guild, ready, user_id=7, delay_seconds=0))
    assert result.ok is True
    assert original.name == "one"
    assert guild.get_channel(1).name == "ONE"
    assert result.applied[0].channel.name == "ONE"


def test_second_apply_failure_compensates_first_and_never_touches_later_rows() -> None:
    guild = ReturningGuild()
    first = guild.add(1, "one")
    second = guild.add(2, "two", fail_edit=True)
    third = guild.add(3, "three")
    rows = [_row(1, "one", "ONE"), _row(2, "two", "TWO"), _row(3, "three", "THREE")]
    ready, _skipped, errors = run(apply_service.preflight_plan(guild, rows, name_limit=100))
    assert errors == []
    result = run(apply_service.apply_prepared(guild, ready, user_id=7, delay_seconds=0))
    assert result.ok is False
    assert result.residual == []
    assert result.restored_count == 1
    assert guild.get_channel(1).name == "one"
    assert guild.get_channel(2).name == "two"
    assert guild.get_channel(3).name == "three"
    assert first.edits == ["ONE", "one"]
    assert second.edits == ["TWO"]
    assert third.edits == []


def test_mid_apply_admin_race_stops_and_compensates_prior_rename() -> None:
    guild = ReturningGuild()
    first = guild.add(1, "one")
    guild.add(2, "two")
    ready, _skipped, errors = run(
        apply_service.preflight_plan(guild, [_row(1, "one", "ONE"), _row(2, "two", "TWO")], name_limit=100)
    )
    assert errors == []
    guild.replace(ReturningChannel(2, "admin-changed", guild))
    result = run(apply_service.apply_prepared(guild, ready, user_id=7, delay_seconds=0))
    assert result.ok is False
    assert "changed to `admin-changed` while Apply was running" in result.failure
    assert result.residual == []
    assert guild.get_channel(1).name == "one"
    assert guild.get_channel(2).name == "admin-changed"
    assert first.edits == ["ONE", "one"]


def test_undo_preflight_stale_row_changes_nothing() -> None:
    guild = ReturningGuild()
    current = guild.add(1, "someone-edited-this")
    snapshot = [{**_row(1, "old", "new"), "old_name": "old", "new_name": "new"}]
    ready, errors = run(apply_service.preflight_undo(guild, snapshot, name_limit=100))
    assert ready == []
    assert errors == ["`new` is now `someone-edited-this`."]
    assert current.edits == []


def test_failed_undo_compensates_earlier_restore_back_to_pre_undo_name() -> None:
    guild = ReturningGuild()
    first = guild.add(1, "NEW-ONE", fail_edit=True)
    second = guild.add(2, "NEW-TWO")
    snapshot = [
        {**_row(1, "OLD-ONE", "NEW-ONE"), "old_name": "OLD-ONE", "new_name": "NEW-ONE"},
        {**_row(2, "OLD-TWO", "NEW-TWO"), "old_name": "OLD-TWO", "new_name": "NEW-TWO"},
    ]
    ready, errors = run(apply_service.preflight_undo(guild, snapshot, name_limit=100))
    assert errors == []
    result = run(apply_service.undo_prepared(guild, ready, user_id=9, delay_seconds=0))
    assert result.ok is False
    assert result.residual == []
    assert result.restored_count == 1
    assert guild.get_channel(1).name == "NEW-ONE"
    assert guild.get_channel(2).name == "NEW-TWO"
    assert first.edits == ["OLD-ONE"]
    assert second.edits == ["OLD-TWO", "NEW-TWO"]


def test_v2_batch_apply_delegates_to_transaction_service_and_durable_snapshot_gate() -> None:
    apply_start = V2.index("class ReviewedPreviewView")
    bridge_start = V2.index("class LegacyStyleChangePreviewView", apply_start)
    block = V2[apply_start:bridge_start]
    assert "apply_service.preflight_plan" in block
    assert "apply_service.apply_prepared" in block
    assert "apply_service.compensate_applied" in block
    assert "Apply Reversed Because Undo History Could Not Be Saved" in block
    assert "**No names were changed.**" in block
    assert "legacy._open_rollback" not in block


def test_v2_undo_is_transactional_and_only_pops_snapshot_after_success() -> None:
    start = V2.index("class UndoConfirmView")
    end = V2.index("class ReviewedPreviewView", start)
    block = V2[start:end]
    assert "apply_service.preflight_undo" in block
    assert "apply_service.undo_prepared" in block
    assert "Undo Blocked Before Any Rename" in block
    assert "the Undo snapshot was kept" in block
    assert block.index("if not result.ok:") < block.index("_pop_snapshot_if_current")
    assert "legacy._open_rollback" not in V2


def test_every_active_legacy_preview_uses_the_transactional_apply_owner() -> None:
    assert legacy.DesignPreviewView is studio_v2.ReviewedPreviewView
    assert legacy.StyleChangePreviewView is studio_v2.LegacyStyleChangePreviewView
    assert issubclass(studio_v2.LegacyStyleChangePreviewView, studio_v2.ReviewedPreviewView)
    assert V2.count('label="Apply Reviewed Changes"') == 1
    assert "This preview is obsolete" in V2
    assert "_store_durable_snapshot" in V2


def test_apply_service_has_no_continue_after_transaction_failure_loop() -> None:
    assert "preflight the complete batch" in APPLY
    assert "revalidate each item immediately before its edit" in APPLY
    assert "compensate earlier edits" in APPLY
    assert "_rollback_after_failure" in APPLY


def test_setup_and_slash_command_converge_on_same_studio_module() -> None:
    bridge = (ROOT / "stoney_verify/commands_ext/public_design_bridge.py").read_text(encoding="utf-8")
    assert "public_design_studio_v2 as design" in bridge
    assert "public_design_studio_v2 as design" in GROUP
    assert "competing design screen" in bridge
