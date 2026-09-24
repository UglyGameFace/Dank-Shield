from __future__ import annotations

from dataclasses import asdict, dataclass
import sys
from typing import Any, Dict, List, Optional, Sequence


# This is the explicit startup-health contract, not an activation registry.
# Every module here is an actual native production owner imported by main.py,
# app.py, or commands.py before Discord login/command use. Diagnostics only
# observe whether those already-owned modules are present in sys.modules. They
# never import a missing module as a repair action.
#
# Do not list dormant compatibility guards or feature modules that are loaded
# lazily on demand. Doing so turns an intentional architecture migration into a
# permanent false startup warning.
EXPECTED_STARTUP_OWNER_MODULES: tuple[str, ...] = (
    "stoney_verify.startup_guards.process_health",
    "stoney_verify.startup_guards.discord_api_safety",
    "stoney_verify.command_runtime",
    "stoney_verify.startup_guards.public_server_env_id_guard",
    "stoney_verify.interaction_guard",
    "stoney_verify.verification_new.basic_verify",
    "stoney_verify.ticket_panel_runtime",
    "stoney_verify.interaction_handlers",
    "stoney_verify.commands_ext.public_self_roles_group",
    "stoney_verify.members_new.activity_tracker",
)


@dataclass(frozen=True)
class StartupGuardStatus:
    """Observed state for one explicitly owned startup module."""

    module: str
    state: str
    error_type: Optional[str] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class StartupHealthReport:
    """Read-only snapshot of the explicit production startup contract."""

    status: str
    expected_count: int
    loaded_count: int
    failed_count: int
    missing_count: int
    blockers: List[str]
    warnings: List[str]
    guards: List[StartupGuardStatus]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _loaded_module_names() -> set[str]:
    return set(sys.modules)


def build_startup_health_report() -> StartupHealthReport:
    """Report explicit startup ownership without importing or repairing anything.

    Missing modules are warnings. Direct main.py import failures normally prevent
    the process from reaching diagnostics at all; host-hook failures can be
    swallowed by the host hook and therefore appear here as missing instead of
    being guessed into a synthetic failure state.
    """

    loaded_modules = _loaded_module_names()
    statuses: List[StartupGuardStatus] = []
    warnings: List[str] = []

    for module in EXPECTED_STARTUP_OWNER_MODULES:
        if module in loaded_modules:
            statuses.append(StartupGuardStatus(module=module, state="loaded"))
        else:
            statuses.append(StartupGuardStatus(module=module, state="missing"))
            warnings.append(f"{module}: expected startup owner is not loaded")

    loaded_count = sum(1 for item in statuses if item.state == "loaded")
    missing_count = sum(1 for item in statuses if item.state == "missing")
    status = "warning" if missing_count else "ok"

    return StartupHealthReport(
        status=status,
        expected_count=len(EXPECTED_STARTUP_OWNER_MODULES),
        loaded_count=loaded_count,
        failed_count=0,
        missing_count=missing_count,
        blockers=[],
        warnings=warnings,
        guards=statuses,
    )


def format_startup_health_report(report: StartupHealthReport) -> str:
    """Format a plain-language startup ownership report for logs or commands."""

    lines = [
        "Dank Shield startup ownership health",
        f"Status: {report.status.upper()}",
        (
            "Counts: "
            f"expected={report.expected_count} "
            f"loaded={report.loaded_count} "
            f"missing={report.missing_count}"
        ),
    ]

    if report.blockers:
        lines.append("")
        lines.append("Blockers:")
        for blocker in report.blockers[:10]:
            lines.append(f"- {blocker}")
        if len(report.blockers) > 10:
            lines.append(f"- ...and {len(report.blockers) - 10} more")

    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in report.warnings[:10]:
            lines.append(f"- {warning}")
        if len(report.warnings) > 10:
            lines.append(f"- ...and {len(report.warnings) - 10} more")

    return "\n".join(lines)


def startup_health_summary() -> str:
    """Convenience helper for read-only startup diagnostics."""

    return format_startup_health_report(build_startup_health_report())


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv or [])
    if "--load" in args:
        print(
            "Bulk startup-guard loading is retired; --load is ignored and this "
            "report remains read-only."
        )
    report = build_startup_health_report()
    print(format_startup_health_report(report))
    return 1 if report.status == "blocker" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
