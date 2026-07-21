from __future__ import annotations

from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools/_tmp_finish_setup_parent_docs_audit.py"
PRIOR_RETRY = ROOT / "tools/_tmp_retry_setup_parent_docs_audit.py"
THIS = Path(__file__).resolve()


text = TARGET.read_text(encoding="utf-8")

# The original helper was authored against an older ASCII-arrow docstring.
old_doc = '''    """`/dank setup -> Safety & Repair.\nIt extends the older guard implementation with exact-name discovery and clearer\nfix boundaries, without blindly overwriting unrelated server channels.\n""",'''
new_doc = '''    """This is the product-level entrypoint used by /dank setup → Safety & Repair.\nIt extends the older guard implementation with exact-name discovery and clearer\nfix boundaries, without blindly overwriting unrelated server channels.\n""",'''

if old_doc in text:
    text = text.replace(old_doc, new_doc, 1)
elif new_doc not in text:
    raise RuntimeError("STOP: could not locate either expected permission-repair docstring guard")

# Three replacement targets are intentionally present twice. The helper handles
# the first occurrence at one step and the remaining occurrence at a later step.
old_replace_once = '''def replace_once(text: str, old: str, new: str, label: str) -> str:\n    count = text.count(old)\n    if count != 1:\n        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")\n    return text.replace(old, new, 1)\n'''
new_replace_once = '''def replace_once(text: str, old: str, new: str, label: str) -> str:\n    count = text.count(old)\n    first_of_two_labels = {\n        "permission preview preserves parent",\n        "bot access wrapper expectation",\n        "modlog control label expectation",\n    }\n    if label in first_of_two_labels and count == 2:\n        return text.replace(old, new, 1)\n    if count != 1:\n        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")\n    return text.replace(old, new, 1)\n'''

if old_replace_once in text:
    text = text.replace(old_replace_once, new_replace_once, 1)
elif new_replace_once not in text:
    raise RuntimeError("STOP: could not locate expected replace_once helper")

TARGET.write_text(text, encoding="utf-8")
print("✅ Corrected the stale docstring guard.")
print("✅ Hardened all three intentional duplicate replacement cases.")

# The target helper performs all production/test/doc writes only after every
# guarded transformation and Python compile check succeeds.
runpy.run_path(str(TARGET), run_name="__main__")

# The target deletes itself on success. Remove both retry wrappers too so no
# temporary staging scaffolding survives in the final commit.
if PRIOR_RETRY.exists():
    PRIOR_RETRY.unlink()
if THIS.exists():
    THIS.unlink()

print("✅ All temporary setup staging helpers removed.")
