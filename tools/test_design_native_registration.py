from __future__ import annotations

from pathlib import Path

bad = []

native = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(errors="ignore")
group = Path("stoney_verify/commands_ext/public_design_group.py").read_text(errors="ignore")
bridge = Path("stoney_verify/commands_ext/public_design_bridge.py").read_text(errors="ignore")
startup = Path("stoney_verify/startup_guards/__init__.py").read_text(errors="ignore")
shim = Path("stoney_verify/startup_guards/server_design_studio_command_guard.py").read_text(errors="ignore")

if "register_public_design_studio_command" not in native:
    bad.append("native design module missing register_public_design_studio_command")

if "\napply()\n" in native:
    bad.append("native design module still calls apply() at import time")

if "server_design_studio_command_guard as design" in group:
    bad.append("public_design_group still imports design command guard")

if "public_design_studio as design" not in group:
    bad.append("public_design_group does not import native design module")

if "public_design_studio as design" not in bridge:
    bad.append("setup design bridge does not import native design module")

if "stoney_verify.startup_guards.server_design_studio_command_guard" in startup:
    bad.append("startup registry still loads server_design_studio_command_guard")

if "This file must not call apply() at import time." not in shim:
    bad.append("old design guard was not rewritten as compatibility shim")

if "\napply()\n" in shim:
    bad.append("old design guard shim still calls apply() at import time")

if bad:
    print("FAIL design native registration")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS design native registration")
