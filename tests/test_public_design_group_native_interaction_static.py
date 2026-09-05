from __future__ import annotations

from pathlib import Path

GROUP = Path("stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
PLAN = Path("stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")
APPLY = Path("stoney_verify/services/server_design_apply_service.py").read_text(encoding="utf-8")


def test_design_group_registers_command_with_native_interaction_guard() -> None:
    assert "run_guarded_interaction" in GROUP
    assert 'action_name="/dank design"' in GROUP
    assert "Dank Design failed safely" in GROUP
    assert "await design.open_design_studio(interaction)" in GROUP


def test_design_group_does_not_delegate_registration_or_activate_runtime_patches() -> None:
    assert "design.register_public_design_studio_command(" not in GROUP
    assert "server_design_studio_command_guard" not in GROUP
    assert "activate_public_design_enhancements" not in GROUP
    assert "public_design_studio_v2 as design" in GROUP


def test_consolidated_studio_uses_one_explicit_plan_service() -> None:
    assert "server_design_plan_service as plans" in STUDIO
    assert "plans.build_saved_design_plan" in STUDIO
    assert "plans.build_drift_repair_plan" in STUDIO
    assert "legacy.build_design_plan" in PLAN
    assert "command_guard.build_design_plan =" not in PLAN


def test_public_workflow_keeps_guarded_permission_and_transactional_apply() -> None:
    assert "_require_design_permission" in STUDIO
    assert "class ReviewedPreviewView" in STUDIO
    assert "This preview is obsolete" in STUDIO
    assert "server_design_apply_service as apply_service" in STUDIO
    assert "apply_service.preflight_plan" in STUDIO
    assert "apply_service.apply_prepared" in STUDIO
    assert "current != before" in APPLY
    assert "compensate_applied" in APPLY
    assert "_persist_rollback_snapshot" in STUDIO
