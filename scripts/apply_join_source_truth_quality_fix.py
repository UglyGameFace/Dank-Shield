#!/usr/bin/env python3
from __future__ import annotations

import py_compile
import sys
from pathlib import Path


ROOT = Path.cwd()
EVENTS = ROOT / "stoney_verify" / "events.py"
SYNC_SERVICE = ROOT / "stoney_verify" / "members_new" / "sync_service.py"


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


def patch_events_build_join_context(content: str) -> tuple[str, bool]:
    old = '''def _build_join_context(
    *,
    entry_method: str,
    join_source: str,
    verification_source: str,
    invite_code: Optional[str] = None,
    invited_by: Optional[str] = None,
    invited_by_name: Optional[str] = None,
    vouched_by: Optional[str] = None,
    vouched_by_name: Optional[str] = None,
    approved_by: Optional[str] = None,
    approved_by_name: Optional[str] = None,
    entry_reason: Optional[str] = None,
    approval_reason: Optional[str] = None,
    join_note: Optional[str] = None,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    vanity_used: bool = False,
    source_ticket_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "entry_method": str(entry_method or "").strip() or "unknown_join",
        "join_source": str(join_source or "").strip() or "unknown_join",
        "verification_source": str(verification_source or "").strip() or "join_observer_unresolved",
        "invite_code": str(invite_code or "").strip() or None,
        "invited_by": str(invited_by or "").strip() or None,
        "invited_by_name": str(invited_by_name or "").strip() or None,
        "vouched_by": str(vouched_by or "").strip() or None,
        "vouched_by_name": str(vouched_by_name or "").strip() or None,
        "approved_by": str(approved_by or "").strip() or None,
        "approved_by_name": str(approved_by_name or "").strip() or None,
        "entry_reason": str(entry_reason or "").strip() or None,
        "approval_reason": str(approval_reason or "").strip() or None,
        "join_note": str(join_note or "").strip() or None,
        "channel_id": str(channel_id or "").strip() or None,
        "channel_name": str(channel_name or "").strip() or None,
        "vanity_used": bool(vanity_used),
        "source_ticket_id": str(source_ticket_id or "").strip() or None,
    }
'''

    new = '''def _join_truth_quality(entry_method: str, *, invite_code: Optional[str] = None, invited_by: Optional[str] = None) -> Tuple[str, int, str]:
    method = str(entry_method or "").strip().lower()

    if method == "invite" and (invite_code or invited_by):
        return ("confirmed", 95, "Invite usage delta identified a specific invite.")
    if method == "vanity_invite":
        return ("confirmed", 90, "Vanity invite usage increased.")
    if method in {"vouched", "manual_verification", "ticket_verification"}:
        return ("confirmed", 85, "Entry source came from an explicit staff/ticket action.")
    if method == "invite_tracking_unavailable":
        return ("unknown", 15, "Invite tracking was unavailable due to permissions or API failure.")
    if method == "invite_cache_warming":
        return ("partial", 35, "Invite cache was still warming; attribution should not be trusted as exact.")
    if method == "invite_unresolved":
        return ("partial", 45, "Invite cache existed, but the usage delta did not identify one invite.")
    return ("unknown", 20, "Join attribution is unknown.")


def _build_join_context(
    *,
    entry_method: str,
    join_source: str,
    verification_source: str,
    invite_code: Optional[str] = None,
    invited_by: Optional[str] = None,
    invited_by_name: Optional[str] = None,
    vouched_by: Optional[str] = None,
    vouched_by_name: Optional[str] = None,
    approved_by: Optional[str] = None,
    approved_by_name: Optional[str] = None,
    entry_reason: Optional[str] = None,
    approval_reason: Optional[str] = None,
    join_note: Optional[str] = None,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    vanity_used: bool = False,
    source_ticket_id: Optional[str] = None,
) -> Dict[str, Any]:
    quality, confidence, quality_reason = _join_truth_quality(
        entry_method,
        invite_code=invite_code,
        invited_by=invited_by,
    )
    return {
        "entry_method": str(entry_method or "").strip() or "unknown_join",
        "join_source": str(join_source or "").strip() or "unknown_join",
        "verification_source": str(verification_source or "").strip() or "join_observer_unresolved",
        "invite_code": str(invite_code or "").strip() or None,
        "invited_by": str(invited_by or "").strip() or None,
        "invited_by_name": str(invited_by_name or "").strip() or None,
        "vouched_by": str(vouched_by or "").strip() or None,
        "vouched_by_name": str(vouched_by_name or "").strip() or None,
        "approved_by": str(approved_by or "").strip() or None,
        "approved_by_name": str(approved_by_name or "").strip() or None,
        "entry_reason": str(entry_reason or "").strip() or None,
        "approval_reason": str(approval_reason or "").strip() or None,
        "join_note": str(join_note or "").strip() or None,
        "channel_id": str(channel_id or "").strip() or None,
        "channel_name": str(channel_name or "").strip() or None,
        "vanity_used": bool(vanity_used),
        "source_ticket_id": str(source_ticket_id or "").strip() or None,
        "entry_truth_quality": quality,
        "entry_confidence": confidence,
        "entry_quality_reason": quality_reason,
        "entry_conflict": False,
    }
'''

    return replace_once(content, old, new, label="add join truth quality to events._build_join_context")


