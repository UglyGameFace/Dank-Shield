#!/usr/bin/env python3
from __future__ import annotations

"""Apply the member-sync service handoff.

This script performs the safe next step in the events.py split:

1. Ensure members_new.sync_service preserves join-risk / alt-cluster evidence.
2. Ensure events._new_sync_member_safe passes risk_profile into that service.
3. Physically replace the legacy events.py member DB fallback bodies with thin
   delegates to members_new.sync_service.

It is marker-based on purpose because both files are large and this change must
be safe to run from Termux/Codespaces.
"""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SYNC_SERVICE = ROOT / "stoney_verify" / "members_new" / "sync_service.py"
AUDIT_ROLE_TRUTH = ROOT / "tools" / "audit_role_truth.py"
AUDIT_EVENT_BOUNDARY = ROOT / "tools" / "audit_event_boundary.py"

RISK_OPTIONAL_COLUMNS = '''    "risk_score",
    "risk_level",
    "risk_reasons",
    "fingerprint",
    "alt_cluster_key",
    "alt_cluster_size",
    "burst_join_count",
    "same_fingerprint_count",
    "similar_name_count",
    "same_age_bucket_count",
    "suspicious_name_pattern",
    "repeated_char_pattern",
    "default_avatar",
    "account_age_days",
    "age_bucket",
    "digit_ratio",
    "underscore_ratio",
    "cluster_members",
    "suspicion_flags",
    "risk_last_evaluated_at",
    "last_join_risk_score",
    "last_join_risk_level",
    "last_join_fingerprint",
    "alt_notes",
'''

