from __future__ import annotations

"""Audit raw Discord picker usage.

This script helps migrate Dank Shield toward the shared picker kit in
`stoney_verify.ui` without guessing where old one-off dropdowns still live.

Default mode is report-only and exits 0 so it is safe to run during cleanup.
Use `--fail` when the known raw picker list has been burned down and CI should
block new raw picker usage.
"""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RAW_PICKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("discord.ui.Select", re.compile(r"discord\.ui\.Select\b")),
    ("discord.ui.RoleSelect", re.compile(r"discord\.ui\.RoleSelect\b")),
    ("discord.ui.ChannelSelect", re.compile(r"discord\.ui\.ChannelSelect\b")),
    ("discord.ui.UserSelect", re.compile(r"discord\.ui\.UserSelect\b")),
    ("discord.ui.MentionableSelect", re.compile(r"discord\.ui\.MentionableSelect\b")),
    ("SelectOption", re.compile(r"discord\.SelectOption\b|\bSelectOption\b")),
)

IGNORED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
}

ALLOWED_FILES = {
    Path("stoney_verify/ui/picker.py"),
    Path("tools/audit_picker_adoption.py"),
}


@dataclass(frozen=True)
class Finding:
    path: Path
    line_no: int
    kind: str
    line: str


def _is_python_file(path: Path) -> bool:
    if path.suffix != ".py":
        return False
    return not any(part in IGNORED_PARTS for part in path.parts)


def scan() -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if not _is_python_file(rel) or rel in ALLOWED_FILES:
            continue

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        for line_no, line in enumerate(lines, start=1):
            for kind, pattern in RAW_PICKER_PATTERNS:
                if pattern.search(line):
                    findings.append(Finding(rel, line_no, kind, line.strip()))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit raw picker usage that should migrate to stoney_verify.ui."
    )
    parser.add_argument(
        "--fail",
        action="store_true",
        help="Exit 1 when raw picker usage is found.",
    )
    args = parser.parse_args()

    findings = scan()
    if not findings:
        print("✅ No raw Discord picker usage found outside the shared picker kit.")
        return 0

    print("⚠️ Raw Discord picker usage found. Migrate these to stoney_verify.ui wrappers:")
    for finding in findings:
        print(f"{finding.path}:{finding.line_no}: {finding.kind}: {finding.line}")

    print(f"\nTotal findings: {len(findings)}")
    return 1 if args.fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
