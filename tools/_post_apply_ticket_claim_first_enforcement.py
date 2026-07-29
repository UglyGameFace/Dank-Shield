from __future__ import annotations

import re
from pathlib import Path


root = Path(__file__).resolve().parents[1]
service_path = root / "stoney_verify/tickets_new/service.py"
text = service_path.read_text(encoding="utf-8")
pattern = re.compile(
    r'''\n\ndef _actor_is_elevated_staff\(actor: Optional\[discord\.Member \| discord\.User\]\) -> bool:\n.*?\n    return False\n\n\ndef _ticket_archive_category_id''',
    re.S,
)
updated, count = pattern.subn("\n\ndef _ticket_archive_category_id", text, count=1)
if count != 1:
    raise SystemExit(f"obsolete elevated-staff helper not found: {count}")
service_path.write_text(updated, encoding="utf-8")
print("Removed obsolete elevated-staff lifecycle bypass helper.")
