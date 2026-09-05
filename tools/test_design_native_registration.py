from __future__ import annotations

from pathlib import Path

bad = []

legacy = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(errors="ignore")
native = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text(errors="ignore")
group = Path("stoney_verify/commands_ext/public_design_group.py").read_text(errors="ignore")
bridge = Path("stoney_verify/commands_ext/public_design_bridge.py").read_text(errors="ignore")
startup = Path("stoney_verify/startup_guards/__init__.py").read_text(errors="ignore")
shim = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text(errors="ignore")

if "register_public_design_studio_command" not in native:
    bad.append("consolidated design owner missing compatibility registrar")

if "\napply()\n" in native:
    bad.append("consolidated design owner calls apply() at import time")

if "server_design_studio_command_guard as design" in group:
    bad.append("public_design_group still imports design command guard")

if "public_design_studio_v2 as design" not in group:
    bad.append("public_design_group does not import consolidated design owner")

if "public_design_studio_v2 as design" not in bridge:
    bad.append("setup design bridge does not import consolidated design owner")

if "stoney_verify.startup_guards.server_design_studio_command_guard" in startup:
    bad.append("startup registry still loads deprecated design command shim")

if "Deprecated import-only compatibility shim" not in shim:
    bad.append("old design command guard is not clearly marked compatibility-only")

if "from stoney_verify.commands_ext.public_design_studio_v2 import" not in shim:
    bad.append("old design command shim does not delegate to consolidated owner")

if "\napply()\n" in shim:
    bad.append("old design command shim still calls apply() at import time")

if "async def build_design_plan" not in legacy:
    bad.append("legacy compatibility backend lost mature plan primitive unexpectedly")

if bad:
    print("FAIL design native registration")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS design native registration")
