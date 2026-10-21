from __future__ import annotations

"""Audit event-boundary ownership during the events.py split.

The huge stoney_verify.events module is being reduced safely over multiple PRs.
This audit locks the boundaries that already moved so future edits do not bring
back duplicate startup patches or event-owned removal decisions.
"""

import ast
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
    "async def block_or_run_bot_removal(",
    "try_recover_unverified_before_removal",
    "member_has_safe_verification_state",
    "post_fresh_join_recovery_log(",
    "handle_join_verification_failure",
)

# Do not demand that watchdog exhaustion invokes a kick as an event-boundary
# contract. After the October 21 DB outage, that call is a SECURITY REGRESSION.
# The native owner must instead raise a review without dispatching removal.

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


def _require_review_only_watchdog(source: str, failures: list[str]) -> None:
    """AST contract: the native watchdog failure handler never removes people.

    A syntactically present marker or a comment mentioning the old function
    cannot satisfy this audit. These checks operate on the actual handler AST.
    """
    try:
        tree = ast.parse(source, filename=str(JOIN_REMOVAL_SAFETY))
        handler = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "handle_join_verification_failure"
        )
        shared = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "block_or_run_bot_removal"
        )
    except (SyntaxError, StopIteration) as exc:
        failures.append(f"Cannot audit native verification handler: {type(exc).__name__}")
        return

    # Explicitly forbid any direct/indirect action that can kick/ban on
    # verification uncertainty, including a regression to old removal routing.
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            target = node.func
            called = (
                target.attr if isinstance(target, ast.Attribute)
                else target.id if isinstance(target, ast.Name) else ""
            )
            if called in {"kick", "ban", "block_or_run_bot_removal",
                          "_block_or_run_removal"}:
                failures.append(
                    f"Automatic verification failure handler calls forbidden {called}"
                )

    named_calls = {
        node.func.id if isinstance(node.func, ast.Name)
        else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        for node in ast.walk(handler) if isinstance(node, ast.Call)
    }
    if "post_fresh_join_recovery_log" not in named_calls:
        failures.append("Native watchdog must produce a staff-review record")

    # The shared last-mile guard must still block automatic verification
    # removals. Literal markers are backed by unit behavior tests in CI.
    shared_source = ast.get_source_segment(source, shared) or ""
    for marker in ("not staff_confirmed", "is_fail_closed_verification_reason(reason)",
                   "staff review required; no removal executed"):
        if marker not in shared_source:
            failures.append("Missing verification fail-closed final guard: " + marker)


def main() -> int:
    failures: list[str] = []

    events = _require_file(EVENTS, "stoney_verify/events.py", failures)
    event_safety = _require_file(EVENT_SAFETY, "startup_guards/event_safety.py", failures)
    fresh_join_recovery = _require_file(FRESH_JOIN_RECOVERY, "startup_guards/fresh_join_role_recovery.py", failures)
    join_removal_safety = _require_file(JOIN_REMOVAL_SAFETY, "members_new/join_removal_safety.py", failures)

    _require_markers(event_safety, REQUIRED_EVENT_SAFETY_MARKERS, "startup_guards/event_safety.py", failures)
    _forbid_markers(event_safety, FORBIDDEN_EVENT_SAFETY_MARKERS, "startup_guards/event_safety.py", failures)

    _require_markers(join_removal_safety, REQUIRED_JOIN_REMOVAL_SAFETY_MARKERS, "members_new/join_removal_safety.py", failures)
    _require_review_only_watchdog(join_removal_safety, failures)

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