def patch_events_persistence(content: str) -> tuple[str, bool]:
    old = '''            "source_ticket_id": context.get("source_ticket_id"),
            "joined_at": joined_at,
            "vanity_used": bool(context.get("vanity_used", False)),
            "synced_at": now_iso,
'''

    new = '''            "source_ticket_id": context.get("source_ticket_id"),
            "entry_truth_quality": context.get("entry_truth_quality"),
            "entry_confidence": context.get("entry_confidence"),
            "entry_quality_reason": context.get("entry_quality_reason"),
            "entry_conflict": bool(context.get("entry_conflict", False)),
            "joined_at": joined_at,
            "vanity_used": bool(context.get("vanity_used", False)),
            "synced_at": now_iso,
'''

    content, changed1 = replace_once(content, old, new, label="persist join truth quality on guild_members patch")

    old = '''            "source_ticket_id": context.get("source_ticket_id"),
            "vanity_used": bool(context.get("vanity_used", False)),
            "risk_score": risk_payload.get("risk_score", 0),
'''

    new = '''            "source_ticket_id": context.get("source_ticket_id"),
            "entry_truth_quality": context.get("entry_truth_quality"),
            "entry_confidence": context.get("entry_confidence"),
            "entry_quality_reason": context.get("entry_quality_reason"),
            "entry_conflict": bool(context.get("entry_conflict", False)),
            "vanity_used": bool(context.get("vanity_used", False)),
            "risk_score": risk_payload.get("risk_score", 0),
'''

    content, changed2 = replace_once(content, old, new, label="persist join truth quality on member_joins row")

    old = '''                        "vanity_used": bool(context.get("vanity_used", False)),
                        "invited_by": context.get("invited_by"),
'''

    new = '''                        "vanity_used": bool(context.get("vanity_used", False)),
                        "entry_truth_quality": context.get("entry_truth_quality"),
                        "entry_confidence": context.get("entry_confidence"),
                        "entry_quality_reason": context.get("entry_quality_reason"),
                        "entry_conflict": bool(context.get("entry_conflict", False)),
                        "invited_by": context.get("invited_by"),
'''

    content, changed3 = replace_once(content, old, new, label="persist join truth quality on member_events metadata")
    return content, bool(changed1 or changed2 or changed3)


def patch_sync_service_optional_columns(content: str) -> tuple[str, bool]:
    old = '''    "entry_reason",
    "approval_reason",
    "has_any_role",
'''

    new = '''    "entry_reason",
    "approval_reason",
    "entry_truth_quality",
    "entry_confidence",
    "entry_quality_reason",
    "entry_conflict",
    "has_any_role",
'''

    return replace_once(content, old, new, label="add entry truth quality optional guild_members columns")


