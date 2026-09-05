from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUP = (ROOT / "stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
V2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
LEGACY = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
REGISTRY = (ROOT / "stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    if '@dank_group.command(name="design"' not in GROUP:
        failures.append("public_design_group does not own /dank design")
    if "public_design_studio_v2 as design" not in GROUP:
        failures.append("public registrar does not route to consolidated Studio")
    if "register_public_design_studio_command" in V2:
        failures.append("v2 still exposes a duplicate command registrar")
    if "register_public_design_studio_command" in LEGACY:
        failures.append("legacy Studio still exposes a duplicate command registrar")
    if '"public_design_group"' not in REGISTRY or '"design"' not in REGISTRY:
        failures.append("canonical commands_ext registry is missing design ownership")
    if 'allowed.add("design")' in GROUP:
        failures.append("registration still mutates allowed children")
    if failures:
        print("DESIGN NATIVE REGISTRATION FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("PASS one native /dank design registrar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
