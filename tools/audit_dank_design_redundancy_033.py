from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
PLAN = (ROOT / "stoney_verify/services/server_design_plan_service.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "stoney_verify/commands_ext/public_design_bridge.py").read_text(encoding="utf-8")
STARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
REGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")

RETIRED_RUNTIME = (
    ROOT / "stoney_verify/commands_ext/public_design_enhancements.py",
    ROOT / "stoney_verify/startup_guards/server_design_command_module_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_strict_layout_guard.py",
    ROOT / "stoney_verify/startup_guards/server_design_studio_command_guard.py",
)


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    failures: list[str] = []

    for path in RETIRED_RUNTIME:
        if path.exists():
            failures.append(f"retired runtime owner still exists: {path.relative_to(ROOT)}")

    historical = sorted((ROOT / "tools").glob("apply_dank_design_*.py"))
    historical += sorted((ROOT / "tools").glob("apply_p0_int_design_*.py"))
    if historical:
        failures.append("historical design mutation scripts still exist: " + ", ".join(str(path.relative_to(ROOT)) for path in historical))

    if GROUP.count('@dank_group.command(name="design"') != 1:
        failures.append("/dank design must have exactly one public command decorator")
    if "public_design_studio_v2 as design" not in GROUP:
        failures.append("public design group is not routed to the consolidated V2 Studio")
    if "register_public_design_studio_command" in V2 or "register_public_design_studio_command" in LEGACY:
        failures.append("duplicate Studio command registrar remains")
    if "activate_public_design_enhancements" in GROUP:
        failures.append("public registration still activates a compatibility enhancement layer")

    for name in (
        "server_design_command_module_guard",
        "server_design_majority_layout_guard",
        "server_design_strict_layout_guard",
        "server_design_studio_command_guard",
    ):
        if name in STARTUP:
            failures.append(f"startup still references retired design guard: {name}")

    if "__use_live_majority_layout" in PLAN:
        failures.append("retired live-majority runtime magic flag remains in the native planner")
    if "__use_live_majority_layout" in LEGACY:
        failures.append("retired live-majority runtime magic flag remains in the legacy backend")
    if '@discord.ui.button(label="Review Name Drift"' in LEGACY:
        failures.append("retired mashed legacy public home still exists")
    if 'custom_id="dank_design:apply"' in LEGACY:
        failures.append("retired independent legacy Apply owner still exists")
    if "command_guard.build_design_plan =" in PLAN:
        failures.append("native planner still contains runtime build_design_plan reassignment")
    if "majority.build_category_aware_options" not in PLAN or "majority.annotate_category_aware_plan_items" not in PLAN:
        failures.append("native category-aware planning ownership is missing")

    for label in (
        "Design Entire Server",
        "Edit One Category / Channel",
        "Fix Inconsistent Names",
        "Saved Rules & Protection",
        "Undo Last Apply",
    ):
        if f'label="{label}"' not in V2:
            failures.append(f"consolidated five-workflow home is missing: {label}")

    if "class ReviewedPreviewView" not in V2 or "Apply Reviewed Changes" not in V2:
        failures.append("consolidated reviewed-apply owner is missing")
    if "legacy.DesignPreviewView = ReviewedPreviewView" not in V2:
        failures.append("legacy sub-editors are not explicitly routed to the consolidated Apply owner")
    if "legacy.DesignHomeView = DesignHomeView" not in V2:
        failures.append("legacy sub-editor Back paths are not explicitly routed to the consolidated home")

    # The compatibility bridge is allowed to redirect legacy UI symbols only.
    # It must never reassign planner/service/registration functions.
    forbidden_bridge_assignments = (
        "legacy.build_design_plan =",
        "legacy.register_public_design_studio_command =",
        "legacy._already_semantically_matches_design =",
    )
    for marker in forbidden_bridge_assignments:
        if marker in V2:
            failures.append(f"compatibility bridge owns forbidden runtime behavior: {marker}")

    if "public_design_studio_v2 as design" not in BRIDGE:
        failures.append("Setup bridge does not converge on the consolidated V2 Studio")
    if '"public_design_group"' not in REGISTRY or '"design"' not in REGISTRY:
        failures.append("declarative command registry is missing the design owner")

    if failures:
        print("DANK DESIGN REDUNDANCY AUDIT FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("DANK DESIGN REDUNDANCY AUDIT OK")
    print(
        "public_registrar=1 retired_runtime=0 historical_mutators=0 "
        "runtime_magic=0 native_plan=yes consolidated_apply=yes compatibility_boundary=ui_only"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
