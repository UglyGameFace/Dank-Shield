from __future__ import annotations

"""Static public launch readiness audit for Dank Shield.

This audit keeps the public-release docs, env defaults, and command-surface
contract aligned. It is intentionally conservative: runtime behavior still needs
manual fresh-server and existing-server smoke tests.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CHECKLIST = ROOT / "docs" / "PUBLIC_LAUNCH_CHECKLIST.md"
ENV_EXAMPLE = ROOT / ".env.example"

REQUIRED_COMMANDS = [
    "/dank",
    "/mod",
    "/ticket",
    "/tickets",
    "/ticket-category",
    "/ticket-panel",
    "/verify",
]

REQUIRED_DANK_CHILDREN = [
    "cleanup",
    "commands",
    "help",
    "members",
    "setup",
    "spam",
]

FORBIDDEN_PUBLIC_COMMANDS = [
    "/stoney",
    "/dank scoreboard",
    "/dank setup-status",
    "/dank db-check",
    "/dank production-audit",
    "/repair_verify_ui",
    "/recompute_member_risk",
    "/recompute_all_member_risk",
    "/spam_guard",
    "/grant_vr",
]

REQUIRED_ENV = [
    "STONEY_DEPLOYMENT_MODE=production",
    "STONEY_PUBLIC_MODE=true",
    "STONEY_PRODUCTION_MODE=true",
    "STONEY_COMMAND_PROFILE=public",
    "STONEY_SYNC_BETA_GUILD_COMMANDS=false",
    "CLEAR_GLOBAL_COMMANDS_ON_BOOT=false",
    "STONEY_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT=false",
    "DANK_SKIP_UNCHANGED_GLOBAL_SYNC=true",
    "DANK_FORCE_COMMAND_SYNC_ON_BOOT=false",
    "STONEY_PUBLIC_CONFIG_ISOLATION=true",
    "STONEY_ALLOW_SERVER_ENV_IDS=false",
    "STONEY_SERVER_ENV_IDS_ENABLED=false",
    "BOT_DISPLAY_NAME=Dank Shield",
]

REQUIRED_PERMISSION_COPY = [
    "Manage Channels",
    "Manage Roles",
    "View Channels",
    "Send Messages",
    "Embed Links",
    "Attach Files",
    "Read Message History",
    "Manage Messages",
    "Moderate Members",
]

REQUIRED_CHECKLIST_MARKERS = [
    "Fresh server smoke test",
    "Existing server smoke test",
    "Dashboard handoff gate",
    "Stability burn-in",
    "One-time command cache flush",
    "DANK_FORCE_COMMAND_SYNC_ON_BOOT=true",
    "DANK_FORCE_COMMAND_SYNC_ON_BOOT=false",
    "public_startup_guard blockers=0 warnings=0",
    "commands_ext registration complete",
    "local global commands: ['dank', 'mod', 'ticket', 'tickets', 'ticket-category', 'ticket-panel', 'verify']",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def require_contains(label: str, text: str, marker: str, failures: list[str]) -> None:
    if marker not in text:
        failures.append(f"{label} missing marker: {marker}")


def main() -> int:
    failures: list[str] = []

    readme = read(README)
    checklist = read(CHECKLIST)
    env_example = read(ENV_EXAMPLE)
    combined_public_docs = "\n".join([readme, checklist])

    if not readme:
        failures.append("README.md is missing or empty")
    if not checklist:
        failures.append("docs/PUBLIC_LAUNCH_CHECKLIST.md is missing or empty")
    if not env_example:
        failures.append(".env.example is missing or empty")

    for command in REQUIRED_COMMANDS:
        require_contains("public launch docs", combined_public_docs, command, failures)

    for child in REQUIRED_DANK_CHILDREN:
        require_contains("public launch docs", checklist, child, failures)

    for forbidden in FORBIDDEN_PUBLIC_COMMANDS:
        require_contains("public launch forbidden-command list", checklist, forbidden, failures)

    for marker in REQUIRED_ENV:
        require_contains(".env.example", env_example, marker, failures)
        require_contains("public launch checklist", checklist, marker, failures)

    for permission in REQUIRED_PERMISSION_COPY:
        require_contains("README/launch permission docs", combined_public_docs, permission, failures)

    for marker in REQUIRED_CHECKLIST_MARKERS:
        require_contains("public launch checklist", checklist, marker, failures)

    if "Administrator" in checklist and "avoid requiring Administrator" not in checklist:
        failures.append("public launch checklist mentions Administrator without the public-safe warning")

    if failures:
        print("Public launch readiness audit failed:")
        for failure in failures:
            print(" -", failure)
        return 1

    print("Public launch readiness audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
