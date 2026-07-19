from __future__ import annotations

"""Audit public slash-command surface and default-opening friction.

The canonical product surface lives in ``stoney_verify.command_surface_contract``.
This audit checks that stale Discord registrations are still pruned safely, that
advanced/direct aliases remain classified as hidden cleanup targets, and that
public production defaults avoid duplicate/guild-scoped command friction.
"""

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stoney_verify.command_surface_contract import (  # noqa: E402
    PUBLIC_DANK_CHILDREN,
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
    PUBLIC_HIDDEN_DANK_CHILDREN,
)

SLASH_CLEANUP = ROOT / "stoney_verify" / "startup_guards" / "slash_command_cleanup.py"
BRANDING_GUARD = ROOT / "stoney_verify" / "startup_guards" / "dank_shield_branding_guard.py"
STARTUP_LOADER = ROOT / "stoney_verify" / "startup_guards" / "__init__.py"
ENV_EXAMPLE = ROOT / ".env.example"
PUBLIC_SETUP_AUDIT = ROOT / "tools" / "audit_public_setup.py"
PUBLIC_SURFACE_AUDIT = ROOT / "tools" / "audit_public_command_surface.py"

REQUIRED_STALE_TOP_LEVEL = {
    "stoney",
    "spam_guard",
    "grant_vr",
    "ticket_panel_rules_set",
    "ticket_panel_bootstrap_all",
    "verify_status",
    "repair_verify_ui",
}

REQUIRED_PRUNED_DANK_CHILDREN = {
    "setup-status",
    "setup-assistant",
    "setup-defaults",
    "setup-review",
    "setup-verify-ids",
    "production-audit",
    "permission-check",
    "tickettool-check",
    "db-check",
    "health",
    "scoreboard",
}

REQUIRED_ENV_MARKERS = {
    "DANK_DEPLOYMENT_MODE=production",
    "DANK_PUBLIC_MODE=true",
    "DANK_PRODUCTION_MODE=true",
    "DANK_COMMAND_PROFILE=public",
    "DANK_SYNC_BETA_GUILD_COMMANDS=false",
    "CLEAR_GLOBAL_COMMANDS_ON_BOOT=false",
    "DANK_SKIP_UNCHANGED_GLOBAL_SYNC=true",
    "DANK_FORCE_COMMAND_SYNC_ON_BOOT=false",
    "DANK_PUBLIC_CONFIG_ISOLATION=true",
    "DANK_ALLOW_SERVER_ENV_IDS=false",
    "DANK_SERVER_ENV_IDS_ENABLED=false",
    "BOT_DISPLAY_NAME=Dank Shield",
}

FORBIDDEN_ENV_MARKERS = {
    "GUILD_ID=1098088221457514609",
    "DANK_SYNC_BETA_GUILD_COMMANDS=true",
    "CLEAR_GLOBAL_COMMANDS_ON_BOOT=true",
    "DANK_CLEAR_ANY_GUILD_COMMAND_COPY_ON_BOOT=true",
}

REQUIRED_BRANDING_MARKERS = {
    "Dank Shield",
    "/dank",
    "discord.InteractionResponse",
    "discord.Interaction",
    "Webhook",
}

REQUIRED_LOADER_ORDER = [
    "stoney_verify.startup_guards.slash_command_cleanup",
    "stoney_verify.startup_guards.dank_shield_branding_guard",
]

REQUIRED_COMMAND_CLEANUP_EPOCH_MARKERS = {
    "COMMAND_CLEANUP_EPOCH",
    "cleanup_epoch",
    'state["cleanup_epoch"] = COMMAND_CLEANUP_EPOCH',
    'and str(state.get("cleanup_epoch", "")) == COMMAND_CLEANUP_EPOCH',
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def literal_set_from_file(path: Path, assignment_name: str) -> set[str]:
    text = read(path)
    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == assignment_name for t in node.targets):
            continue
        value = node.value
        if not isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            continue
        out: set[str] = set()
        for item in value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                out.add(item.value)
        return out
    return set()


def fail_missing(label: str, have: set[str], required: set[str], failures: list[str]) -> None:
    missing = sorted(required - have)
    if missing:
        failures.append(f"{label} missing: {', '.join(missing)}")