def patch_sync_service_helpers(content: str) -> tuple[str, bool]:
    anchor = '''def _entry_metadata_from_existing_join_and_tickets(
'''

    helper = '''def _entry_truth_quality_from_meta(
    *,
    entry_method: Optional[str],
    invite_code: Optional[str],
    invited_by: Optional[str],
    latest_join: Optional[Dict[str, Any]],
    existing: Dict[str, Any],
) -> Dict[str, Any]:
    explicit_quality = _coalesce_str(
        (latest_join or {}).get("entry_truth_quality"),
        existing.get("entry_truth_quality"),
    )
    explicit_reason = _coalesce_str(
        (latest_join or {}).get("entry_quality_reason"),
        existing.get("entry_quality_reason"),
    )

    try:
        explicit_confidence = (latest_join or {}).get("entry_confidence")
        if explicit_confidence is None:
            explicit_confidence = existing.get("entry_confidence")
        if explicit_confidence is not None:
            explicit_confidence_int = max(0, min(100, int(explicit_confidence)))
        else:
            explicit_confidence_int = None
    except Exception:
        explicit_confidence_int = None

    if explicit_quality:
        return {
            "entry_truth_quality": explicit_quality,
            "entry_confidence": explicit_confidence_int if explicit_confidence_int is not None else 50,
            "entry_quality_reason": explicit_reason or "Entry truth quality was already recorded.",
            "entry_conflict": bool((latest_join or {}).get("entry_conflict") or existing.get("entry_conflict") or False),
        }

    method = _safe_str(entry_method).strip().lower()
    if method == "invite" and (invite_code or invited_by):
        return {
            "entry_truth_quality": "confirmed",
            "entry_confidence": 95,
            "entry_quality_reason": "Invite usage delta identified a specific invite.",
            "entry_conflict": False,
        }
    if method == "vanity_invite":
        return {
            "entry_truth_quality": "confirmed",
            "entry_confidence": 90,
            "entry_quality_reason": "Vanity invite usage increased.",
            "entry_conflict": False,
        }
    if method in {"vouched", "manual_verification", "ticket_verification"}:
        return {
            "entry_truth_quality": "confirmed",
            "entry_confidence": 85,
            "entry_quality_reason": "Entry source came from an explicit staff/ticket action.",
            "entry_conflict": False,
        }
    if method == "invite_tracking_unavailable":
        return {
            "entry_truth_quality": "unknown",
            "entry_confidence": 15,
            "entry_quality_reason": "Invite tracking was unavailable due to permissions or API failure.",
            "entry_conflict": False,
        }
    if method == "invite_cache_warming":
        return {
            "entry_truth_quality": "partial",
            "entry_confidence": 35,
            "entry_quality_reason": "Invite cache was still warming; attribution should not be trusted as exact.",
            "entry_conflict": False,
        }
    if method == "invite_unresolved":
        return {
            "entry_truth_quality": "partial",
            "entry_confidence": 45,
            "entry_quality_reason": "Invite cache existed, but the usage delta did not identify one invite.",
            "entry_conflict": False,
        }

    return {
        "entry_truth_quality": "unknown",
        "entry_confidence": 20,
        "entry_quality_reason": "Join attribution is unknown.",
        "entry_conflict": False,
    }


'''

    if "def _entry_truth_quality_from_meta" in content:
        ok("sync_service entry truth helper already present")
        return content, False

    if anchor not in content:
        die("Could not find sync_service helper anchor")

    return content.replace(anchor, helper + anchor, 1), True


