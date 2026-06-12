from __future__ import annotations

"""Audit per-guild role truth ownership.

Verification/member role state must be owned by stoney_verify.role_truth. Runtime
startup bridges are not allowed for member role truth anymore.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROLE_TRUTH = ROOT / "stoney_verify" / "role_truth.py"
BRIDGE = ROOT / "stoney_verify" / "startup_guards" / "per_guild_role_truth_guard.py"
STARTUP_LOADER = ROOT / "stoney_verify" / "startup_guards" / "__init__.py"
MEMBER_SERVICE = ROOT / "stoney_verify" / "members_new" / "service.py"
SYNC_SERVICE = ROOT / "stoney_verify" / "members_new" / "sync_service.py"
EVENTS = ROOT / "stoney_verify" / "events.py"

FORBIDDEN_GLOBAL_ROLE_MARKERS = (
    "UNVERIFIED_ROLE_ID",
    "VERIFIED_ROLE_ID",
    "RESIDENT_ROLE_ID",
    "STAFF_ROLE_ID",
    "STONER_ROLE_ID",
    "DRUNKEN_ROLE_ID",
    "def _configured_role_ids",
)

REQUIRED_ROLE_TRUTH_MARKERS = (
    "def member_role_truth",
    "def member_is_pending_verification",
    "def member_has_any_safe_access_role",
    "def apply_truth_to_snapshot",
    "def base_member_role_snapshot",
    "def build_member_role_snapshot",
    "SAFE_ROLE_KEYS",
    "PENDING_ROLE_KEYS",
    "get_guild_role_config",
    "It does not fall back to deployment role IDs.",
)

REQUIRED_EVENTS_ROLE_TRUTH_MARKERS = (
    "from . import role_truth",
    "return role_truth.member_has_role_id(member, role_id)",
    "role_truth.member_has_any_safe_access_role(",
    "return bool(role_truth.member_is_pending_verification(member))",
    "def _member_role_snapshot(member: discord.Member) -> Dict[str, Any]:\n    return role_truth.build_member_role_snapshot(member)",
)

FORBIDDEN_EVENTS_LEGACY_ROLE_TRUTH_MARKERS = (
    "if VERIFIED_ROLE_ID and _member_has_role_id(member, int(VERIFIED_ROLE_ID)):",
    "if RESIDENT_ROLE_ID and _member_has_role_id(member, int(RESIDENT_ROLE_ID)):",
    "if STAFF_ROLE_ID and _member_has_role_id(member, int(STAFF_ROLE_ID)):",
    "if STONER_ROLE_ID and _member_has_role_id(member, int(STONER_ROLE_ID)):",
    "if DRUNKEN_ROLE_ID and _member_has_role_id(member, int(DRUNKEN_ROLE_ID)):",
    "verified_id = int(VERIFIED_ROLE_ID or 0)",
    "has_unverified = _member_has_role_id(member, uv_id) if uv_id else False",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _assert_no_global_role_truth(path: Path, label: str, failures: list[str]) -> None:
    text = _read(path)
    for marker in FORBIDDEN_GLOBAL_ROLE_MARKERS:
        if marker in text:
            failures.append(f"{label} must not own global role truth: {marker}")


def main() -> int:
    failures: list[str] = []

    role_truth = _read(ROLE_TRUTH)
    if not role_truth:
        failures.append("stoney_verify/role_truth.py is missing")
    for marker in REQUIRED_ROLE_TRUTH_MARKERS:
        if marker not in role_truth:
            failures.append(f"stoney_verify/role_truth.py missing marker: {marker}")

    if BRIDGE.exists():
        failures.append("startup_guards/per_guild_role_truth_guard.py must not exist")

    startup_loader = _read(STARTUP_LOADER)
    if "per_guild_role_truth_guard" in startup_loader:
        failures.append("startup guard loader must not load per_guild_role_truth_guard")

    service = _read(MEMBER_SERVICE)
    if "from .. import role_truth" not in service:
        failures.append("members_new/service.py must import native role_truth")
    _assert_no_global_role_truth(MEMBER_SERVICE, "members_new/service.py", failures)

    sync_service = _read(SYNC_SERVICE)
    if "role_truth.build_member_role_snapshot(member)" not in sync_service:
        failures.append("members_new/sync_service.py must use role_truth.build_member_role_snapshot(member)")
    _assert_no_global_role_truth(SYNC_SERVICE, "members_new/sync_service.py", failures)

    events = _read(EVENTS)
    if not events:
        failures.append("stoney_verify/events.py is missing")
    for marker in REQUIRED_EVENTS_ROLE_TRUTH_MARKERS:
        if marker not in events:
            failures.append(f"stoney_verify/events.py must delegate role truth to role_truth: {marker}")
    for marker in FORBIDDEN_EVENTS_LEGACY_ROLE_TRUTH_MARKERS:
        if marker in events:
            failures.append(f"stoney_verify/events.py still has legacy global role truth helper logic: {marker}")

    if failures:
        print("Role truth audit failed:")
        for item in failures:
            print(" -", item)
        return 1

    print("Role truth audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
