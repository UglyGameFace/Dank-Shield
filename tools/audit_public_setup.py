from __future__ import annotations

"""Static public setup audit for Dank Shield.

This is intentionally narrow: it checks user-facing setup/launch text for old
private-bot branding, known private server names/IDs, and dangerous setup-env
patterns. It does not fail on internal package names such as stoney_verify.
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

CHECK_FILES = [
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_solid.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_group.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_start.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_feature_health_scoreboard.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_scoreboard_command.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_verification_idle_kick_controls.py",
    ROOT / "docs" / "public-production-env.md",
    ROOT / ".env.example",
]

# These are okay in internal identifiers/comments, but should not appear in text
# users see during public setup.
FORBIDDEN_PUBLIC_TEXT = [
    r"/stoney\b",
    r"Stoney Verify",
    r"Stoney Balonney",
    r"The 420 Lobby",
    r"DickHeads",
]

# Known old/private guild IDs from production debugging. Public setup source must
# never bake these into code or docs.
FORBIDDEN_IDS = [
    "1098088221457514609",
    "1232631147649830992",
    "1317042307903651901",
    "1357215261001912320",
    "1514374173517152418",
]

ALLOWED_STONEY_INTERNAL_PATTERNS = [
    "custom_id=\"stoney_",
    "custom_id='stoney_",
    "stoney_group",
    "stoney_verify",
    "STONEY_",
    "STONEY_GUILD_CONFIG_TABLE",
]


def _line_allowed(line: str) -> bool:
    return any(pattern in line for pattern in ALLOWED_STONEY_INTERNAL_PATTERNS)


def main() -> int:
    failures: list[str] = []

    for path in CHECK_FILES:
        if not path.exists():
            continue
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            if _line_allowed(line):
                continue
            for pattern in FORBIDDEN_PUBLIC_TEXT:
                if re.search(pattern, line):
                    failures.append(f"{rel}:{idx}: forbidden public setup text `{pattern}` -> {line.strip()[:180]}")
            for private_id in FORBIDDEN_IDS:
                if private_id in line:
                    failures.append(f"{rel}:{idx}: forbidden private guild/server ID `{private_id}`")

    if failures:
        print("Public setup audit failed:")
        for item in failures:
            print(" -", item)
        return 1

    print("Public setup audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
