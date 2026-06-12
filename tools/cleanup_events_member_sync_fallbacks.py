#!/usr/bin/env python3
from __future__ import annotations

"""Remove legacy member-sync fallback ownership from events.py.

After members_new.sync_service owns full sync and departed reconciliation,
events.py should not keep local guild_members DB wrappers, minimal payload
builders, or risk payload builders around just for the old startup fallback.
"""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SYNC_SERVICE = ROOT / "stoney_verify" / "members_new" / "sync_service.py"
JOIN_CONTEXT_SERVICE = ROOT / "stoney_verify" / "members_new" / "join_context_service.py"

INITIAL_SWEEP_DELEGATE = '''async def _initial_member_sync_sweep() -> None:
    try:
        guilds = list(getattr(bot, "guilds", []) or [])
    except Exception:
        guilds = []

    for guild in guilds:
        try:
            if not callable(new_run_full_member_sync_for_guild):
                print(f"⚠️ full member sync service unavailable for guild {getattr(guild, 'id', 'unknown')}")
                continue

            summary = await new_run_full_member_sync_for_guild(guild)
            print(
                f"✅ Initial member sync complete for guild {guild.id}: "
                f"active={int(summary.get('active_members_synced') or 0)} "
                f"marked_departed={int(summary.get('marked_departed') or 0)} "
                f"errors={int(summary.get('errors') or 0)}"
            )
        except Exception as e:
            print(f"⚠️ Initial member sync failed for guild {getattr(guild, 'id', 'unknown')}: {e}")


'''

MEMBER_SYNC_HEADER = '''# ============================================================
# Member sync service delegates
# ============================================================

'''

FORBIDDEN_EVENTS_MARKERS = (
    "def _guild_members_select_existing_sync",
    "def _guild_members_upsert_sync",
    "def _guild_members_update_member_sync",
    "def _guild_members_select_guild_rows_sync",
    "async def _bulk_mark_departed_members_async",
    "def _minimal_member_payload",
    "def _build_risk_payload_from_profile",
    "def _extract_existing_risk_payload",
    "def _derive_suspicion_flags_from_profile",
    "def _derive_alt_cluster_key_from_profile",
    "def _invite_meta(invite: discord.Invite)",
    "_INVITE_USES_CACHE",
    "_VANITY_USES_CACHE",
    "guild_members\").upsert(payload",
)

REQUIRED_SYNC_SERVICE_MARKERS = (
    "async def sync_member_to_supabase",
    "async def mark_member_left",
    "async def run_full_member_sync_for_guild",
    "async def run_departed_reconciliation_for_guild",
    "def _build_risk_payload_from_profile",
)

REQUIRED_JOIN_CONTEXT_MARKERS = (
    "def invite_meta",
    "async def warm_invite_cache_for_guild",
    "async def detect_join_entry_context",
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
        die(f"could not find start marker for {label}: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        die(f"could not find end marker for {label}: {end_marker!r}")
    current = text[start:end]
    if replacement.strip() in current:
        ok(f"{label} already applied")
        return text, False
    return text[:start] + replacement + text[end:], True


def remove_block(text: str, *, start_marker: str, end_marker: str, label: str) -> tuple[str, bool]:
    start = text.find(start_marker)
    if start < 0:
        ok(f"{label} already removed")
        return text, False
    end = text.find(end_marker, start)
    if end < 0:
        die(f"could not find end marker for {label}: {end_marker!r}")
    ok(f"removed {label}")
    return text[:start] + text[end:], True


def verify_services_ready() -> None:
    sync_service = read(SYNC_SERVICE)
    missing_sync = [m for m in REQUIRED_SYNC_SERVICE_MARKERS if m not in sync_service]
    if missing_sync:
        print("❌ members_new.sync_service is not ready:")
        for marker in missing_sync:
            print(" -", marker)
        raise SystemExit(1)

    join_service = read(JOIN_CONTEXT_SERVICE)
    missing_join = [m for m in REQUIRED_JOIN_CONTEXT_MARKERS if m not in join_service]
    if missing_join:
        print("❌ members_new.join_context_service is not ready:")
        for marker in missing_join:
            print(" -", marker)
        raise SystemExit(1)


def main() -> int:
    if not EVENTS.exists():
        die(f"missing {EVENTS}")
    verify_services_ready()

    text = read(EVENTS)
    changed = False

    # Remove dead risk-payload helpers now that the startup sync fallback is going away.
    text, did = remove_block(
        text,
        start_marker="def _as_float(v: Any, default: float = 0.0) -> float:\n",
        end_marker="def _startup_task_running(attr_name: str) -> bool:\n",
        label="dead risk payload helpers",
    )
    changed = changed or did

    # Keep VC session DB wrappers but remove old guild_members local DB ownership.
    text, did = remove_block(
        text,
        start_marker="def _guild_members_select_existing_sync(sb: Any, guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:\n",
        end_marker="# ============================================================\n# Verification ticket cleanup helpers\n",
        label="legacy guild_members DB wrappers",
    )
    changed = changed or did

    # Remove old local member payload helpers; member sync service owns these now.
    text, did = replace_block(
        text,
        start_marker="# ============================================================\n# Dashboard / Supabase member sync helpers\n",
        end_marker="async def _sync_member_to_supabase(\n",
        replacement=MEMBER_SYNC_HEADER,
        label="dead local member payload helpers",
    )
    changed = changed or did

    # Make initial sweep service-only and remove old local DB fallback path.
    text, did = replace_block(
        text,
        start_marker="async def _initial_member_sync_sweep() -> None:\n",
        end_marker="# ============================================================\n# Invite cache + entry-path persistence helpers\n",
        replacement=INITIAL_SWEEP_DELEGATE,
        label="initial member sync service-only delegate",
    )
    changed = changed or did

    # Remove old local invite metadata/cache helpers; join_context_service owns these now.
    text, did = remove_block(
        text,
        start_marker="# ============================================================\n# Invite cache + entry-path persistence helpers\n",
        end_marker="def _join_truth_quality(",
        label="dead local invite metadata helpers",
    )
    changed = changed or did

    offenders = [marker for marker in FORBIDDEN_EVENTS_MARKERS if marker in text]
    if offenders:
        print("❌ events.py still contains member-sync fallback markers:")
        for marker in offenders:
            print(" -", marker)
        return 1

    if changed:
        write(EVENTS, text)
        ok("updated stoney_verify/events.py")
    else:
        ok("member-sync fallback leftovers already cleaned")

    py_compile.compile(str(EVENTS), doraise=True)
    ok("compiled stoney_verify/events.py")
    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/cleanup_events_member_sync_fallbacks.py")
    print("  python -m py_compile stoney_verify/events.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Remove legacy member sync fallback leftovers"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
