#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

cd ~/Dank-Shield || exit 1

BRANCH="fix/setup-navigation-ux-overhaul"
SOURCE="tools/apply_setup_navigation_ux_overhaul.sh"
TMP_ROOT="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/dank-setup-overhaul-v3"
CORRECTED="$TMP_ROOT/apply_corrected.sh"
PREFLIGHT_HOME="$TMP_ROOT/home"
PREFLIGHT_REPO="$PREFLIGHT_HOME/Dank-Shield"

# A failed earlier run may only have changed this one file before stopping.
DIRTY="$(git status --porcelain)"
if [[ -n "$DIRTY" ]]; then
  UNEXPECTED="$(printf '%s\n' "$DIRTY" | grep -vE '^ M stoney_verify/setup_service_state\.py$' || true)"
  if [[ -n "$UNEXPECTED" ]]; then
    echo "ERROR: unexpected local changes exist; nothing was reset:"
    printf '%s\n' "$DIRTY"
    exit 1
  fi
  git restore -- stoney_verify/setup_service_state.py
  echo "✅ Removed the partial change left by the failed run"
fi

rm -rf "$TMP_ROOT"
mkdir -p "$TMP_ROOT" "$PREFLIGHT_HOME"

python - "$SOURCE" "$CORRECTED" <<'PY'
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
        "writer_marker = '''def upsert_guild_config_sync(\n'''",
        'writer_marker = "def upsert_guild_config_sync"',
        "writer function marker",
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

target.write_text(text, encoding="utf-8")
target.chmod(0o755)
print("✅ Built corrected setup-overhaul patch")
PY

# Validate every marker and compile step in a disposable clone first.
git clone --quiet --branch "$BRANCH" --single-branch \
  https://github.com/UglyGameFace/Dank-Shield.git "$PREFLIGHT_REPO"

PREFLIGHT_SCRIPT="$TMP_ROOT/apply_preflight.sh"
python - "$CORRECTED" "$PREFLIGHT_SCRIPT" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")
source = source.replace(
    'git commit -m "Unify setup state navigation and completion"',
    'echo "✅ Preflight would commit setup overhaul"',
    1,
)
source = source.replace(
    'git push origin fix/setup-navigation-ux-overhaul',
    'echo "✅ Preflight would push setup overhaul"',
    1,
)
Path(sys.argv[2]).write_text(source, encoding="utf-8")
Path(sys.argv[2]).chmod(0o755)
PY

echo
echo "=== DISPOSABLE PREFLIGHT ==="
HOME="$PREFLIGHT_HOME" bash "$PREFLIGHT_SCRIPT"

echo
echo "=== APPLY TO REAL CHECKOUT ==="
bash "$CORRECTED"

echo
echo "✅ Setup navigation overhaul applied, compiled, committed, and pushed"
