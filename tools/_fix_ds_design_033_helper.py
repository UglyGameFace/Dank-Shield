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
text = text.replace(old, new, 1)

start_marker = '# Dedicated workflow must compile active owners, not files we intentionally removed.\n'
end_marker = 'write(workflow_rel, workflow)\n'
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise RuntimeError("could not locate temporary helper workflow-edit block")
end += len(end_marker)
text = text[:start] + text[end:]

final_marker = 'print(f"Removed historical design migration scripts: {len(removed_migrations)}")\n'
if text.count(final_marker) != 1:
    raise RuntimeError("could not locate cleanup helper final marker")
text = text.replace(
    final_marker,
    final_marker + 'Path(__file__).unlink()\nprint("Removed temporary DS-DESIGN-033 cleanup helper")\n',
    1,
)

path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
print("Corrected cleanup guard, respected workflow permission boundary, and scheduled helper self-removal")
