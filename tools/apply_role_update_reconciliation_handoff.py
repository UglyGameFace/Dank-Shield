#!/usr/bin/env python3
from __future__ import annotations

"""Physically route on_member_update role reconciliation out of events.py."""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SERVICE = ROOT / "stoney_verify" / "members_new" / "role_update_reconciliation_service.py"

ROLE_BLOCK_DELEGATE = '''            try:
                from .members_new.role_update_reconciliation_service import reconcile_member_role_update

                role_result = await reconcile_member_role_update(
                    before,
                    after,
                    now_utc=now_utc,
                    auto_uv_removal_ts=_AUTO_UV_REMOVAL_TS,
                    resolve_unverified_chat_channel=_resolve_unverified_chat_channel,
                    start_join_grace_timer=start_join_grace_then_kick_timer_for_member,
                )
                if role_result.suppress_further_processing:
                    try:
                        await _new_sync_member_safe(after, in_guild=True)
                    except Exception:
                        pass
                    return
                removed_unverified = bool(role_result.removed_unverified)
            except Exception as e:
                print(f"⚠️ role update reconciliation service error for member {getattr(after, 'id', 'unknown')}: {repr(e)}")

'''

REQUIRED_SERVICE_MARKERS = (
    "class RoleUpdateReconcileResult",
    "async def reconcile_member_role_update",
    "Auto-remove Unverified when safe access role is granted",
    "Auto-restore Unverified after member became roleless",
)

FORBIDDEN_EVENTS_MARKERS = (
    "UNVERIFIED_ROLE_ID or 0",
    "VERIFIED_ROLE_ID or 0",
    "RESIDENT_ROLE_ID or 0",
    "STONER_ROLE_ID or 0",
    "STAFF_ROLE_ID or 0",
    "Auto-remove Unverified when Verified is granted",
    "Auto-restore Unverified after member became roleless",
    "[ROLE-HEAL] Restored Unverified",
    "roleless auto-heal block error",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def die(message: str) -> None:
    print(f"❌ {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def verify_service_ready() -> None:
    service = read(SERVICE)
    missing = [marker for marker in REQUIRED_SERVICE_MARKERS if marker not in service]
    if missing:
        print("❌ role_update_reconciliation_service is not ready:")
        for marker in missing:
            print(" -", marker)
        raise SystemExit(1)


def replace_role_reconcile_block(text: str) -> tuple[str, bool]:
    start_marker = "            try:\n                uv_id = int(UNVERIFIED_ROLE_ID or 0)\n"
    end_marker = "            try:\n                strip_msg = await _mass_role_strip_if_needed(after)\n"
    start = text.find(start_marker)
    if start < 0:
        if "reconcile_member_role_update" in text:
            ok("events on_member_update role reconciliation delegate already applied")
            return text, False
        die(f"could not find role reconciliation start marker: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        die(f"could not find role reconciliation end marker: {end_marker!r}")
    return text[:start] + ROLE_BLOCK_DELEGATE + text[end:], True


def main() -> int:
    if not EVENTS.exists():
        die(f"missing {EVENTS}")
    if not SERVICE.exists():
        die(f"missing {SERVICE}")

    verify_service_ready()
    text = read(EVENTS)
    text, changed = replace_role_reconcile_block(text)

    offenders = [marker for marker in FORBIDDEN_EVENTS_MARKERS if marker in text]
    if offenders:
        print("❌ events.py still contains role update ownership markers:")
        for marker in offenders:
            print(" -", marker)
        return 1

    if changed:
        write(EVENTS, text)
        ok("updated stoney_verify/events.py role update reconciliation ownership")
    else:
        ok("events.py role update reconciliation delegate already present")

    py_compile.compile(str(EVENTS), doraise=True)
    py_compile.compile(str(SERVICE), doraise=True)
    ok("compiled events.py and role_update_reconciliation_service.py")

    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/apply_role_update_reconciliation_handoff.py")
    print("  python -m py_compile stoney_verify/events.py stoney_verify/members_new/role_update_reconciliation_service.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Physically hand off role update reconciliation"')
    print("  git fetch origin")
    print("  git rebase origin/main")
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
