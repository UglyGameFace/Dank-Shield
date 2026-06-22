from __future__ import annotations

from pathlib import Path

src = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text()

failures: list[str] = []

required = [
    'if selected == "custom_setup":',
    "_open_custom_service_picker(",
    "This is the actual manual editor",
    'custom_id="dank_setup:custom_editor"',
    'setup_template_payload("custom_setup")',
]

for marker in required:
    if marker not in src:
        failures.append(f"missing marker: {marker}")

# The custom branch must happen before the generic saved-preview path.
custom_index = src.find('if selected == "custom_setup":')
saved_preview_index = src.find('embed.title = "✅ Setup Choice Saved"')
if custom_index == -1 or saved_preview_index == -1 or custom_index > saved_preview_index:
    failures.append("custom setup branch is not before generic saved-preview return")

if failures:
    print("FAIL custom setup opens editor")
    for item in failures:
        print(" -", item)
    raise SystemExit(1)

print("PASS custom setup opens editor")
