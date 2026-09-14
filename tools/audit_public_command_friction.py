from __future__ import annotations

"""Audit public command surface and default-opening friction.

The canonical final product surface lives in ``stoney_verify.command_surface_contract``.
Implementation modules may register broader groups before final compaction, but
Discord must sync only the compact doorway commands plus the intentional direct
purge exception.

This audit intentionally validates the live command owners. Historical dormant
startup guards are not a command-surface dependency and are not treated as one.
"""

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stoney_verify.command_surface_contract import (  # noqa: E402
    PUBLIC_DANK_CHILDREN,
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
    PUBLIC_HIDDEN_DANK_CHILDREN,
)

ENV_EXAMPLE = ROOT / ".env.example"
PUBLIC_SETUP_AUDIT = ROOT / "tools" / "audit_public_setup.py"
PUBLIC_SURFACE_AUDIT = ROOT / "tools" / "audit_public_command_surface.py"
DIRECT_PURGE = ROOT / "stoney_verify" / "commands_ext" / "public_direct_purge.py"
FINAL_SURFACE = ROOT / "stoney_verify" / "commands_ext" / "public_exit_compact_surface.py"

REQUIRED_ENV_MARKERS = {
    "DANK_DEPLOYMENT_MODE=production",
    "DANK_PUBLIC_MODE=true",
    "DANK_PRODUCTION_MODE=true",
    "DANK_COMMAND_PROFILE=public",
    "DANK_SYNC_BETA_GUILD_COMMANDS=false",
    "CLEAR_GLOBAL_COMMANDS_ON_BOOT=false",
    "DANK_SKIP_UNCHANGED_GLOBAL_SYNC=true",
    "DANK_FORCE_COMMAND_SYNC_ON_BOOT=false",
    "DANK_PUBLIC_CONFIG_ISOLATION=true",
    "DANK_ALLOW_SERVER_ENV_IDS=false",
    "DANK_SERVER_ENV_IDS_ENABLED=false",
    "BOT_DISPLAY_NAME=Dank Shield",
}

FORBIDDEN_ENV_MARKERS = {
    "GUILD_ID=1098088221457514609",
    "DANK_SYNC_BETA_GUILD_COMMANDS=true",
    "CLEAR_GLOBAL_COMMANDS_ON_BOOT=true",
    "DANK_CLEAR_ANY_GUILD_COMMAND_COPY_ON_BOOT=true",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def literal_set_from_file(path: Path, assignment_name: str) -> set[str]:
    text = read(path)
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == assignment_name for t in node.targets):
            continue
        value = node.value
        if not isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            continue
        out: set[str] = set()
        for item in value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                out.add(item.value)
        return out
    return set()


def main() -> int:
    failures: list[str] = []

    expected = (
        "dank",
        "mod",
        "ticket",
        "tickets",
        "verify",
        "View Dank Profile",
    )
    if PUBLIC_GLOBAL_COMMAND_COUNT != len(expected) or PUBLIC_GLOBAL_COMMAND_NAMES != expected:
        failures.append(
            f"canonical global surface mismatch: count={PUBLIC_GLOBAL_COMMAND_COUNT} names={PUBLIC_GLOBAL_COMMAND_NAMES!r}"
        )
    if PUBLIC_DANK_CHILDREN != frozenset({"home", "purge", "setup", "upload"}):
        failures.append(f"canonical /dank children mismatch: {sorted(PUBLIC_DANK_CHILDREN)!r}")

    for path in (
        ENV_EXAMPLE,
        PUBLIC_SETUP_AUDIT,
        PUBLIC_SURFACE_AUDIT,
        DIRECT_PURGE,
        FINAL_SURFACE,
    ):
        if not path.exists():
            failures.append(f"missing required file: {path.relative_to(ROOT)}")

    hidden_direct_aliases = {
        name
        for name in PUBLIC_HIDDEN_DANK_CHILDREN
        if name in {
            "setup-access",
            "setup-assistant",
            "setup-defaults",
            "setup-find",
            "setup-logs",
            "setup-picker",
            "setup-review",
            "setup-status",
            "setup-tickets",
            "setup-verify",
            "setup-verify-ids",
            "db-check",
            "permission-check",
            "production-audit",
            "tickettool-check",
        }
    }
    if hidden_direct_aliases & PUBLIC_DANK_CHILDREN:
        failures.append(
            "hidden direct aliases leaked into canonical /dank children: "
            + ", ".join(sorted(hidden_direct_aliases & PUBLIC_DANK_CHILDREN))
        )

    purge_text = read(DIRECT_PURGE)
    for marker in (
        'name="purge"',
        'name="messages"',
        'name="members"',
        "cleanup_purge",
        "members_purge_all",
    ):
        if marker not in purge_text:
            failures.append(f"direct purge facade missing marker: {marker}")
    for forbidden in ("channel.history(", "scan_inactive_members(", "msg.delete(", "execute_member_cleanup("):
        if forbidden in purge_text:
            failures.append(f"direct purge facade duplicates canonical engine logic: {forbidden}")

    final_text = read(FINAL_SURFACE)
    for marker in (
        "install_direct_purge_group()",
        'expected_children = ["home", "purge", "setup", "upload"]',
        "DANK_PAYLOAD_SAFETY_LIMIT",
        "dank_payload_size(tree)",
    ):
        if marker not in final_text:
            failures.append(f"final command surface missing purge marker: {marker}")

    env_text = read(ENV_EXAMPLE)
    for marker in REQUIRED_ENV_MARKERS:
        if marker not in env_text:
            failures.append(f".env.example missing public production marker: {marker}")
    for marker in FORBIDDEN_ENV_MARKERS:
        if marker in env_text:
            failures.append(f".env.example contains unsafe public production marker: {marker}")

    if failures:
        print("Public command/friction audit failed:")
        for failure in failures:
            print(" -", failure)
        return 1

    print("Public command/friction audit passed.")
    print(f"Canonical globals: {PUBLIC_GLOBAL_COMMAND_COUNT} -> {', '.join(PUBLIC_GLOBAL_COMMAND_NAMES)}")
    print("Canonical /dank children: " + ", ".join(sorted(PUBLIC_DANK_CHILDREN)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
