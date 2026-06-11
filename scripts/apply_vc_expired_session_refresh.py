#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import sys
from pathlib import Path


ROOT = Path.cwd()

FILES = {
    "vc_sessions": ROOT / "stoney_verify" / "vc_sessions.py",
    "vc_verify": ROOT / "stoney_verify" / "vc_verify.py",
}


def die(message: str) -> None:
    print(f"❌ {message}")
    sys.exit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def read(path: Path) -> str:
    if not path.exists():
        die(f"Missing file: {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def replace_once(content: str, old: str, new: str, *, label: str) -> tuple[str, bool]:
    if old not in content:
        if new in content:
            ok(f"{label} already applied")
            return content, False
        die(f"Could not find expected block for: {label}")

    count = content.count(old)
    if count != 1:
        die(f"Expected exactly 1 match for {label}, found {count}")

    return content.replace(old, new, 1), True


def patch_vc_sessions() -> bool:
    path = FILES["vc_sessions"]
    content = read(path)
    original = content

    anchor = (
        '        desired_access_minutes = int(access_minutes or row.get("access_minutes") or _access_minutes())\n'
        "\n"
    )

    refresh_block = '''        status = _normalize_status(row.get("status"))
        expired_or_terminal = _row_is_expired(row) or status in {"EXPIRED", "COMPLETED", "CANCELED"}
        if expired_or_terminal:
            now_iso = _utc_iso()
            refreshed_access_minutes = desired_access_minutes if desired_access_minutes > 0 else _access_minutes()

            refresh_meta = _merge_meta(
                row.get("meta"),
                {
                    "owner_confirmed": False,
                    "staff_confirmed": False,
                    "unlocked": False,
                    "last_action": "refresh_expired_session",
                    "last_action_at": now_iso,
                    "expired_refresh_from_status": status,
                    "expired_refresh_previous_revoke_at": row.get("revoke_at"),
                },
            )

            patch.update(
                {
                    "status": "PENDING",
                    "ticket_channel_id": desired_ticket_channel_id or row.get("ticket_channel_id"),
                    "requester_id": desired_requester_id or row.get("requester_id"),
                    "owner_id": desired_owner_id or row.get("owner_id"),
                    "vc_channel_id": desired_vc_channel_id or row.get("vc_channel_id"),
                    "queue_channel_id": desired_queue_channel_id or row.get("queue_channel_id"),
                    "queue_message_id": None,
                    "accepted_at": None,
                    "accepted_by": None,
                    "ready_at": None,
                    "started_at": None,
                    "started_by": None,
                    "completed_at": None,
                    "completed_by": None,
                    "canceled_at": None,
                    "canceled_by": None,
                    "expired_at": None,
                    "restarted_at": None,
                    "restarted_by": None,
                    "access_minutes": refreshed_access_minutes,
                    "revoke_at": _utc_iso(_utcnow() + timedelta(minutes=refreshed_access_minutes)),
                    "last_watchdog_at": None,
                    "meta": refresh_meta,
                }
            )

'''

    if "refresh_expired_session" not in content:
        if anchor not in content:
            die("Could not find insert anchor in vc_sessions.ensure_session()")
        content = content.replace(anchor, anchor + refresh_block, 1)
        ok("Inserted expired-session refresh block in vc_sessions.ensure_session()")
    else:
        ok("Expired-session refresh block already present in vc_sessions.py")

    old_meta_merge = '''        if isinstance(meta, dict) and meta:
            patch["meta"] = _merge_meta(row.get("meta"), meta)
'''

    new_meta_merge = '''        if isinstance(meta, dict) and meta:
            patch["meta"] = _merge_meta(patch.get("meta", row.get("meta")), meta)
'''

    content, _changed = replace_once(
        content,
        old_meta_merge,
        new_meta_merge,
        label="preserve refresh metadata when ensure_session(meta=...) is passed",
    )

    if content != original:
        write(path, content)
        ok(f"Updated {path}")
        return True

    ok(f"No changes needed for {path}")
    return False


def patch_vc_verify() -> bool:
    path = FILES["vc_verify"]
    content = read(path)
    original = content

    old = '''    row = _get_session_row(tok)
    if row:
        return row

    if not vc_sessions or not hasattr(vc_sessions, "ensure_session"):
        return None
'''

    new = '''    row = _get_session_row(tok)
    if row:
        status = str(row.get("status") or "").upper().strip()
        if status not in {"EXPIRED", "COMPLETED", "DONE", "CANCELED", "CANCELLED"}:
            return row

    if not vc_sessions or not hasattr(vc_sessions, "ensure_session"):
        return None
'''

    content, changed = replace_once(
        content,
        old,
        new,
        label="prevent _ensure_session_backing() from short-circuiting on terminal VC rows",
    )

    if changed and content != original:
        write(path, content)
        ok(f"Updated {path}")
        return True

    ok(f"No changes needed for {path}")
    return False


def compile_check() -> None:
    for path in FILES.values():
        py_compile.compile(str(path), doraise=True)
        ok(f"Compiled {path}")


def main() -> None:
    if not (ROOT / "stoney_verify").exists():
        die("Run this from the repo root. I could not find ./stoney_verify")

    changed = False
    changed = patch_vc_sessions() or changed
    changed = patch_vc_verify() or changed

    compile_check()

    if changed:
        print("\n✅ VC expired-session runtime patch applied.")
        print("\nNext commands:")
        print("  git diff -- stoney_verify/vc_sessions.py stoney_verify/vc_verify.py")
        print("  git add stoney_verify/vc_sessions.py stoney_verify/vc_verify.py")
        print('  git commit -m "Fix VC expired session refresh"')
        print("  git push")
    else:
        print("\n✅ No changes needed. Patch was already applied.")


if __name__ == "__main__":
    main()
