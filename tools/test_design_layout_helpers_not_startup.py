from __future__ import annotations

from pathlib import Path

bad = []

startup = Path("stoney_verify/startup_guards/__init__.py").read_text(errors="ignore")
strict = Path("stoney_verify/startup_guards/server_design_strict_layout_guard.py").read_text(errors="ignore")
majority = Path("stoney_verify/startup_guards/server_design_majority_layout_guard.py").read_text(errors="ignore")
group = Path("stoney_verify/commands_ext/public_design_group.py").read_text(errors="ignore")
enh = Path("stoney_verify/commands_ext/public_design_enhancements.py").read_text(errors="ignore")

for module in (
    "stoney_verify.startup_guards.server_design_strict_layout_guard",
    "stoney_verify.startup_guards.server_design_majority_layout_guard",
):
    if module in startup:
        bad.append(f"{module} still loads during startup")

for name, src in (("strict", strict), ("majority", majority)):
    if "\napply()\n" in src:
        bad.append(f"{name} layout guard still applies at import time")

if "server_design_strict_layout_guard as strict_layout" in group:
    bad.append("public_design_group still imports strict layout guard directly")

if "server_design_majority_layout_guard as majority_layout" in group:
    bad.append("public_design_group still imports majority layout guard directly")

if "activate_public_design_enhancements" not in group:
    bad.append("public_design_group does not activate native design enhancements")

if "stoney_verify.commands_ext.public_design_studio" not in majority:
    bad.append("majority layout helper does not target native design module")

if "stoney_verify.commands_ext.public_design_studio" not in strict:
    bad.append("strict layout helper does not target native design module")

if "activate_public_design_enhancements" not in enh:
    bad.append("public_design_enhancements missing activation function")

if bad:
    print("FAIL design layout helpers not startup")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS design layout helpers not startup")
