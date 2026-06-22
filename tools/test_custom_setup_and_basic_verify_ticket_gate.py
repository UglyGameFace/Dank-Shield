from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

solid = (ROOT / "stoney_verify/commands_ext/public_setup_solid.py").read_text(errors="ignore")
flow = (ROOT / "stoney_verify/startup_guards/unverified_ticket_panel_flow.py").read_text(errors="ignore")
fresh = (ROOT / "stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(errors="ignore")

failures: list[str] = []

for marker in (
    "stoney_solid:dashboard_custom_setup",
    "_open_custom_service_picker",
):
    if marker not in solid:
        failures.append(f"setup home missing custom setup marker: {marker}")

if "if choice.key == \"custom_setup\":" not in fresh or "_open_custom_service_picker(interaction)" not in fresh:
    failures.append("fresh choice custom setup does not open the custom service picker")

for marker in (
    "def _should_auto_route_unverified_ticket",
    "reason=basic_verify_or_no_advanced_verify",
    "public ticket click allowed through normal support path",
    "skipped verification UI post in ticket",
):
    if marker not in flow:
        failures.append(f"unverified support ticket gate missing marker: {marker}")

if failures:
    print("FAIL custom setup/basic verify ticket gate")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS custom setup/basic verify ticket gate")
