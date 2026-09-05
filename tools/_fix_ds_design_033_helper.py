from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("_apply_ds_design_033_ux_patch.py")
text = path.read_text(encoding="utf-8")
old = '''if count != 2:
    raise RuntimeError(f"smart-auto-detect magic marker count: expected 2, found {count}")
'''
new = '''if count != 1:
    raise RuntimeError(f"smart-auto-detect magic marker count: expected 1, found {count}")
'''
if text.count(old) != 1:
    raise RuntimeError(f"expected exactly one stale magic-count assertion, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
Path(__file__).unlink()
print("Corrected DS-DESIGN-033 cleanup helper guard and removed temporary fixer")
