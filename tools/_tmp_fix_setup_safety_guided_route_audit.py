from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools/audit_setup_safety.py"
HELPER = Path(__file__).resolve()

source = TARGET.read_text(encoding="utf-8")
old = '''        custom_continue is None
        or "recommend._open_guided_setup(interaction)"
        not in custom_continue[2]
'''
new = '''        custom_continue is None
        or "recommend._open_guided_setup("
        not in custom_continue[2]
'''

count = source.count(old)
if count != 1:
    raise RuntimeError(
        "setup safety guided-route audit: expected exactly 1 stale match, "
        f"found {count}"
    )

updated = source.replace(old, new, 1)
compile(updated, str(TARGET), "exec")
TARGET.write_text(updated, encoding="utf-8")
HELPER.unlink()
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Setup safety audit now accepts the canonical Quick Setup route with keyword context.")
print("✅ The audit still requires CustomServiceModeView to route through recommend._open_guided_setup.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
