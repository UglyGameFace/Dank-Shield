from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "stoney_verify/commands_ext/public_setup_solid.py").read_text()

required = [
    "normalize_setup_health",
    "truth_rules_text",
    "doctor = normalize_setup_health",
    'name="Truth Rules"',
]

missing = [item for item in required if item not in src]
if missing:
    print("FAIL live setup check canonical wiring")
    for item in missing:
        print(" - missing", item)
    raise SystemExit(1)

print("PASS live setup check canonical wiring")
