from __future__ import annotations

from pathlib import Path

files = {
    "bridge": Path("stoney_verify/commands_ext/public_design_bridge.py").read_text(errors="ignore"),
    "recommend": Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text(errors="ignore"),
    "fresh": Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(errors="ignore"),
    "design_group": Path("stoney_verify/commands_ext/public_design_group.py").read_text(errors="ignore"),
}

bad = []

for marker in (
    "open_design_studio_from_setup",
    "_home_embed",
    "DesignHomeView",
    "fonts, separators, category frames",
):
    if marker not in files["bridge"]:
        bad.append(f"bridge missing {marker}")

for src_name in ("recommend", "fresh"):
    if "Server Design" not in files[src_name]:
        bad.append(f"{src_name} manage setup missing Server Design")
    if "public_design_bridge" not in files[src_name]:
        bad.append(f"{src_name} manage setup not wired to public_design_bridge")

if files["design_group"].count("strict_layout.apply()") > 1:
    bad.append("public_design_group still calls strict_layout.apply more than once")

if "TEMPORARY BRIDGE" not in files["design_group"]:
    bad.append("public_design_group does not document temporary design guard bridge")

if bad:
    print("FAIL design manage setup bridge")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS design manage setup bridge")
