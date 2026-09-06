from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
PLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")
RULES = (ROOT / "stoney_verify/services/server_design_rule_service.py").read_text(encoding="utf-8")
GROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")

RETIRED = (
    ROOT / "stoney_verify/commands_ext/public_design_enhancements.py",
    ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_strict_layout_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_studio_command_guard.py",
)


def main() -> int:
    failures: list[str] = []
    for path in RETIRED:
        if path.exists():
            failures.append(f"retired design runtime shim still exists: {path.relative_to(ROOT)}")
    if "server_design_command_module_guard" in STARTUP:
        failures.append("startup registry still loads the retired design command guard")
    if 'allowed.add("design")' in GROUP or "commands_ext._ALLOWED_DANK_CHILDREN =" in GROUP:
        failures.append("native design registrar still mutates canonical registry state")
    if "majority.build_category_aware_options" not in PLAN:
        failures.append("native plan service lost category-aware planning")
    if "persist_separator_choice" not in RULES or "reset_all_overrides" not in RULES:
        failures.append("saved-rule service is missing separator/reset authority")
    if "__use_live_majority_layout" in PLAN:
        failures.append("retired runtime-magic design flag remains in plan service")
    if failures:
        print("DESIGN OWNERSHIP AUDIT FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS design ownership is native; retired startup shims absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
