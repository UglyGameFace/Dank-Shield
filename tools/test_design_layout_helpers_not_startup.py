from __future__ import annotations

from pathlib import Path

bad = []

startup = Path("stoney_verify/startup_guards/__init__.py").read_text(errors="ignore")
strict = Path("stoney_verify/startup_guards/server_design_strict_layout_guard.py").read_text(errors="ignore")
majority_guard = Path("stoney_verify/startup_guards/server_design_majority_layout_guard.py").read_text(errors="ignore")
group = Path("stoney_verify/commands_ext/public_design_group.py").read_text(errors="ignore")
enh = Path("stoney_verify/commands_ext/public_design_enhancements.py").read_text(errors="ignore")
plan = Path("stoney_verify/services/server_design_plan_service.py").read_text(errors="ignore")
majority_service = Path("stoney_verify/services/server_design_majority_layout.py").read_text(errors="ignore")
native_studio = Path("stoney_verify/services/server_design_studio.py").read_text(errors="ignore")

for module in (
    "stoney_verify.startup_guards.server_design_strict_layout_guard",
    "stoney_verify.startup_guards.server_design_majority_layout_guard",
):
    if module in startup:
        bad.append(f"{module} still loads during startup")

for name, src in (("strict", strict), ("majority", majority_guard)):
    if "\napply()\n" in src:
        bad.append(f"{name} legacy layout guard still applies at import time")

if "server_design_strict_layout_guard" in group or "server_design_majority_layout_guard" in group:
    bad.append("public_design_group directly references a layout startup guard")

if "activate_public_design_enhancements" in group:
    bad.append("public_design_group still activates the historical enhancement patch layer")

if "server_design_strict_layout_guard" in enh or "server_design_majority_layout_guard" in enh:
    bad.append("public_design_enhancements still imports a design startup guard")

if "public_design_studio_v2 as design" not in group:
    bad.append("public_design_group is not routed to consolidated Studio")

for marker in (
    "majority.build_category_aware_options",
    "majority.annotate_category_aware_plan_items",
    "repair_confidence.evaluate_repair_plan",
    "legacy.build_design_plan",
):
    if marker not in plan:
        bad.append(f"native design plan service missing {marker}")

if "activate_public_design_enhancements" not in enh:
    bad.append("public_design_enhancements compatibility entry point disappeared")

# The native parser already stops before known separators. Majority analysis may
# keep the old helper name for compatibility, but it must never replace the
# parser function at runtime again.
if "studio._strip_leading_icon =" in majority_service:
    bad.append("majority service still monkey-patches native separator parsing")
if "Deprecated compatibility no-op" not in majority_service:
    bad.append("separator parser compatibility helper is not explicitly inert")
if "if any(remaining.startswith(sep) for sep in _all_separator_values()):" not in native_studio:
    bad.append("native Studio parser no longer owns separator-safe icon parsing")

if bad:
    print("FAIL design layout helpers not startup")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS design layout helpers not startup")