def main() -> int:
    failures: list[str] = []

    if PUBLIC_GLOBAL_COMMAND_COUNT != 9 or len(PUBLIC_GLOBAL_COMMAND_NAMES) != 9:
        failures.append("canonical public global command surface is not exactly 9 commands")
    if not PUBLIC_DANK_CHILDREN:
        failures.append("canonical public /dank child contract is empty")

    for path in (
        SLASH_CLEANUP,
        BRANDING_GUARD,
        STARTUP_LOADER,
        ENV_EXAMPLE,
        PUBLIC_SETUP_AUDIT,
        PUBLIC_SURFACE_AUDIT,
    ):
        if not path.exists():
            failures.append(f"missing required file: {path.relative_to(ROOT)}")

    stale_top = literal_set_from_file(SLASH_CLEANUP, "STALE_TOP_LEVEL_COMMANDS")
    pruned_dank = literal_set_from_file(SLASH_CLEANUP, "CONFUSING_DANK_CHILDREN")

    fail_missing("STALE_TOP_LEVEL_COMMANDS", stale_top, REQUIRED_STALE_TOP_LEVEL, failures)
    fail_missing("CONFUSING_DANK_CHILDREN", pruned_dank, REQUIRED_PRUNED_DANK_CHILDREN, failures)

    hidden_direct_aliases = {
        name
        for name in PUBLIC_HIDDEN_DANK_CHILDREN
        if name in {
            "setup-access",
            "setup-assistant",
            "setup-defaults",
            "setup-find",
            "setup-logs",
            "setup-picker",
            "setup-review",
            "setup-status",
            "setup-tickets",
            "setup-verify",
            "setup-verify-ids",
            "db-check",
            "permission-check",
            "production-audit",
            "tickettool-check",
        }
    }
    fail_missing("CONFUSING_DANK_CHILDREN hidden aliases", pruned_dank, hidden_direct_aliases, failures)

    cleanup_text = read(SLASH_CLEANUP)
    cleanup_required_text = [
        "install_slash_command_cleanup_guard()",
        "app_commands.CommandTree.sync = _patched_sync",
        'remove_stale_top_level_commands(self, reason="pre_sync", guild=guild)',
        'prune_public_stoney_children(self, reason="pre_sync", guild=guild)',
        "DANK_SKIP_UNCHANGED_GLOBAL_SYNC",
        "DANK_FORCE_COMMAND_SYNC_ON_BOOT",
        "DANK_GUILD_COMMAND_CLEANUP_IDS",
        "DANK_SYNC_BETA_GUILD_COMMANDS",
    ]
    cleanup_required_text.extend(sorted(REQUIRED_COMMAND_CLEANUP_EPOCH_MARKERS))
    for marker in cleanup_required_text:
        if marker not in cleanup_text:
            failures.append(f"slash command cleanup missing marker: {marker}")

    branding_text = read(BRANDING_GUARD)
    for marker in REQUIRED_BRANDING_MARKERS:
        if marker not in branding_text:
            failures.append(f"branding guard missing marker: {marker}")

    loader_text = read(STARTUP_LOADER)
    for marker in REQUIRED_LOADER_ORDER:
        if marker not in loader_text:
            failures.append(f"startup loader missing guard: {marker}")

    slash_pos = loader_text.find("stoney_verify.startup_guards.slash_command_cleanup")
    public_scope_pos = loader_text.find("stoney_verify.startup_guards.public_startup_scope")
    if slash_pos < 0:
        failures.append("startup loader does not load slash_command_cleanup")
    elif public_scope_pos >= 0 and slash_pos > public_scope_pos:
        failures.append("slash_command_cleanup loads too late after public_startup_scope")

    env_text = read(ENV_EXAMPLE)
    for marker in REQUIRED_ENV_MARKERS:
        if marker not in env_text:
            failures.append(f".env.example missing public production marker: {marker}")
    for marker in FORBIDDEN_ENV_MARKERS:
        if marker in env_text:
            failures.append(f".env.example contains unsafe public production marker: {marker}")

    if failures:
        print("Public command/friction audit failed:")
        for failure in failures:
            print(" -", failure)
        return 1

    print("Public command/friction audit passed.")
    print(f"Canonical globals: {PUBLIC_GLOBAL_COMMAND_COUNT} -> {', '.join(PUBLIC_GLOBAL_COMMAND_NAMES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
