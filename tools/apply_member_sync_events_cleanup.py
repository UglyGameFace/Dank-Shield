#!/usr/bin/env python3
from __future__ import annotations

"""Physically remove legacy member-sync ownership from events.py.

Run after members_new.sync_service has risk_profile support.
This removes the remaining events.py fallback ownership:
- _new_sync_member_safe legacy fallback call
- _new_mark_member_left_safe legacy fallback call
- _sync_member_to_supabase legacy DB body
- _mark_member_left legacy DB body
"""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SYNC_SERVICE = ROOT / "stoney_verify" / "members_new" / "sync_service.py"

NEW_SYNC_SAFE = '''async def _new_sync_member_safe(
    member: discord.Member,
    *,
    in_guild: bool,
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
        print("⚠️ new_sync_member_to_supabase unavailable; member sync skipped")
    except Exception as e:
        print("⚠️ new_sync_member_to_supabase failed:", repr(e))


'''

NEW_MARK_LEFT_SAFE = '''async def _new_mark_member_left_safe(member: discord.Member) -> None:
    try:
        if callable(new_mark_member_left):
            await new_mark_member_left(member)
            return
        print("⚠️ new_mark_member_left unavailable; member-left sync skipped")
    except Exception as e:
        print("⚠️ new_mark_member_left failed:", repr(e))


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
        print("⚠️ member sync service unavailable; member sync skipped")
    except Exception as e:
        print("⚠️ _sync_member_to_supabase service delegate failed:", repr(e))


'''

MARK_LEFT_DELEGATE = '''async def _mark_member_left(member: discord.Member) -> None:
    try:
        if callable(new_mark_member_left):
            await new_mark_member_left(member)
            return
        print("⚠️ member-left sync service unavailable; member-left sync skipped")
    except Exception as e:
        print("⚠️ _mark_member_left service delegate failed:", repr(e))


'''

FORBIDDEN_EVENTS_MARKERS = (
    "legacy _sync_member_to_supabase fallback failed",
    "legacy _mark_member_left fallback failed",
    "await _guild_members_upsert_async(sb, full_payload",
    "await _guild_members_update_member_async(\n                sb,\n                guild_id,\n                user_id,",
)

REQUIRED_SYNC_SERVICE_MARKERS = (
    "risk_profile: Optional[Dict[str, Any]] = None",
    "def _build_risk_payload_from_profile",
    "**merged_risk_payload,",
    "last_join_risk_score",
    "alt_cluster_key",
    "suspicion_flags",
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


def replace_block(text: str, *, start_marker: str, end_marker: str, replacement: str, label: str) -> tuple[str, bool]:
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


def verify_sync_service_ready() -> None:
    service = read(SYNC_SERVICE)
    missing = [m for m in REQUIRED_SYNC_SERVICE_MARKERS if m not in service]
    if missing:
        print("❌ sync_service is not ready for events.py member-sync fallback removal:")
        for item in missing:
            print(" -", item)
        raise SystemExit(1)


def main() -> int:
    if not EVENTS.exists():
        die(f"Missing {EVENTS}")
    if not SYNC_SERVICE.exists():
        die(f"Missing {SYNC_SERVICE}")

    verify_sync_service_ready()

    text = read(EVENTS)
    changed = False

    text, did = replace_block(
        text,
        start_marker="async def _new_sync_member_safe(\n",
        end_marker="async def _new_mark_member_left_safe(member: discord.Member) -> None:\n",
        replacement=NEW_SYNC_SAFE,
        label="events._new_sync_member_safe service-only wrapper",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="async def _new_mark_member_left_safe(member: discord.Member) -> None:\n",
        end_marker="# ============================================================\n# Async wrappers for blocking Supabase work\n",
        replacement=NEW_MARK_LEFT_SAFE,
        label="events._new_mark_member_left_safe service-only wrapper",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="async def _sync_member_to_supabase(\n",
        end_marker="async def _mark_member_left(member: discord.Member) -> None:\n",
        replacement=SYNC_MEMBER_DELEGATE,
        label="events._sync_member_to_supabase service delegate",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="async def _mark_member_left(member: discord.Member) -> None:\n",
        end_marker="async def _initial_member_sync_sweep() -> None:\n",
        replacement=MARK_LEFT_DELEGATE,
        label="events._mark_member_left service delegate",
    )
    changed = changed or did

    offenders = [m for m in FORBIDDEN_EVENTS_MARKERS if m in text]
    if offenders:
        print("❌ events.py still contains legacy member-sync markers:")
        for item in offenders:
            print(" -", item)
        return 1

    if changed:
        write(EVENTS, text)
        ok("Updated stoney_verify/events.py member-sync ownership")
    else:
        ok("events.py member-sync ownership already cleaned")

    py_compile.compile(str(EVENTS), doraise=True)
    ok("Compiled stoney_verify/events.py")

    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/apply_member_sync_events_cleanup.py")
    print("  python -m py_compile stoney_verify/events.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Physically hand off member sync events"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
