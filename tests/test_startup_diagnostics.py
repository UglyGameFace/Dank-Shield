from __future__ import annotations

from types import SimpleNamespace

from stoney_verify import startup_diagnostics as diagnostics


def test_startup_health_report_reads_current_state_without_loading_missing(monkeypatch):
    load_calls: list[str] = []
    fake_loader = SimpleNamespace(
        _STARTUP_GUARDS=("guard.loaded", "guard.missing"),
        _LOADED={"guard.loaded": object()},
        _ERRORS={},
        load_all_startup_guards=lambda: load_calls.append("called"),
    )

    monkeypatch.setattr(diagnostics, "_load_startup_loader", lambda: fake_loader)

    report = diagnostics.build_startup_health_report(load_missing=False)

    assert load_calls == []
    assert report.status == "warning"
    assert report.expected_count == 2
    assert report.loaded_count == 1
    assert report.failed_count == 0
    assert report.missing_count == 1
    assert report.warnings == ["guard.missing: not loaded yet"]
    assert [guard.state for guard in report.guards] == ["loaded", "missing"]


def test_startup_health_report_can_load_missing_on_demand(monkeypatch):
    load_calls: list[str] = []

    def load_all() -> None:
        load_calls.append("called")

    fake_loader = SimpleNamespace(
        _STARTUP_GUARDS=("guard.loaded",),
        _LOADED={"guard.loaded": object()},
        _ERRORS={},
        load_all_startup_guards=load_all,
    )

    monkeypatch.setattr(diagnostics, "_load_startup_loader", lambda: fake_loader)

    report = diagnostics.build_startup_health_report(load_missing=True)

    assert load_calls == ["called"]
    assert report.status == "ok"
    assert report.expected_count == 1
    assert report.loaded_count == 1
    assert report.failed_count == 0
    assert report.missing_count == 0


def test_startup_health_report_marks_failed_guards_as_blockers(monkeypatch):
    error = RuntimeError("boom")
    fake_loader = SimpleNamespace(
        _STARTUP_GUARDS=("guard.failed",),
        _LOADED={},
        _ERRORS={"guard.failed": error},
    )

    monkeypatch.setattr(diagnostics, "_load_startup_loader", lambda: fake_loader)

    report = diagnostics.build_startup_health_report()

    assert report.status == "blocker"
    assert report.expected_count == 1
    assert report.loaded_count == 0
    assert report.failed_count == 1
    assert report.missing_count == 0
    assert report.guards[0].state == "failed"
    assert report.guards[0].error_type == "RuntimeError"
    assert report.guards[0].error_message == "boom"
    assert report.blockers == ["guard.failed: RuntimeError: boom"]


def test_format_startup_health_report_is_plain_language():
    report = diagnostics.StartupHealthReport(
        status="blocker",
        expected_count=2,
        loaded_count=1,
        failed_count=1,
        missing_count=0,
        blockers=["guard.failed: RuntimeError: boom"],
        warnings=[],
        guards=[],
    )

    text = diagnostics.format_startup_health_report(report)

    assert "Dank Shield startup health" in text
    assert "Status: BLOCKER" in text
    assert "expected=2" in text
    assert "loaded=1" in text
    assert "failed=1" in text
    assert "guard.failed: RuntimeError: boom" in text
