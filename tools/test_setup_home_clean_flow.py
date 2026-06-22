from __future__ import annotations

from pathlib import Path

fresh = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text()
recommend = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text()

failures: list[str] = []

required_markers = [
    "Dank Shield Setup Hub",
    "clean wizard flow",
    "Custom setup opens the service switches after you choose it.",
    "No duplicate shortcuts",
]

for marker in required_markers:
    if marker not in fresh and marker not in recommend:
        failures.append(f"missing clean home marker: {marker}")

if 'custom_id="dank_setup:custom_editor"' in recommend:
    failures.append("duplicate Custom Setup shortcut still exists on ProductSetupHomeView")

if "Setup Choices" in fresh or "Product Rule" in fresh:
    failures.append("fresh setup home still contains the old giant setup wall fields")

if "What the buttons mean" in recommend:
    failures.append("product setup home still contains old long button explanation block")

if 'custom_id="dank_setup_choice:custom"' not in fresh:
    failures.append("Custom setup choice was removed from the setup-type choice flow")

if "_open_custom_service_picker(interaction)" not in fresh:
    failures.append("Custom setup no longer opens the service picker from the choice flow")

if failures:
    print("FAIL setup home clean flow")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS setup home clean flow")
