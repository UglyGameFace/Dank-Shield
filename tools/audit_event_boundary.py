from __future__ import annotations

"""Audit event-boundary ownership during the events.py split.

The huge stoney_verify.events module is being reduced safely over multiple PRs.
This audit locks the boundaries that already moved so future edits do not bring
back duplicate startup patches or event-owned removal decisions.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EVENTS = ROOT / "stoney_verify" / "events.py"
EVENT_SAFETY = ROOT / "stoney_verify" / "startup_guards" / "event_safety.py"
FRESH_JOIN_RECOVERY = ROOT / "stoney_verify" / "startup_guards" / "fresh_join_role_recovery.py"
JOIN_REMOVAL_SAFETY = ROOT / "stoney_verify" / "members_new" / "join_removal_safety.py"

REQUIRED_EVENT_SAFETY_MARKERS = (
    "def _patch_member_role_snapshot",
    "_member_role_snapshot->role_truth",
    "def _patch_join_verification_failure",
    "_handle_join_verification_failure->join_removal_safety",
    "handle_join_verification_failure(member, reason)",
)

REQUIRED_JOIN_REMOVAL_SAFETY_MARKERS = (
    "async def handle_join_verification_failure",
    "await block_or_run_bot_removal(",
    "try_recover_unverified_before_removal",
    "member_has_safe_verification_state",
    "handle_join_verification_failure",
)

REQUIRED_FRESH_JOIN_RECOVERY_MARKERS = (
    "async def ensure_fresh_join_unverified_role",
    "async def _on_member_join_recover_role",
    "bot.add_listener(_on_member_join_recover_role, \"on_member_join\")",
)

FORBIDDEN_FRESH_JOIN_RECOVERY_MARKERS = (
    "def _patch_join_removal_safety",
    "def _patch_events_fail_closed",
    "block_or_run_bot_removal",
    "_handle_join_verification_failure",
    "_fresh_join_role_recovery_wrapped",
)

FORBIDDEN_EVENT_SAFETY_MARKERS = (
    "member.kick(",
    "member.ban(",
    "Verification Safety Fail-Closed",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _require_file(path: Path, label: str, failures: list[str]) -> str:
    text = _read(path)
    if not text:
        failures.append(f"{label} is missing or empty")
    return text


def _require_markers(text: str, markers: tuple[str, ...], label: str, failures: list[str]) -> None:
    for marker in markers:
        if marker not in text:
            failures.append(f"{label} missing required boundary marker: {marker}")


def _forbid_markers(text: str, markers: tuple[str, ...], label: str, failures: list[str]) -> None:
    for marker in markers:
        if marker in text:
            failures.append(f"{label} must not contain duplicate ownership marker: {marker}")


def main() -> int:
    failures: list[str] = []

    events = _require_file(EVENTS, "stoney_verify/events.py", failures)
    event_safety = _require_file(EVENT_SAFETY, "startup_guards/event_safety.py", failures)
    fresh_join_recovery = _require_file(FRESH_JOIN_RECOVERY, "startup_guards/fresh_join_role_recovery.py", failures)
    join_removal_safety = _require_file(JOIN_REMOVAL_SAFETY, "members_new/join_removal_safety.py", failures)

    _require_markers(event_safety, REQUIRED_EVENT_SAFETY_MARKERS, "startup_guards/event_safety.py", failures)
    _forbid_markers(event_safety, FORBIDDEN_EVENT_SAFETY_MARKERS, "startup_guards/event_safety.py", failures)

    _require_markers(join_removal_safety, REQUIRED_JOIN_REMOVAL_SAFETY_MARKERS, "members_new/join_removal_safety.py", failures)

    _require_markers(fresh_join_recovery, REQUIRED_FRESH_JOIN_RECOVERY_MARKERS, "startup_guards/fresh_join_role_recovery.py", failures)
    _forbid_markers(fresh_join_recovery, FORBIDDEN_FRESH_JOIN_RECOVERY_MARKERS, "startup_guards/fresh_join_role_recovery.py", failures)

    if "async def _handle_join_verification_failure" not in events:
        failures.append("events.py no longer has the legacy fail-closed handler; remove the event_safety routing audit/update before passing.")
    if "async def handle_join_verification_failure" not in join_removal_safety:
        failures.append("join_removal_safety.py must own the native fail-closed handler")

    if failures:
        print("Event boundary audit failed:")
        for item in failures:
            print(" -", item)
        return 1

    print("Event boundary audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
