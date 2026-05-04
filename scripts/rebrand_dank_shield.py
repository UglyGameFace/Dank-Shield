#!/usr/bin/env python3
from __future__ import annotations

"""
Dank Shield rebrand migration.

This edits the actual project files in-place. It does NOT add wrapper modules,
monkey patches, or runtime shims.

Run from the repo root:

    python scripts/rebrand_dank_shield.py

Then redeploy.

Safety rules:
- Rebrands visible product text.
- Keeps internal Python package/import names like stoney_verify unchanged.
- Keeps Discord custom IDs / markers like stoney_verify:verify_ui:v9 unchanged.
- Keeps /stoney command names unchanged for now so existing slash command routing
  and setup docs do not break mid-refactor.
"""

import re
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]

BRAND_NAME = "Dank Shield"
VERIFY_TITLE = "Dank Shield Verification"
TAGLINE_SHORT = "Good vibes in. Bad actors out."
TAGLINE_LONG = "Verification, tickets, and server protection without snuffing out the vibe."

TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".env",
    ".example",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".config",
}

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}

REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Dank Shield Verification", VERIFY_TITLE),
    ("Dank Shield Verification", VERIFY_TITLE),
    ("Dank Shield Verification", VERIFY_TITLE),
    ("Dank Shield ticket panel", f"{BRAND_NAME} ticket panel"),
    ("Dank Shield Ticket Panel", f"{BRAND_NAME} Ticket Panel"),
    ("Dank Shield dashboard", f"{BRAND_NAME} dashboard"),
    ("Dank Shield Dashboard", f"{BRAND_NAME} Dashboard"),
    ("Dank Shield helper", f"{BRAND_NAME} helper"),
    ("Dank Shield Helper", f"{BRAND_NAME} Helper"),
    ("Dank Shield", BRAND_NAME),
    ("Dank-Shield", "Dank-Shield"),
    ("Dank Shield", BRAND_NAME),
    ("dank-shield-helper", "dank-shield-helper"),
)

ENV_LINES = {
    "BOT_DISPLAY_NAME": BRAND_NAME,
    "VERIFY_UI_TITLE": VERIFY_TITLE,
    "DANK_SHIELD_TAGLINE_SHORT": TAGLINE_SHORT,
    "DANK_SHIELD_TAGLINE_LONG": TAGLINE_LONG,
}


def should_scan(path: Path) -> bool:
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return False
    if path.name.startswith(".") and path.name not in {".env", ".env.example"}:
        return False
    if path.suffix in TEXT_EXTENSIONS:
        return True
    if path.name in {"Dockerfile", "Procfile", "discloud.config", ".env.example", ".env"}:
        return True
    return False


def iter_files() -> Iterable[Path]:
    for path in ROOT.rglob("*"):
        if path.is_file() and should_scan(path):
            yield path


def safe_read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except Exception:
        return None


def safe_write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def update_verify_ui_title(text: str, path: Path) -> str:
    normalized = path.as_posix()
    if normalized.endswith("stoney_verify/verify_ui.py"):
        text = re.sub(
            r'VERIFY_UI_TITLE\s*=\s*["\'][^"\']*["\']',
            'VERIFY_UI_TITLE = os.getenv("VERIFY_UI_TITLE", "Dank Shield Verification")',
            text,
            count=1,
        )
    return text


def update_env_text(text: str) -> str:
    lines = text.splitlines()
    existing_keys = set()

    for idx, line in enumerate(lines):
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in ENV_LINES:
            lines[idx] = f"{key}={ENV_LINES[key]}"
            existing_keys.add(key)

    missing = [(k, v) for k, v in ENV_LINES.items() if k not in existing_keys]
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# Dank Shield branding")
        for key, value in missing:
            lines.append(f"{key}={value}")

    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def update_text(path: Path, text: str) -> tuple[str, list[str]]:
    before = text
    hits: list[str] = []

    text = update_verify_ui_title(text, path)
    if text != before:
        hits.append("VERIFY_UI_TITLE")

    for old, new in REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
            hits.append(old)

    if path.name in {".env.example", ".env"} or path.name.endswith(".env.example"):
        after = text
        text = update_env_text(text)
        if text != after:
            hits.append("branding env")

    return text, hits


def main() -> int:
    changed: list[tuple[Path, list[str]]] = []

    for path in iter_files():
        text = safe_read(path)
        if text is None:
            continue

        new_text, hits = update_text(path, text)
        if new_text != text:
            safe_write(path, new_text)
            changed.append((path, hits))

    print("Dank Shield rebrand complete.")
    print(f"Files changed: {len(changed)}")
    for path, hits in changed:
        rel = path.relative_to(ROOT)
        unique_hits = []
        for hit in hits:
            if hit not in unique_hits:
                unique_hits.append(hit)
        print(f" - {rel}: {', '.join(unique_hits)}")

    if not changed:
        print("No visible brand strings were found to change.")

    print()
    print("Brand:")
    print(f" - {BRAND_NAME} — {TAGLINE_SHORT}")
    print(f" - {TAGLINE_LONG}")
    print()
    print("Next steps:")
    print("  1. Review git diff.")
    print("  2. Make sure no internal import/package names were renamed.")
    print("  3. Redeploy.")
    print("  4. Repost verification/ticket panels once so old embeds are refreshed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
