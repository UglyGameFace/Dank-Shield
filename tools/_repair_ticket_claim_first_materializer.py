from __future__ import annotations

import re
from pathlib import Path


path = Path(__file__).resolve().parent / "_apply_ticket_claim_first_enforcement.py"
text = path.read_text(encoding="utf-8")
pattern = re.compile(
    r'''service = replace_once\(\n'''
    r'''    service,\n'''
    r'''    ''' + "'''" + r'''        row_before = await _ticket_row_for_channel_id\(channel\.id\).*?'''
    r'''    "close policy guard",\n'''
    r'''\)\n''',
    re.S,
)
replacement = '''service = replace_regex_once(
    service,
    r''' + "'''" + '''(async def mark_ticket_closed\\(.*?row_before = await _ticket_row_for_channel_id\\(channel.id\\)\\n        status_before = _ticket_status\\(row_before\\)\\n)\\n        if status_before == "deleted":''' + "'''" + ''',
    r''' + "'''" + '''\\1\n        decision = await authorize_ticket_action(\n            channel_id=channel.id,\n            actor=closed_by,\n            action="close",\n            allow_requester_cancel=True,\n            system_action=closed_by is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n\n        if status_before == "deleted":''' + "'''" + ''',
    "close policy guard",
)
'''
updated, count = pattern.subn(lambda _match: replacement, text, count=1)
if count != 1:
    raise SystemExit(f"ambiguous close-policy materializer block not found: {count}")

write_marker = 'service_path.write_text(service, encoding="utf-8")\n'
removal = '''service = replace_regex_once(
    service,
    r''' + "'''" + '''\\n\\ndef _actor_is_elevated_staff\\(actor: Optional\\[discord\\.Member \\| discord\\.User\\]\\) -> bool:\\n.*?\\n    return False\\n\\n\\ndef _ticket_archive_category_id''' + "'''" + ''',
    "\\n\\ndef _ticket_archive_category_id",
    "remove obsolete elevated staff helper",
)
'''
if write_marker not in updated:
    raise SystemExit("service write marker not found")
updated = updated.replace(write_marker, removal + write_marker, 1)

path.write_text(updated, encoding="utf-8")
print("Anchored close policy patch and removed obsolete elevated-staff helper.")
