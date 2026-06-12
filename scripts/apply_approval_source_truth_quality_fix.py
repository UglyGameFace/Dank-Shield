#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import sys
from pathlib import Path

ROOT = Path.cwd()
TARGET = ROOT / "stoney_verify" / "verification_new" / "service.py"


def die(msg: str) -> None:
    print(f"❌ {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"✅ {msg}")


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    if old not in text:
        if new in text:
            ok(f"{label} already applied")
            return text, False
        die(f"Missing block: {label}")
    if text.count(old) != 1:
        die(f"Expected one match for {label}, found {text.count(old)}")
    return text.replace(old, new, 1), True


def main() -> None:
    if not TARGET.exists():
        die(f"Missing {TARGET}")

    text = TARGET.read_text(encoding="utf-8")
    original = text

    old = '''        staff_id = str(staff_member.id)
        staff_name = _member_display_name(staff_member) or str(staff_member)

        member_patch: Dict[str, Any] = {
'''
    new = '''        staff_id = str(staff_member.id)
        staff_name = _member_display_name(staff_member) or str(staff_member)
        truth_meta: Dict[str, Any] = {
            "entry_truth_quality": "confirmed",
            "entry_confidence": 95 if ticket_channel_id else 90,
            "entry_quality_reason": "Verified by explicit staff workflow.",
            "entry_conflict": False,
        }

        member_patch: Dict[str, Any] = {
'''
    text, _ = replace_once(text, old, new, "compute truth metadata")

    old = '''            "source_ticket_id": ticket_channel_id,
            "verification_ticket_id": ticket_channel_id,
        }
'''
    new = '''            "source_ticket_id": ticket_channel_id,
            "verification_ticket_id": ticket_channel_id,
            **truth_meta,
        }
'''
    text, _ = replace_once(text, old, new, "guild_members truth metadata")

    old = '''        join_patch: Dict[str, Any] = {
            "approved_by": staff_id,
            "approved_by_name": staff_name,
            "join_note": str(decision_text),
            "entry_method": entry_method,
            "verification_source": verification_source,
            "source_ticket_id": ticket_channel_id,
        }
'''
    new = '''        join_patch: Dict[str, Any] = {
            "approved_by": staff_id,
            "approved_by_name": staff_name,
            "join_note": str(decision_text),
            "entry_method": entry_method,
            "verification_source": verification_source,
            "source_ticket_id": ticket_channel_id,
            **truth_meta,
        }
'''
    text, _ = replace_once(text, old, new, "member_joins truth metadata")

    old = '''        metadata: Dict[str, Any] = {
            "decision": str(decision_text),
            "decision_kind": decision_kind,
            "verification_source": verification_source,
            "entry_method": entry_method,
            "source_ticket_id": ticket_channel_id,
            "channel_id": ticket_channel_id,
            "channel_name": channel.name if isinstance(channel, discord.TextChannel) else None,
        }
'''
    new = '''        metadata: Dict[str, Any] = {
            "decision": str(decision_text),
            "decision_kind": decision_kind,
            "verification_source": verification_source,
            "entry_method": entry_method,
            "source_ticket_id": ticket_channel_id,
            "channel_id": ticket_channel_id,
            "channel_name": channel.name if isinstance(channel, discord.TextChannel) else None,
            **truth_meta,
        }
'''
    text, _ = replace_once(text, old, new, "member_events truth metadata")

    if text != original:
        TARGET.write_text(text, encoding="utf-8")
        ok(f"Updated {TARGET}")
    else:
        ok("No changes needed")

    py_compile.compile(str(TARGET), doraise=True)
    ok(f"Compiled {TARGET}")

    print("\nNext commands:")
    print("  git diff -- stoney_verify/verification_new/service.py")
    print("  git add stoney_verify/verification_new/service.py")
    print('  git commit -m "Track approval source truth quality"')
    print("  git push")


if __name__ == "__main__":
    main()
