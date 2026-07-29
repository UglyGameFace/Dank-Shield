from __future__ import annotations

from pathlib import Path


path = Path(__file__).resolve().parent / "_apply_ticket_claim_first_enforcement.py"
text = path.read_text(encoding="utf-8")
old = '''service = replace_once(
    service,
    ''' + "'''" + '''        row_before = await _ticket_row_for_channel_id(channel.id)\n        status_before = _ticket_status(row_before)\n\n        if status_before == "deleted":\n''' + "'''" + ''',
    ''' + "'''" + '''        row_before = await _ticket_row_for_channel_id(channel.id)\n        status_before = _ticket_status(row_before)\n\n        decision = await authorize_ticket_action(\n            channel_id=channel.id,\n            actor=closed_by,\n            action="close",\n            allow_requester_cancel=True,\n            system_action=closed_by is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n\n        if status_before == "deleted":\n''' + "'''" + ''',
    "close policy guard",
)
'''
new = '''service = replace_regex_once(
    service,
    r''' + "'''" + '''(async def mark_ticket_closed\\(.*?row_before = await _ticket_row_for_channel_id\\(channel.id\\)\\n        status_before = _ticket_status\\(row_before\\)\\n)\\n        if status_before == "deleted":''' + "'''" + ''',
    r''' + "'''" + '''\\1\n        decision = await authorize_ticket_action(\n            channel_id=channel.id,\n            actor=closed_by,\n            action="close",\n            allow_requester_cancel=True,\n            system_action=closed_by is None,\n            row=row_before,\n        )\n        if not decision.allowed:\n            return False\n\n        if status_before == "deleted":''' + "'''" + ''',
    "close policy guard",
)
'''
if old not in text:
    raise SystemExit("ambiguous close-policy materializer block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Anchored claim-first close policy patch to mark_ticket_closed().")
