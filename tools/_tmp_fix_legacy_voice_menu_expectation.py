from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tests/test_setup_advanced_options_behavior.py"
HELPER = Path(__file__).resolve()

source = TARGET.read_text(encoding="utf-8")
old = '''    assert labels(recommend.AdvancedVerificationView()) == {\n        "Choose Core Modules",\n        "Roles & Channels",\n        "Timers & Rules",\n        "Back to All Features",\n        "Setup Home",\n        "Close",\n    }\n'''
new = '''    assert labels(recommend.AdvancedVerificationView()) == {\n        "Choose Core Modules",\n        "Roles & Channels",\n        "Timers & Rules",\n        "Review Old Voice Items",\n        "Back to All Features",\n        "Setup Home",\n        "Close",\n    }\n'''

count = source.count(old)
if count != 1:
    raise RuntimeError(
        "Verification submenu expectation: expected exactly 1 stale block, "
        f"found {count}"
    )

updated = source.replace(old, new, 1)
compile(updated, str(TARGET), "exec")
TARGET.write_text(updated, encoding="utf-8")
HELPER.unlink()
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Verification submenu regression now includes Review Old Voice Items.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
