from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
PLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")
STARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
REGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")

RETIRED = (
    ROOT / "stoney_verify/commands_ext/public_design_enhancements.py",
    ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_strict_layout_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_studio_command_guard.py",
)


def test_one_public_design_registration_owner() -> None:
    assert GROUP.count('@dank_group.command(name="design"') == 1
    assert "register_public_design_studio_command" not in V2
    assert "register_public_design_studio_command" not in LEGACY
    assert 'allowed.add("design")' not in GROUP
    assert "commands_ext._ALLOWED_DANK_CHILDREN =" not in GROUP
    assert '"public_design_group"' in REGISTRY
    assert '"design"' in REGISTRY


def test_retired_runtime_patch_design_modules_are_physically_absent() -> None:
    assert all(not path.exists() for path in RETIRED)
    assert "server_design_command_module_guard" not in STARTUP
    assert "server_design_majority_layout_guard" not in STARTUP
    assert "server_design_strict_layout_guard" not in STARTUP


def test_native_plan_has_no_retired_runtime_magic_flag() -> None:
    assert "__use_live_majority_layout" not in PLAN
    assert "majority.build_category_aware_options" in PLAN
    assert "majority.annotate_category_aware_plan_items" in PLAN


def test_separator_entry_uses_saved_authority_not_live_majority_guess() -> None:
    start = V2.index("async def separator_only")
    end = V2.index("async def back", start)
    block = V2[start:end]
    assert "effective_draft_separator" in block
    assert "_infer_live_majority_context" not in block


def test_historical_design_mutators_are_removed() -> None:
    assert not list((ROOT / "tools").glob("apply_dank_design_*.py"))
    assert not list((ROOT / "tools").glob("apply_p0_int_design_*.py"))