RISK_HELPERS = '''

def _safe_string_list(value: Any, max_items: int = 20) -> List[str]:
    out: List[str] = []
    try:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                text = str(item or "").strip()
                if text and text not in out:
                    out.append(text)
        else:
            text = str(value or "").strip()
            if text:
                out.append(text)
    except Exception:
        pass
    return out[:max_items]


def _safe_json_object_list(value: Any, max_items: int = 10) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    out.append(dict(item))
    except Exception:
        pass
    return out[:max_items]


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or isinstance(value, bool):
            return float(default)
        return float(str(value).strip())
    except Exception:
        return float(default)


def _derive_alt_cluster_key_from_profile(profile: Dict[str, Any]) -> Optional[str]:
    try:
        explicit = str(profile.get("alt_cluster_key") or "").strip()
        if explicit:
            return explicit

        fingerprint = str(profile.get("fingerprint") or "").strip()
        username_key = str(
            profile.get("username_normalized")
            or profile.get("display_name_normalized")
            or ""
        ).strip()
        age_bucket = str(profile.get("age_bucket") or "").strip()

        if _as_int(profile.get("same_fingerprint_count"), 0) > 0 and fingerprint:
            return f"fp:{fingerprint}"
        if _as_int(profile.get("similar_name_count"), 0) > 0 and username_key:
            return f"name:{username_key[:48]}"
        if _as_int(profile.get("same_age_bucket_count"), 0) >= 3 and age_bucket:
            return f"age:{age_bucket}"
    except Exception:
        pass
    return None


def _derive_suspicion_flags_from_profile(profile: Dict[str, Any]) -> List[str]:
    flags = _safe_string_list(profile.get("suspicion_flags"), 20)
    try:
        if flags:
            return flags
        if _as_int(profile.get("account_age_days"), 999999) <= 1:
            flags.append("extremely_new_account")
        elif _as_int(profile.get("account_age_days"), 999999) <= 3:
            flags.append("very_new_account")
        elif _as_int(profile.get("account_age_days"), 999999) <= 7:
            flags.append("fresh_account")
        if bool(profile.get("default_avatar")):
            flags.append("default_avatar")
        if bool(profile.get("suspicious_name_pattern")):
            flags.append("suspicious_name_pattern")
        if bool(profile.get("repeated_char_pattern")):
            flags.append("repeated_character_pattern")
        if _as_float(profile.get("digit_ratio"), 0.0) >= 0.45:
            flags.append("very_high_digit_ratio")
        elif _as_float(profile.get("digit_ratio"), 0.0) >= 0.25:
            flags.append("elevated_digit_ratio")
        if _as_float(profile.get("underscore_ratio"), 0.0) >= 0.18:
            flags.append("high_underscore_ratio")
        if _as_int(profile.get("burst_count"), 0) > 0:
            flags.append("join_burst")
        if _as_int(profile.get("same_fingerprint_count"), 0) > 0:
            flags.append("shared_behavior_fingerprint")
        if _as_int(profile.get("similar_name_count"), 0) > 0:
            flags.append("similar_recent_username")
        if _as_int(profile.get("same_age_bucket_count"), 0) > 0:
            flags.append("age_bucket_cluster")
    except Exception:
        pass
    return flags[:20]


def _build_risk_payload_from_profile(
    risk_profile: Optional[Dict[str, Any]],
    *,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(risk_profile, dict):
        return {}

    now_value = now_iso or _sync_iso_now()
    score = max(0, min(100, _as_int(risk_profile.get("score"), 0)))
    level_raw = str(risk_profile.get("level") or "low").strip().lower()
    level = level_raw if level_raw in {"low", "medium", "high", "critical"} else "low"
    fingerprint = str(risk_profile.get("fingerprint") or "").strip() or None
    alt_cluster_key = _derive_alt_cluster_key_from_profile(risk_profile)
    same_fingerprint_count = max(0, _as_int(risk_profile.get("same_fingerprint_count"), 0))
    similar_name_count = max(0, _as_int(risk_profile.get("similar_name_count"), 0))
    same_age_bucket_count = max(0, _as_int(risk_profile.get("same_age_bucket_count"), 0))
    burst_join_count = max(0, _as_int(risk_profile.get("burst_count"), 0))
    alt_cluster_size = max(0, _as_int(risk_profile.get("alt_cluster_size"), 0))
    if alt_cluster_size <= 0 and alt_cluster_key:
        alt_cluster_size = 1 + max(same_fingerprint_count, similar_name_count, same_age_bucket_count)

    return {
        "risk_score": score,
        "risk_level": level,
        "risk_reasons": _safe_string_list(risk_profile.get("reasons"), 12),
        "fingerprint": fingerprint,
        "alt_cluster_key": alt_cluster_key,
        "alt_cluster_size": alt_cluster_size,
        "burst_join_count": burst_join_count,
        "same_fingerprint_count": same_fingerprint_count,
        "similar_name_count": similar_name_count,
        "same_age_bucket_count": same_age_bucket_count,
        "suspicious_name_pattern": bool(risk_profile.get("suspicious_name_pattern")),
        "repeated_char_pattern": bool(risk_profile.get("repeated_char_pattern")),
        "default_avatar": bool(risk_profile.get("default_avatar")),
        "account_age_days": _as_int(risk_profile.get("account_age_days"), 0),
        "age_bucket": str(risk_profile.get("age_bucket") or "").strip() or None,
        "digit_ratio": round(_as_float(risk_profile.get("digit_ratio"), 0.0), 3),
        "underscore_ratio": round(_as_float(risk_profile.get("underscore_ratio"), 0.0), 3),
        "cluster_members": _safe_json_object_list(risk_profile.get("cluster_members"), 8),
        "suspicion_flags": _derive_suspicion_flags_from_profile(risk_profile),
        "risk_last_evaluated_at": now_value,
        "last_join_risk_score": score,
        "last_join_risk_level": level,
        "last_join_fingerprint": fingerprint,
    }
'''

EVENTS_SERVICE_CALL = '''                await new_sync_member_to_supabase(
                    member,
                    in_guild=in_guild,
                    risk_profile=risk_profile,
                )
'''

