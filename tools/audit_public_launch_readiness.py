from __future__ import annotations

"""Static public launch readiness audit for Dank Shield.

This audit keeps the public-release docs, env defaults, and command-surface
contract aligned. It is intentionally conservative: runtime behavior still needs
manual fresh-server and existing-server smoke tests.
"""

from pathlib import Path
import re
from runpy import run_path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
CHECKLIST = ROOT / "docs" / "PUBLIC_LAUNCH_CHECKLIST.md"
ENV_EXAMPLE = ROOT / ".env.example"

# Read the standalone, side-effect-free contract without importing bot modules
# or starting Discord. The docs must describe the actual public command tree.
CONTRACT = run_path(str(ROOT / "stoney_verify" / "command_surface_contract.py"))
REQUIRED_COMMANDS = tuple(
    "/" + name for name in CONTRACT["PUBLIC_GLOBAL_COMMAND_NAMES"]
    if name != "View Dank Profile"
)
REQUIRED_CONTEXT_MENU = "View Dank Profile"
REQUIRED_DANK_CHILDREN = tuple(sorted(CONTRACT["PUBLIC_DANK_CHILDREN"]))
FORBIDDEN_PUBLIC_COMMANDS = (
    "/stoney", "/ticket-panel", "/ticket-category",
    "/dank commands", "/dank cleanup", "/dank members", "/dank spam",
    "/dank scoreboard", "/dank setup-status", "/dank db-check",
    "/dank production-audit", "/repair_verify_ui",
    "/recompute_member_risk", "/recompute_all_member_risk",
    "/spam_guard", "/grant_vr",
)

REQUIRED_ENV = [
    "DANK_DEPLOYMENT_MODE=production",
    "DANK_PUBLIC_MODE=true",
    "DANK_PRODUCTION_MODE=true",
    "DANK_COMMAND_PROFILE=public",
    "DANK_SYNC_BETA_GUILD_COMMANDS=false",
    "CLEAR_GLOBAL_COMMANDS_ON_BOOT=false",
    "DANK_DANGEROUS_CLEAR_ALL_GLOBAL_COMMANDS_ON_BOOT=false",
    "DANK_SKIP_UNCHANGED_GLOBAL_SYNC=true",
    "DANK_FORCE_COMMAND_SYNC_ON_BOOT=false",
    "DANK_PUBLIC_CONFIG_ISOLATION=true",
    "DANK_ALLOW_SERVER_ENV_IDS=false",
    "DANK_SERVER_ENV_IDS_ENABLED=false",
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
    "public command contract: 8 slash roots + View Dank Profile user context menu",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def require_contains(label: str, text: str, marker: str, failures: list[str]) -> None:
    if marker not in text:
        failures.append(f"{label} missing marker: {marker}")


def verify_documented_command_surface(checklist: str, failures: list[str]) -> None:
    """Require an exact fenced final public tree, not substring matches."""
    start = "## Required public command surface"
    end = "## One-time command cache flush"
    if start not in checklist or end not in checklist:
        failures.append("public launch checklist missing command surface section")
        return
    section = checklist.split(start, 1)[1].split(end, 1)[0]
    blocks = re.findall(r"```text\s*\n(.*?)\n```", section, re.DOTALL)
    if len(blocks) != 3:
        failures.append("public command surface needs exactly three lists")
        return
    lists = [tuple(line.strip() for line in block.splitlines() if line.strip()) for block in blocks]
    if lists[0] != REQUIRED_COMMANDS:
        failures.append("public slash roots drifted from command_surface_contract")
    if lists[1] != REQUIRED_DANK_CHILDREN:
        failures.append("direct /dank children drifted from command_surface_contract")
    forbidden = set(lists[2])
    missing = set(FORBIDDEN_PUBLIC_COMMANDS) - forbidden
    if missing:
        failures.append("forbidden public commands missing: " + ", ".join(sorted(missing)))
    if "/dank" in forbidden or forbidden.intersection(REQUIRED_COMMANDS):
        failures.append("current public slash root is incorrectly forbidden")


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

    verify_documented_command_surface(checklist, failures)
    require_contains("public launch docs", combined_public_docs, REQUIRED_CONTEXT_MENU, failures)

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
