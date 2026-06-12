#!/usr/bin/env python3
from __future__ import annotations

"""Physically route VC session runtime helpers out of events.py.

Run after stoney_verify/verification_new/vc_session_runtime_service.py exists.
"""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SERVICE = ROOT / "stoney_verify" / "verification_new" / "vc_session_runtime_service.py"

VC_RUNTIME_DELEGATES = '''def _vc_runtime_deps():
    from .verification_new.vc_session_runtime_service import VcRuntimeDeps

    return VcRuntimeDeps(
        vc_sessions=vc_sessions,
        vc_requests=VC_REQUESTS,
        resolve_vc_verify_channel=_resolve_vc_verify_channel,
        fetch_active_session_rows=_fetch_active_vc_session_rows,
        can_manage_channel=_can_manage_channel,
        as_int=_as_int,
        vc_row_token=_vc_row_token,
        vc_row_status=_vc_row_status,
        vc_owner_id_from_row=_vc_owner_id_from_row,
        vc_staff_ids_from_row=_vc_staff_ids_from_row,
        vc_meta_dict=_vc_meta_dict,
        member_in_target_voice=_member_in_target_voice,
    )


async def _vc_channel_is_empty(channel: discord.abc.GuildChannel) -> bool:
    from .verification_new.vc_session_runtime_service import vc_channel_is_empty

    return await vc_channel_is_empty(channel)


async def _vc_relock_session_channel(
    guild: discord.Guild,
    row: Dict[str, Any],
    *,
    reason: str = "vc session ended",
) -> bool:
    from .verification_new.vc_session_runtime_service import relock_session_channel

    return await relock_session_channel(
        guild,
        row,
        reason=reason,
        deps=_vc_runtime_deps(),
    )


async def _vc_mark_session_completed(
    guild: discord.Guild,
    row: Dict[str, Any],
) -> None:
    from .verification_new.vc_session_runtime_service import mark_session_completed

    await mark_session_completed(guild, row, deps=_vc_runtime_deps())


async def _vc_touch_session_activity(
    guild: discord.Guild,
    row: Dict[str, Any],
    *,
    reason: str,
) -> None:
    from .verification_new.vc_session_runtime_service import touch_session_activity

    await touch_session_activity(guild, row, reason=reason, deps=_vc_runtime_deps())


async def _vc_mark_owner_confirmed_if_needed(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    verify_vc_id: int,
) -> None:
    from .verification_new.vc_session_runtime_service import mark_owner_confirmed_if_needed

    await mark_owner_confirmed_if_needed(
        row,
        owner,
        verify_vc_id,
        deps=_vc_runtime_deps(),
    )


async def _vc_mark_started_if_needed(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    staff_members: List[discord.Member],
    verify_vc_id: int,
) -> None:
    from .verification_new.vc_session_runtime_service import mark_started_if_needed

    await mark_started_if_needed(
        row,
        owner,
        staff_members,
        verify_vc_id,
        deps=_vc_runtime_deps(),
    )


async def _vc_sync_runtime_request_state(
    row: Dict[str, Any],
    owner: Optional[discord.Member],
    staff_members: List[discord.Member],
    verify_vc_id: int,
) -> None:
    from .verification_new.vc_session_runtime_service import sync_runtime_request_state

    await sync_runtime_request_state(
        row,
        owner,
        staff_members,
        verify_vc_id,
        deps=_vc_runtime_deps(),
    )


async def _maybe_finish_vc_sessions_after_voice_change(
    guild: discord.Guild,
    changed_channel_ids: set[int],
) -> None:
    from .verification_new.vc_session_runtime_service import maybe_finish_vc_sessions_after_voice_change

    await maybe_finish_vc_sessions_after_voice_change(
        guild,
        changed_channel_ids,
        deps=_vc_runtime_deps(),
    )


'''

REQUIRED_SERVICE_MARKERS = (
    "class VcRuntimeDeps",
    "async def relock_session_channel",
    "async def mark_session_completed",
    "async def touch_session_activity",
    "async def mark_owner_confirmed_if_needed",
    "async def mark_started_if_needed",
    "async def sync_runtime_request_state",
    "async def maybe_finish_vc_sessions_after_voice_change",
)

FORBIDDEN_EVENTS_MARKERS = (
    "VC relock skipped: bot member missing",
    "Failed clearing VC overwrite for owner",
    "Failed clearing VC overwrite for staff",
    "VC session finalize loop error",
    "VC session live-state reconcile error",
    "_maybe_finish_vc_sessions_after_voice_change error",
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


def verify_service_ready() -> None:
    service = read(SERVICE)
    missing = [marker for marker in REQUIRED_SERVICE_MARKERS if marker not in service]
    if missing:
        print("❌ VC runtime service is not ready:")
        for marker in missing:
            print(" -", marker)
        raise SystemExit(1)


def main() -> int:
    if not EVENTS.exists():
        die(f"missing {EVENTS}")
    if not SERVICE.exists():
        die(f"missing {SERVICE}")

    verify_service_ready()
    text = read(EVENTS)

    text, changed = replace_block(
        text,
        start_marker="async def _vc_channel_is_empty(channel: discord.abc.GuildChannel) -> bool:\n",
        end_marker="# Guards\n",
        replacement=VC_RUNTIME_DELEGATES,
        label="events VC runtime service delegates",
    )

    offenders = [marker for marker in FORBIDDEN_EVENTS_MARKERS if marker in text]
    if offenders:
        print("❌ events.py still contains VC runtime ownership markers:")
        for marker in offenders:
            print(" -", marker)
        return 1

    if changed:
        write(EVENTS, text)
        ok("updated stoney_verify/events.py VC runtime ownership")
    else:
        ok("events.py VC runtime delegates already present")

    py_compile.compile(str(EVENTS), doraise=True)
    py_compile.compile(str(SERVICE), doraise=True)
    ok("compiled events.py and vc_session_runtime_service.py")

    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/apply_vc_runtime_service_handoff.py")
    print("  python -m py_compile stoney_verify/events.py stoney_verify/verification_new/vc_session_runtime_service.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Physically hand off VC session runtime"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
