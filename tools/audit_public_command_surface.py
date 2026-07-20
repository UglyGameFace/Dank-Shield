from __future__ import annotations

"""Permanent drift audit for Dank Shield's normal public command profile."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stoney_verify.command_surface_contract import (  # noqa: E402
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def main() -> int:
    errors: list[str] = []

    if PUBLIC_GLOBAL_COMMAND_COUNT != 9:
        errors.append(f"public global count drifted: expected 9, got {PUBLIC_GLOBAL_COMMAND_COUNT}")

    expected = (
        "dank",
        "mod",
        "ticket",
        "tickets",
        "ticket-intake",
        "ticket-category",
        "ticket-panel",
        "verify",
        "View Dank Profile",
    )
    if PUBLIC_GLOBAL_COMMAND_NAMES != expected:
        errors.append(f"public global command names drifted: {PUBLIC_GLOBAL_COMMAND_NAMES!r}")

    access = _read("stoney_verify/commands_ext/public_access_control.py")
    review = _read("stoney_verify/commands_ext/public_setup_review.py")
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

    for name in expected:
        if name not in docs:
            errors.append(f"public production docs do not list {name!r}")

    if errors:
        print("PUBLIC COMMAND SURFACE AUDIT FAILED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("PUBLIC COMMAND SURFACE AUDIT OK")
    print(f"global_count={PUBLIC_GLOBAL_COMMAND_COUNT}")
    print("commands=" + ", ".join(PUBLIC_GLOBAL_COMMAND_NAMES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
