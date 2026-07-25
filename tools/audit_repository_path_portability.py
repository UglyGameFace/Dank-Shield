#!/usr/bin/env python3
"""Reject tracked paths that cannot be cloned safely by common CI platforms."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath


WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
INVALID_WINDOWS_CHARS = re.compile(r'[<>:"\\|?*]')
CONTROL_CHARS = re.compile(r"[\x00-\x1f]")


def tracked_paths() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-tree", "-rz", "--name-only", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [
        raw.decode("utf-8", "surrogateescape")
        for raw in completed.stdout.split(b"\0")
        if raw
    ]


def component_problem(component: str) -> str | None:
    if component in {"", ".", ".."}:
        return "empty or traversal component"
    if component.endswith((" ", ".")):
        return "trailing space or period"
    if INVALID_WINDOWS_CHARS.search(component):
        return "contains a Windows-invalid character"
    if CONTROL_CHARS.search(component):
        return "contains a control character"
    stem = component.split(".", 1)[0].rstrip(" .").lower()
    if stem in WINDOWS_RESERVED:
        return "uses a Windows-reserved device name"
    return None


def main() -> int:
    failures: list[tuple[str, str]] = []
    for path in tracked_paths():
        if PurePosixPath(path).is_absolute():
            failures.append((path, "absolute path"))
            continue
        for component in path.split("/"):
            problem = component_problem(component)
            if problem:
                failures.append((path, problem))
                break

    if failures:
        print("FAIL: repository contains non-portable tracked paths:")
        for path, reason in failures:
            print(f"- {path!r}: {reason}")
        return 1

    print(f"PASS: {len(tracked_paths())} tracked paths are portable across GitHub/Supabase clone environments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
