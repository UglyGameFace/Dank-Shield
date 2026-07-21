#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

cd ~/Dank-Shield || exit 1

SOURCE="tools/apply_setup_navigation_ux_overhaul.sh"
TEMP="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/apply_setup_navigation_ux_overhaul_v2.sh"

python - "$SOURCE" "$TEMP" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")

replacements = [
    (
        "completion_marker = '''async def mark_setup_completed(\n'''",
        'completion_marker = "async def mark_setup_completed"',
        "completion marker",
    ),
    (
        'custom_io + "\\n\\n_CUSTOM_SERVICE_FLAG_KEYS ="',
        "custom_io",
        "custom-service end marker duplication",
    ),
    (
        'setup_doc_features + "\\n\\n_LAYOUT_ONLY_PHRASES ="',
        "setup_doc_features",
        "setup-feature end marker duplication",
    ),
    (
        'launch_view + "\\n\\ndef _patch() -> None:"',
        "launch_view",
        "launch-view end marker duplication",
    ),
    (
        'nav_view + "\\n\\nBackToSetupView = SetupNavView"',
        "nav_view",
        "navigation end marker duplication",
    ),
]

for old, new, label in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"ERROR: expected exactly one {label}; found {count}"
        )
    text = text.replace(old, new, 1)

target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(text, encoding="utf-8")
target.chmod(0o755)

print("✅ Corrected all known patch-script markers")
PY

bash "$TEMP"
