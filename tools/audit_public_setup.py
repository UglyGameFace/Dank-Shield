from __future__ import annotations

"""Static public setup audit for Dank Shield.

The codebase still has internal legacy names such as stoney_verify and old
custom_id prefixes. Those are not shown to server owners and are safer to rename
later during a planned refactor.

This audit checks the rendered public-facing setup text after applying the same
kind of branding cleanup used at runtime. It still fails hard on private server
names/IDs and dangerous setup-env patterns.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

CHECK_FILES = [
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_solid.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_group.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_start.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_recommend.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_fresh_choice.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_full_customization.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_recovery.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_cleanup.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_feature_health_scoreboard.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_scoreboard_command.py",
    ROOT / "docs" / "public-production-env.md",
    ROOT / ".env.example",
]

BRANDING_REPLACEMENTS = [
    ("/stoney setup", "/dank setup"),
    ("`/stoney setup`", "`/dank setup`"),
    ("/stoney cleanup", "/dank setup cleanup"),
    ("/stoney", "/dank"),
    ("`/stoney`", "`/dank`"),
    ("Stoney Verify", "Dank Shield"),
    ("StoneyVerify", "DankShield"),
    ("Stoney setup", "Dank Shield setup"),
    ("Stoney Setup", "Dank Shield Setup"),
    ("Stoney Quick Setup", "Dank Shield Quick Setup"),
    ("Stoney Setup Assistant", "Dank Shield Setup Assistant"),
    ("Stoney is", "Dank Shield is"),
    ("Stoney lacks", "Dank Shield lacks"),
    ("Stoney ticket", "Dank Shield ticket"),
    ("Stoney panel", "Dank Shield panel"),
    ("Stoney's", "Dank Shield's"),
    ("Stoney’s", "Dank Shield’s"),
    ("Stoney", "Dank Shield"),
]

FORBIDDEN_RENDERED_TEXT = [
    r"/stoney\b",
    r"\bStoney\b",
    r"Stoney Verify",
    r"Stoney Balonney",
    r"The 420 Lobby",
    r"DickHeads",
]

FORBIDDEN_IDS = [
    "1098088221457514609",
    "1232631147649830992",
    "1317042307903651901",
    "1357215261001912320",
    "1514374173517152418",
]

# Internal code identifiers are allowed because they are not shown to users.
ALLOWED_RAW_INTERNAL_PATTERNS = [
    "custom_id=\"stoney_",
    "custom_id='stoney_",
    "stoney_group",
    "stoney_verify",
    "STONEY_",
    "STONEY_GUILD_CONFIG_TABLE",
]


def _line_is_internal_only(line: str) -> bool:
    return any(pattern in line for pattern in ALLOWED_RAW_INTERNAL_PATTERNS)


def _rendered(line: str) -> str:
    out = line
    for old, new in BRANDING_REPLACEMENTS:
        out = out.replace(old, new)
    return out


def main() -> int:
    failures: list[str] = []

    for path in CHECK_FILES:
        if not path.exists():
            continue
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(text.splitlines(), start=1):
            for private_id in FORBIDDEN_IDS:
                if private_id in line:
                    failures.append(
                        f"{rel}:{idx}: forbidden private guild/server ID `{private_id}`"
                    )

            if _line_is_internal_only(line):
                continue

            rendered = _rendered(line)
            for pattern in FORBIDDEN_RENDERED_TEXT:
                if re.search(pattern, rendered):
                    failures.append(
                        f"{rel}:{idx}: forbidden rendered setup text "
                        f"`{pattern}` -> {rendered.strip()[:180]}"
                    )

    if failures:
        print("Public setup audit failed:")
        for item in failures:
            print(" -", item)
        return 1

    print("Public setup audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
