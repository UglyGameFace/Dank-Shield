from __future__ import annotations

from pathlib import Path

src = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text()

failures: list[str] = []

required = [
    "Custom Setup — Service Switches",
    "Turn each service **ON or OFF**",
    "Current ON/OFF State",
    'label=f"{label}: {state_text}"',
    'state_text = "ON ✅" if selected else "OFF ⬜"',
    'custom_id=f"dank_setup_custom_toggle:{key}"',
    "Set **{self.short_label}** to **",
]

for marker in required:
    if marker not in src:
        failures.append(f"missing clear toggle marker: {marker}")

for old in ("Turn OFF:", "Turn ON:", '"SpamGuard service"'):
    if old in src:
        failures.append(f"old confusing toggle text still present: {old}")

if failures:
    print("FAIL custom setup clear toggles")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS custom setup clear toggles")
