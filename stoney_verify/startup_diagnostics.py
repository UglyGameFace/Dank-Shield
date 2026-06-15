from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence


@dataclass(frozen=True)
class StartupGuardStatus:
    """Single startup guard import state.

    This intentionally reports loader state without importing every guard by default.
    Use build_startup_health_report(load_missing=True) only from explicit diagnostics
    or local validation tools.
    """

    module: str
    state: str
    error_type: Optional[str] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class StartupHealthReport:
    """Snapshot of startup guard health for production diagnostics."""

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


def _load_startup_loader() -> Any:
    # Importing the package loader should be safe: it defines guard lists and
    # loader state. It should not start the Discord client.
    from stoney_verify import startup_guards

    return startup_guards


def _public_state_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _expected_guards(loader: Any) -> Sequence[str]:
    value = getattr(loader, "_STARTUP_GUARDS", ())
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return ()


def _loaded_guards(loader: Any) -> Dict[str, Any]:
    return _public_state_mapping(getattr(loader, "_LOADED", {}))


def _failed_guards(loader: Any) -> Dict[str, BaseException]:
    raw = _public_state_mapping(getattr(loader, "_ERRORS", {}))
    return {str(module): exc for module, exc in raw.items() if isinstance(exc, BaseException)}


def _summarize_error(exc: BaseException) -> tuple[str, str]:
    return (exc.__class__.__name__, str(exc) or repr(exc))


def build_startup_health_report(*, load_missing: bool = False) -> StartupHealthReport:
    """Build a startup guard health report.

    Args:
        load_missing: When false, report current loader state only. When true,
            call the startup guard loader first so missing modules are attempted.
            Keep this false in normal runtime diagnostics unless the caller
            intentionally wants to trigger imports.
    """

    loader = _load_startup_loader()

    if load_missing:
        load_all = getattr(loader, "load_all_startup_guards", None)
        if callable(load_all):
            load_all()

    expected = tuple(_expected_guards(loader))
    loaded = _loaded_guards(loader)
    failed = _failed_guards(loader)

    statuses: List[StartupGuardStatus] = []
    blockers: List[str] = []
    warnings: List[str] = []

    for module in expected:
        if module in failed:
            error_type, error_message = _summarize_error(failed[module])
            statuses.append(
                StartupGuardStatus(
                    module=module,
                    state="failed",
                    error_type=error_type,
                    error_message=error_message,
                )
            )
            blockers.append(f"{module}: {error_type}: {error_message}")
        elif module in loaded:
            statuses.append(StartupGuardStatus(module=module, state="loaded"))
        else:
            statuses.append(StartupGuardStatus(module=module, state="missing"))
            warnings.append(f"{module}: not loaded yet")

    unexpected_loaded = sorted(module for module in loaded if module not in set(expected))
    for module in unexpected_loaded:
        statuses.append(StartupGuardStatus(module=module, state="loaded_unexpected"))
        warnings.append(f"{module}: loaded but not listed in _STARTUP_GUARDS")

    failed_count = len(failed)
    loaded_count = len(loaded)
    missing_count = sum(1 for status in statuses if status.state == "missing")

    if failed_count:
        status = "blocker"
    elif missing_count:
        status = "warning"
    else:
        status = "ok"

    return StartupHealthReport(
        status=status,
        expected_count=len(expected),
        loaded_count=loaded_count,
        failed_count=failed_count,
        missing_count=missing_count,
        blockers=blockers,
        warnings=warnings,
        guards=statuses,
    )


def format_startup_health_report(report: StartupHealthReport) -> str:
    """Format a plain-language startup health report for logs or commands."""

    lines = [
        "Dank Shield startup health",
        f"Status: {report.status.upper()}",
        (
            "Counts: "
            f"expected={report.expected_count} "
            f"loaded={report.loaded_count} "
            f"failed={report.failed_count} "
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


def startup_health_summary(*, load_missing: bool = False) -> str:
    """Convenience helper for commands, logs, and local diagnostics."""

    return format_startup_health_report(build_startup_health_report(load_missing=load_missing))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv or [])
    load_missing = "--load" in args
    report = build_startup_health_report(load_missing=load_missing)
    print(format_startup_health_report(report))
    return 1 if report.status == "blocker" else 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
