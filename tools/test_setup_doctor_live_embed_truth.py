from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = (root / "stoney_verify/commands_ext/public_setup_solid.py").read_text()

failures: list[str] = []

markers = [
    "_setup_doctor_truth_filter",
    "_setup_doc_is_optional_control",
    "_setup_doc_is_layout_only",
    "_setup_doc_is_vc_only",
    "blockers, warnings, ok = _setup_doctor_truth_filter(cfg, blockers, warnings, ok)",
    'name="Truth Rules"',
    "Optional setup control role is not saved",
    "Layout/style cleanup:",
    "VC Verify is disabled/not configured",
]

for marker in markers:
    if marker not in src:
        failures.append(f"missing marker: {marker}")

if "next_action = f\"Fix this first: {_short(blockers[0], 220)}\"" not in src:
    failures.append("next action code changed; re-check sanitizer placement manually")

if failures:
    print("FAIL live setup doctor truth embed test")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS live setup doctor truth embed test")