SYNC_MEMBER_DELEGATE = '''async def _sync_member_to_supabase(
    member: discord.Member,
    in_guild: bool = True,
    risk_profile: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        if callable(new_sync_member_to_supabase):
            try:
                await new_sync_member_to_supabase(
                    member,
                    in_guild=in_guild,
                    risk_profile=risk_profile,
                )
            except TypeError:
                await new_sync_member_to_supabase(member, in_guild=in_guild)
            return
        print("⚠️ member sync service unavailable; skipping legacy events DB fallback")
    except Exception as e:
        print("⚠️ _sync_member_to_supabase service delegate failed:", repr(e))


'''

MARK_LEFT_DELEGATE = '''async def _mark_member_left(member: discord.Member) -> None:
    try:
        if callable(new_mark_member_left):
            await new_mark_member_left(member)
            return
        print("⚠️ member-left sync service unavailable; skipping legacy events DB fallback")
    except Exception as e:
        print("⚠️ _mark_member_left service delegate failed:", repr(e))


'''


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def die(message: str) -> None:
    print(f"❌ {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def replace_function_block(text: str, *, start_marker: str, end_marker: str, replacement: str, label: str) -> tuple[str, bool]:
    start = text.find(start_marker)
    if start < 0:
        if replacement.strip() in text:
            ok(f"{label} already applied")
            return text, False
        die(f"Could not find start marker for {label}: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        die(f"Could not find end marker for {label}: {end_marker!r}")
    current = text[start:end]
    if replacement.strip() in current:
        ok(f"{label} already applied")
        return text, False
    return text[:start] + replacement + text[end:], True


def patch_sync_service(text: str) -> tuple[str, bool]:
    changed = False

    if '    "risk_score",' not in text:
        anchor = '    "data_health",\n'
        if anchor not in text:
            die("Could not find optional-column anchor in sync_service.py")
        text = text.replace(anchor, RISK_OPTIONAL_COLUMNS + anchor, 1)
        changed = True

    if "def _build_risk_payload_from_profile" not in text:
        anchor = "def _sync_iso_now() -> str:\n"
        if anchor not in text:
            die("Could not find _sync_iso_now anchor in sync_service.py")
        text = text.replace(anchor, RISK_HELPERS + "\n\n" + anchor, 1)
        changed = True

    old_signature = "async def sync_member_to_supabase(member: discord.Member, in_guild: bool = True) -> None:"
    new_signature = (
        "async def sync_member_to_supabase(\n"
        "    member: discord.Member,\n"
        "    in_guild: bool = True,\n"
        "    risk_profile: Optional[Dict[str, Any]] = None,\n"
        ") -> None:"
    )
    if old_signature in text:
        text = text.replace(old_signature, new_signature, 1)
        changed = True

    if "merged_risk_payload = (" not in text:
        anchor = '''        entry_meta = _entry_metadata_from_existing_join_and_tickets(
            existing=existing,
            latest_join=latest_join,
            latest_ticket_rows=latest_ticket_rows,
        )

'''
        insertion = anchor + '''        merged_risk_payload = (
            _build_risk_payload_from_profile(risk_profile, now_iso=now_iso)
            if isinstance(risk_profile, dict)
            else {key: existing.get(key) for key in (
                "risk_score",
                "risk_level",
                "risk_reasons",
                "fingerprint",
                "alt_cluster_key",
                "alt_cluster_size",
                "burst_join_count",
                "same_fingerprint_count",
                "similar_name_count",
                "same_age_bucket_count",
                "suspicious_name_pattern",
                "repeated_char_pattern",
                "default_avatar",
                "account_age_days",
                "age_bucket",
                "digit_ratio",
                "underscore_ratio",
                "cluster_members",
                "suspicion_flags",
                "risk_last_evaluated_at",
                "last_join_risk_score",
                "last_join_risk_level",
                "last_join_fingerprint",
                "alt_notes",
            ) if key in existing}
        )

'''
        if anchor not in text:
            die("Could not find entry_meta anchor in sync_service.py")
        text = text.replace(anchor, insertion, 1)
        changed = True

    payload_anchor = '''            "entry_conflict": bool(entry_meta.get("entry_conflict", False)),
        }
'''
    if "**merged_risk_payload," not in text:
        replacement = '''            "entry_conflict": bool(entry_meta.get("entry_conflict", False)),
            **merged_risk_payload,
        }
'''
        if payload_anchor not in text:
            die("Could not find full_payload risk insertion anchor in sync_service.py")
        text = text.replace(payload_anchor, replacement, 1)
        changed = True

    return text, changed


def patch_events(text: str) -> tuple[str, bool]:
    changed = False

    old = "                await new_sync_member_to_supabase(member, in_guild=in_guild)\n"
    if old in text:
        text = text.replace(old, EVENTS_SERVICE_CALL, 1)
        changed = True

    text, changed_sync_body = replace_function_block(
        text,
        start_marker="async def _sync_member_to_supabase(\n",
        end_marker="async def _mark_member_left(member: discord.Member) -> None:\n",
        replacement=SYNC_MEMBER_DELEGATE,
        label="events._sync_member_to_supabase service delegate",
    )
    changed = changed or changed_sync_body

    text, changed_left_body = replace_function_block(
        text,
        start_marker="async def _mark_member_left(member: discord.Member) -> None:\n",
        end_marker="async def _initial_member_sync_sweep() -> None:\n",
        replacement=MARK_LEFT_DELEGATE,
        label="events._mark_member_left service delegate",
    )
    changed = changed or changed_left_body

    return text, changed


def verify_ready(sync_service: str, events: str) -> list[str]:
    missing: list[str] = []
    for marker in (
        "risk_profile: Optional[Dict[str, Any]] = None",
        "def _build_risk_payload_from_profile",
        "last_join_risk_score",
        "alt_cluster_key",
        "suspicion_flags",
        "**merged_risk_payload,",
    ):
        if marker not in sync_service:
            missing.append(f"sync_service missing {marker}")
    for marker in (
        "risk_profile=risk_profile",
        "member sync service unavailable; skipping legacy events DB fallback",
        "member-left sync service unavailable; skipping legacy events DB fallback",
    ):
        if marker not in events:
            missing.append(f"events.py missing {marker}")
    for forbidden in (
        "await _guild_members_upsert_async(sb, full_payload",
        "await _guild_members_update_member_async(\n                sb,\n                guild_id,\n                user_id,",
        "legacy _sync_member_to_supabase fallback failed",
        "legacy _mark_member_left fallback failed",
    ):
        if forbidden in events:
            missing.append(f"events.py still contains legacy member DB fallback marker: {forbidden}")
    return missing


def main() -> int:
    if not EVENTS.exists():
        die(f"Missing {EVENTS}")
    if not SYNC_SERVICE.exists():
        die(f"Missing {SYNC_SERVICE}")

    sync_text = read(SYNC_SERVICE)
    events_text = read(EVENTS)

    sync_text, changed_sync = patch_sync_service(sync_text)
    events_text, changed_events = patch_events(events_text)

    missing = verify_ready(sync_text, events_text)
    if missing:
        print("❌ Member sync handoff is incomplete:")
        for item in missing:
            print(f" - {item}")
        return 1

    if changed_sync:
        write(SYNC_SERVICE, sync_text)
        ok("Updated stoney_verify/members_new/sync_service.py")
    else:
        ok("sync_service risk_profile support already present")

    if changed_events:
        write(EVENTS, events_text)
        ok("Updated events.py member sync fallbacks to service delegates")
    else:
        ok("events.py member sync service delegates already present")

    for path in (SYNC_SERVICE, EVENTS, AUDIT_ROLE_TRUTH, AUDIT_EVENT_BOUNDARY):
        if path.exists():
            py_compile.compile(str(path), doraise=True)
            ok(f"Compiled {path.relative_to(ROOT)}")

    print("\nNext commands:")
    print("  git diff -- stoney_verify/members_new/sync_service.py stoney_verify/events.py")
    print("  python tools/apply_member_sync_service_handoff.py")
    print("  python -m py_compile stoney_verify/members_new/sync_service.py stoney_verify/events.py")
    print("  git add stoney_verify/members_new/sync_service.py stoney_verify/events.py")
    print('  git commit -m "Physically hand off member sync events"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
