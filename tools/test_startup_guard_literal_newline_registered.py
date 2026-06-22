from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text()

bad = []

if "_STARTUP_GUARDS" not in src:
    bad.append("_STARTUP_GUARDS tuple missing")

if "stoney_verify.startup_guards.embed_literal_newline_guard" not in src:
    bad.append("embed_literal_newline_guard is not registered in _STARTUP_GUARDS")

if bad:
    print("FAIL literal newline startup registration")
    for item in bad:
        print(" -", item)
    raise SystemExit(1)

print("PASS literal newline startup registration")
