from __future__ import annotations

"""Audit per-guild role truth ownership.

Verification/member role state must be owned by stoney_verify.role_truth. Legacy
bridges may call that module, but should not carry duplicate config/role-truth
implementations or read deployment globals as the source of truth.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ROLE_TRUTH = ROOT / "stoney_verify" / "role_truth.py"
BRIDGE = ROOT / "stoney_verify" / "startup_guards" / "per_guild_role_truth_guard.py"
MEMBER_SERVICE = ROOT / "stoney_verify" / "members_new" / "service.py"
SYNC_SERVICE = ROOT / "stoney_verify" / "members_new" / "sync_service.py"

FORBIDDEN_IN_BRIDGE = (
    "STONEY_GUILD_CONFIG_TABLE",
    "get_supabase",
    "_db_guild_config",
    "_CFG_CACHE",
    "_SAFE_KEYS",
    "_PENDING_KEYS",
    "def _role_truth",
    "apply_truth_to_snapshot(member, base)",
)

FORBIDDEN_MEMBER_SERVICE_GLOBAL_ROLE_MARKERS = (
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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def main() -> int:
    failures: list[str] = []

    role_truth = _read(ROLE_TRUTH)
    if not role_truth:
        failures.append("stoney_verify/role_truth.py is missing")
    for marker in REQUIRED_ROLE_TRUTH_MARKERS:
        if marker not in role_truth:
            failures.append(f"stoney_verify/role_truth.py missing marker: {marker}")

    bridge = _read(BRIDGE)
    if "from stoney_verify import role_truth" not in bridge:
        failures.append("per_guild_role_truth_guard must bridge to stoney_verify.role_truth")
    if "role_truth.build_member_role_snapshot" not in bridge:
        failures.append("per_guild_role_truth_guard must use native build_member_role_snapshot")
    for marker in FORBIDDEN_IN_BRIDGE:
        if marker in bridge:
            failures.append(f"per_guild_role_truth_guard carries duplicate/native-owned logic: {marker}")

    service = _read(MEMBER_SERVICE)
    if "from .. import role_truth" not in service:
        failures.append("members_new/service.py must import native role_truth")
    for marker in FORBIDDEN_MEMBER_SERVICE_GLOBAL_ROLE_MARKERS:
        if marker in service:
            failures.append(f"members_new/service.py must not own global role truth: {marker}")

    sync_service = _read(SYNC_SERVICE)
    if "UNVERIFIED_ROLE_ID" in sync_service or "VERIFIED_ROLE_ID" in sync_service:
        print("Role truth audit notice: members_new/sync_service.py still has legacy global role snapshot code; bridge must remain loaded until it is refactored.")

    if failures:
        print("Role truth audit failed:")
        for item in failures:
            print(" -", item)
        return 1

    print("Role truth audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