def patch_sync_service_entry_meta(content: str) -> tuple[str, bool]:
    old = '''    return {
        "invited_by": _coalesce_str(
            (latest_join or {}).get("invited_by"),
            existing.get("invited_by"),
        ),
'''

    new = '''    invited_by = _coalesce_str(
        (latest_join or {}).get("invited_by"),
        existing.get("invited_by"),
    )
    invite_code = _coalesce_str(
        (latest_join or {}).get("invite_code"),
        existing.get("invite_code"),
    )
    truth_meta = _entry_truth_quality_from_meta(
        entry_method=entry_method,
        invite_code=invite_code,
        invited_by=invited_by,
        latest_join=latest_join,
        existing=existing,
    )

    return {
        "invited_by": invited_by,
'''

    content, changed1 = replace_once(content, old, new, label="compute entry truth meta in sync_service")

    old = '''        "invite_code": _coalesce_str(
            (latest_join or {}).get("invite_code"),
            existing.get("invite_code"),
        ),
'''

    new = '''        "invite_code": invite_code,
'''

    content, changed2 = replace_once(content, old, new, label="reuse invite_code in sync_service entry meta")

    old = '''        "entry_reason": entry_reason,
        "approval_reason": approval_reason,
    }
'''

    new = '''        "entry_reason": entry_reason,
        "approval_reason": approval_reason,
        **truth_meta,
    }
'''

    content, changed3 = replace_once(content, old, new, label="include entry truth meta in sync_service return")
    return content, bool(changed1 or changed2 or changed3)


def patch_sync_service_update_join_row(content: str) -> tuple[str, bool]:
    old = '''            "join_source",
        ):
'''

    new = '''            "join_source",
            "entry_truth_quality",
            "entry_confidence",
            "entry_quality_reason",
            "entry_conflict",
        ):
'''

    return replace_once(content, old, new, label="backfill entry truth quality onto latest member_joins row")


def patch_sync_service_payload(content: str) -> tuple[str, bool]:
    old = '''            "entry_reason": entry_meta["entry_reason"],
            "approval_reason": entry_meta["approval_reason"],
        }
'''

    new = '''            "entry_reason": entry_meta["entry_reason"],
            "approval_reason": entry_meta["approval_reason"],
            "entry_truth_quality": entry_meta.get("entry_truth_quality"),
            "entry_confidence": entry_meta.get("entry_confidence"),
            "entry_quality_reason": entry_meta.get("entry_quality_reason"),
            "entry_conflict": bool(entry_meta.get("entry_conflict", False)),
        }
'''

    return replace_once(content, old, new, label="persist entry truth quality on guild_members sync payload")


def patch_events() -> bool:
    content = read(EVENTS)
    original = content
    content, _ = patch_events_build_join_context(content)
    content, _ = patch_events_persistence(content)
    if content != original:
        write(EVENTS, content)
        ok(f"Updated {EVENTS}")
        return True
    ok(f"No changes needed for {EVENTS}")
    return False


def patch_sync_service() -> bool:
    content = read(SYNC_SERVICE)
    original = content
    content, _ = patch_sync_service_optional_columns(content)
    content, _ = patch_sync_service_helpers(content)
    content, _ = patch_sync_service_entry_meta(content)
    content, _ = patch_sync_service_update_join_row(content)
    content, _ = patch_sync_service_payload(content)
    if content != original:
        write(SYNC_SERVICE, content)
        ok(f"Updated {SYNC_SERVICE}")
        return True
    ok(f"No changes needed for {SYNC_SERVICE}")
    return False


def compile_check() -> None:
    for path in (EVENTS, SYNC_SERVICE):
        py_compile.compile(str(path), doraise=True)
        ok(f"Compiled {path}")


def main() -> None:
    if not (ROOT / "stoney_verify").exists():
        die("Run this from the repo root. I could not find ./stoney_verify")
    changed = False
    changed = patch_events() or changed
    changed = patch_sync_service() or changed
    compile_check()
    if changed:
        print("\n✅ Join-source truth-quality runtime patch applied.")
        print("\nNext commands:")
        print("  git diff -- stoney_verify/events.py stoney_verify/members_new/sync_service.py")
        print("  git add stoney_verify/events.py stoney_verify/members_new/sync_service.py")
        print('  git commit -m "Track join source truth quality"')
        print("  git push")
    else:
        print("\n✅ No changes needed. Patch was already applied.")


if __name__ == "__main__":
    main()
