#!/usr/bin/env python3
from __future__ import annotations

"""Audit remaining stoney_verify/events.py ownership after service handoffs.

Reporting-only. Does not modify files.
"""

import ast
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"

HIGH_RISK_MARKERS = {
    "direct guild_members write ownership": [
        'table("guild_members")',
        "table('guild_members')",
        ".upsert(",
        ".update(",
    ],
    "direct join/event table ownership": [
        'table("member_joins")',
        'table("member_events")',
    ],
    "direct ticket lifecycle ownership": [
        "find_open_ticket_for_owner",
        "mark_ticket_deleted",
        "mark_ticket_closed",
        "send_tickettool_style_transcript",
    ],
    "legacy role truth ownership": [
        "UNVERIFIED_ROLE_ID",
        "VERIFIED_ROLE_ID",
        "RESIDENT_ROLE_ID",
        "STONER_ROLE_ID",
        "STAFF_ROLE_ID",
    ],
    "legacy join context ownership": [
        "_INVITE_USES_CACHE",
        "_VANITY_USES_CACHE",
        "def _invite_meta",
        "def _detect_join_entry_context",
        "def _persist_member_join_context",
    ],
    "legacy VC runtime ownership": [
        "VC relock skipped",
        "Failed clearing VC overwrite",
        "VC session finalize loop error",
        "VC session live-state reconcile error",
    ],
}

EXPECTED_DELEGATES = {
    "member sync service delegates": [
        "new_sync_member_to_supabase",
        "new_mark_member_left",
        "new_run_full_member_sync_for_guild",
    ],
    "join context service delegates": [
        "join_context_service",
        "warm_invite_cache_for_guild",
        "persist_member_join_context",
    ],
    "departed ticket cleanup delegates": [
        "departed_member_cleanup_service",
        "close_verification_ticket_for_departed_member",
        "reconcile_stale_open_verification_tickets",
    ],
    "VC runtime service delegates": [
        "vc_session_runtime_service",
        "maybe_finish_vc_sessions_after_voice_change",
        "VcRuntimeDeps",
    ],
    "role truth delegates": [
        "role_truth.build_member_role_snapshot",
        "role_truth.member_is_pending_verification",
        "role_truth.member_has_any_safe_access_role",
    ],
}

EXPECTED_LOCAL_HELPER_PREFIXES = (
    "on_",
    "_as_",
    "_startup_",
    "_assign_startup_task",
    "_run_blocking_db",
    "_tickets_",
    "_vc_sessions_",
    "_fetch_active_vc_session_rows",
    "_auto_close_verification_ticket_for_departed_member",
    "_reconcile_stale_open_verification_tickets",
    "_sync_member_to_supabase",
    "_mark_member_left",
    "_initial_member_sync_sweep",
    "_refresh_guild_invite_cache",
    "_warm_all_guild_invite_caches",
    "_join_truth_quality",
    "_build_join_context",
    "_detect_join_entry_context",
    "_persist_member_join_context",
    "_vc_",
    "_member_in_target_voice",
    "_resolve_vc_verify_channel",
    "_maybe_finish_vc_sessions_after_voice_change",
    "_can_manage_channel",
    "_get_",
    "_safe_",
    "_strip_",
    "_has_",
    "_ensure_",
    "_log_",
    "_send_",
    "_fetch_",
    "_resolve_",
    "_set_",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def line_no_for_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def marker_lines(text: str, markers: Iterable[str]) -> list[str]:
    out: list[str] = []
    for marker in markers:
        start = 0
        while True:
            idx = text.find(marker, start)
            if idx < 0:
                break
            out.append(f"L{line_no_for_offset(text, idx)}: {marker}")
            start = idx + len(marker)
    return out


def function_names(text: str) -> list[str]:
    tree = ast.parse(text)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
    return sorted(set(names))


def possibly_owned_helpers(names: list[str]) -> list[str]:
    suspicious: list[str] = []
    for name in names:
        if name.startswith(EXPECTED_LOCAL_HELPER_PREFIXES):
            continue
        suspicious.append(name)
    return suspicious


def main() -> int:
    if not EVENTS.exists():
        print(f"❌ missing {EVENTS}")
        return 1

    text = read(EVENTS)
    print(f"events.py lines={text.count(chr(10)) + 1}")

    print("\nHigh-risk ownership markers:")
    high_risk_found = False
    for label, markers in HIGH_RISK_MARKERS.items():
        lines = marker_lines(text, markers)
        if lines:
            high_risk_found = True
            print(f"❌ {label}")
            for item in lines[:20]:
                print(f"   - {item}")
            if len(lines) > 20:
                print(f"   ... +{len(lines) - 20} more")
        else:
            print(f"✅ {label}: none")

    print("\nExpected service delegates:")
    missing_delegate = False
    for label, markers in EXPECTED_DELEGATES.items():
        missing = [marker for marker in markers if marker not in text]
        if missing:
            missing_delegate = True
            print(f"⚠️ {label}: missing {', '.join(missing)}")
        else:
            print(f"✅ {label}")

    print("\nFunction inventory:")
    names = function_names(text)
    print(f"functions={len(names)}")
    suspicious = possibly_owned_helpers(names)
    if suspicious:
        print("⚠️ unusual helper names to review:")
        for name in suspicious[:50]:
            print(f"   - {name}")
        if len(suspicious) > 50:
            print(f"   ... +{len(suspicious) - 50} more")
    else:
        print("✅ no unusual helper names")

    print("\nSummary:")
    if high_risk_found:
        print("❌ audit found high-risk ownership markers")
        return 1
    if missing_delegate:
        print("⚠️ audit passed ownership markers but some expected delegates are missing")
        return 2
    print("✅ audit passed: no high-risk ownership markers found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
