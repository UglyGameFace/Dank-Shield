from __future__ import annotations

from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = Path(__file__).resolve()
TARGET = ROOT / "tools/_tmp_finish_setup_parent_docs_audit.py"

source = TARGET.read_text(encoding="utf-8")

old = '''"""`/dank setup -> Safety & Repair.\nIt extends the older guard implementation with exact-name discovery and clearer\nfix boundaries, without blindly overwriting unrelated server channels.\n"""'''
new = '''"""This is the product-level entrypoint used by /dank setup → Safety & Repair.\nIt extends the older guard implementation with exact-name discovery and clearer\nfix boundaries, without blindly overwriting unrelated server channels.\n"""'''

count = source.count(old)
if count != 1:
    raise RuntimeError(
        "retry guard: expected exactly one stale permission-repair docstring "
        f"pattern in the staging helper, found {count}"
    )

TARGET.write_text(source.replace(old, new, 1), encoding="utf-8")
print("✅ Corrected the stale staging-helper docstring guard.")

# The target helper validates all remaining replacements before writing its
# final files, deletes itself on success, and runs git diff --check.
runpy.run_path(str(TARGET), run_name="__main__")

# Only reached after the target helper completed successfully.
WRAPPER.unlink()
print("✅ Retry wrapper removed from the working tree.")
