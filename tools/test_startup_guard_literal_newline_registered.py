from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
startup_init = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
sitecustomize = (ROOT / "sitecustomize.py").read_text(encoding="utf-8")
usercustomize = (ROOT / "usercustomize.py").read_text(encoding="utf-8")

bad: list[str] = []

if "LEGACY_DORMANT_STARTUP_GUARDS" not in startup_init:
    bad.append("historical startup inventory is not explicitly marked dormant")

for forbidden in (
    "def load_startup_guards(",
    "load_all_startup_guards =",
    "_LOADED: Dict[str, ModuleType]",
    "_ERRORS: Dict[str, BaseException]",
):
    if forbidden in startup_init:
        bad.append(f"retired bulk-loader machinery still present: {forbidden}")

if "stoney_verify.startup_guards.embed_literal_newline_guard" not in startup_init:
    bad.append("historical literal-newline guard record disappeared unexpectedly")

if "load_all_startup_guards" in sitecustomize or "load_startup_guards" in sitecustomize:
    bad.append("sitecustomize still exposes bulk-loader compatibility")

if "panel_menu_retry_guard" in usercustomize or "panel_menu_" in usercustomize:
    bad.append("usercustomize still tries to install the retired panel retry guard")

if (ROOT / "stoney_verify/startup_guards/panel_menu_retry_guard.py").exists():
    bad.append("retired panel_menu_retry_guard.py still exists")

if bad:
    print("FAIL startup guard loader retirement")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS startup guard loader retirement")
