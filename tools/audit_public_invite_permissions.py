from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

README = ROOT / "README.md"
ENV_EXAMPLE = ROOT / ".env.example"
PUBLIC_LAUNCH = ROOT / "docs" / "PUBLIC_LAUNCH_CHECKLIST.md"

REQUIRED_README_MARKERS = [
    "Invite Dank Shield with permissions",
    "Manage Channels",
    "Manage Roles",
    "View Channels",
    "Send Messages",
    "Embed Links",
    "Attach Files",
    "Read Message History",
    "Manage Messages",
    "Moderate Members",
    "Keep the Dank Shield bot role above roles it needs to assign",
]

FORBIDDEN_PUBLIC_COPY = [
    "Invite Stoney",
    "Keep the Stoney bot role",
    "Give Stoney permission",
    "Administrator permission is required",
    "requires Administrator permission",
    "must use Administrator",
    "GUILD_ID=1098088221457514609",
    "HOME_GUILD_ID=1098088221457514609",
]

REQUIRED_ENV_MARKERS = [
    "DANK_PUBLIC_MODE=true",
    "DANK_PRODUCTION_MODE=true",
    "DANK_PUBLIC_CONFIG_ISOLATION=true",
    "DANK_ALLOW_SERVER_ENV_IDS=false",
    "DANK_SERVER_ENV_IDS_ENABLED=false",
    "BOT_DISPLAY_NAME=Dank Shield",
]

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

def main() -> int:
    failures: list[str] = []

    readme = read(README)
    env = read(ENV_EXAMPLE)
    launch = read(PUBLIC_LAUNCH)

    for marker in REQUIRED_README_MARKERS:
        if marker not in readme:
            failures.append(f"README missing minimum-permission marker: {marker}")

    public_text = "\n".join([readme, launch, env])
    for marker in FORBIDDEN_PUBLIC_COPY:
        if marker in public_text:
            failures.append(f"public invite/setup copy contains forbidden marker: {marker}")

    for marker in REQUIRED_ENV_MARKERS:
        if marker not in env:
            failures.append(f".env.example missing public-safe env marker: {marker}")

    if failures:
        print("Public invite/permissions audit failed:")
        for failure in failures:
            print(" -", failure)
        return 1

    print("Public invite/permissions audit passed.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
