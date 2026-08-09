from __future__ import annotations

"""Permanent drift audit for Dank Shield's final normal public command profile."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stoney_verify.command_surface_contract import (  # noqa: E402
    PUBLIC_DANK_CHILDREN,
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    errors: list[str] = []

    expected = (
        "dank",
        "mod",
        "ticket",
        "tickets",
        "verify",
        "View Dank Profile",
    )
    if PUBLIC_GLOBAL_COMMAND_COUNT != len(expected):
        errors.append(
            f"public global count drifted: expected {len(expected)}, got {PUBLIC_GLOBAL_COMMAND_COUNT}"
        )
    if PUBLIC_GLOBAL_COMMAND_NAMES != expected:
        errors.append(f"public global command names drifted: {PUBLIC_GLOBAL_COMMAND_NAMES!r}")
    if PUBLIC_DANK_CHILDREN != frozenset({"home", "purge", "upload"}):
        errors.append(f"final /dank children drifted: {sorted(PUBLIC_DANK_CHILDREN)!r}")

    access = _read("stoney_verify/commands_ext/public_access_control.py")
    review = _read("stoney_verify/commands_ext/public_setup_review.py")
    surface = _read("stoney_verify/commands_ext/public_command_surface_v2.py")
    exit_surface = _read("stoney_verify/commands_ext/public_exit_compact_surface.py")
    purge_surface = _read("stoney_verify/commands_ext/public_direct_purge.py")
    docs = _read("docs/public-production-env.md")

    if "_SETUP_PERMISSION_MODULES" in access:
        errors.append("public_access_control still imports advanced setup modules for permission patching")

    advanced_imports = (
        "public_setup_logs",
        "public_setup_by_id",
        "public_setup_picker",
        "public_setup_find",
        "public_setup_review",
    )
    for module in advanced_imports:
        if f'"{module}"' in access or f"'{module}'" in access:
            errors.append(f"public_access_control still references advanced registrar module {module}")

    if "def register_public_setup_review_commands" not in review:
        errors.append("public_setup_review has no explicit registrar")

    tail = review[review.rfind("__all__") :]
    if "attach_setup_review_commands()" in tail:
        errors.append("public_setup_review still attaches advanced commands unconditionally at import time")

    required_surface_markers = (
        "install_compact_public_surface_v2",
        'for retired_root in ("ticket-intake", "ticket-category", "ticket-panel")',
        'dank_children != ["home", "upload"]',
        'expected_roots = {"dank", "mod", "ticket", "tickets", "verify"}',
    )
    for marker in required_surface_markers:
        if marker not in surface:
            errors.append(f"compact-v2 surface missing marker: {marker}")

    required_exit_markers = (
        "compact_surface._INSTALLED = False",
        "compact_surface.install_compact_public_surface_v2(bot, tree)",
        "install_lifecycle_menu_compat()",
        "install_direct_purge_group()",
        'expected_children = ["home", "purge", "upload"]',
    )
    for marker in required_exit_markers:
        if marker not in exit_surface:
            errors.append(f"final command reassertion missing marker: {marker}")

    for marker in (
        "from .public_cleanup_group import cleanup_purge",
        "from .public_members_cleanup_group import members_purge_all",
        'name="messages"',
        'name="members"',
    ):
        if marker not in purge_surface:
            errors.append(f"direct purge facade missing marker: {marker}")

    for name in expected:
        if name not in docs:
            errors.append(f"public production docs do not list {name!r}")
    for retired in ("/ticket-intake", "/ticket-category", "/ticket-panel", "/dank welcome"):
        if f"{retired} —" in docs or f"{retired}` —" in docs:
            errors.append(f"public production docs still advertise retired command root {retired}")

    if errors:
        print("PUBLIC COMMAND SURFACE AUDIT FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("PUBLIC COMMAND SURFACE AUDIT OK")
    print(f"global_count={PUBLIC_GLOBAL_COMMAND_COUNT}")
    print("commands=" + ", ".join(PUBLIC_GLOBAL_COMMAND_NAMES))
    print("dank_children=" + ", ".join(sorted(PUBLIC_DANK_CHILDREN)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
