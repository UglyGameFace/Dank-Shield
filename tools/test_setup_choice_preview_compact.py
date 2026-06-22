from __future__ import annotations

from pathlib import Path

src = Path("stoney_verify/setup_new/templates.py").read_text()

failures = []

required = [
    "You do not need to read a wall of text",
    "Previewing {selected.label}",
    "After you press **Use This Setup**",
    "_compact_choice_line",
]

for marker in required:
    if marker not in src:
        failures.append(f"missing compact UX marker: {marker}")

bad_markers = [
    "for choice in SETUP_TEMPLATE_CHOICES:\n        marker = \"✅ Selected\"",
    "value = (\n            f\"{choice.short_description}",
]

for marker in bad_markers:
    if marker in src:
        failures.append(f"old wall-of-text renderer still present: {marker[:60]}")

if failures:
    print("FAIL setup choice preview compact test")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS setup choice preview compact test")
