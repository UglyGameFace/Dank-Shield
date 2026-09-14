from __future__ import annotations

import builtins
from types import ModuleType

from stoney_verify import startup_diagnostics as diagnostics


def _module(name: str) -> ModuleType:
    return ModuleType(name)


def test_startup_health_report_reads_explicit_owner_state_without_importing(monkeypatch):
    owners = (
        "guard.loaded",
        "guard.missing",
    )
    monkeypatch.setattr(diagnostics, "EXPECTED_STARTUP_OWNER_MODULES", owners)
    monkeypatch.setitem(diagnostics.sys.modules, "guard.loaded", _module("guard.loaded"))
    diagnostics.sys.modules.pop("guard.missing", None)

    import_calls: list[str] = []
    original_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        import_calls.append(str(name))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)

    report = diagnostics.build_startup_health_report()

    assert import_calls == []
    assert report.status == "warning"
    assert report.expected_count == 2
    assert report.loaded_count == 1
    assert report.failed_count == 0
    assert report.missing_count == 1
    assert report.blockers == []
    assert report.warnings == ["guard.missing: expected startup owner is not loaded"]
    assert [guard.state for guard in report.guards] == ["loaded", "missing"]


def test_startup_health_report_is_ok_when_every_explicit_owner_is_loaded(monkeypatch):
    owners = ("guard.one", "guard.two")
    monkeypatch.setattr(diagnostics, "EXPECTED_STARTUP_OWNER_MODULES", owners)
    for name in owners:
        monkeypatch.setitem(diagnostics.sys.modules, name, _module(name))

    report = diagnostics.build_startup_health_report()

    assert report.status == "ok"
    assert report.expected_count == 2
    assert report.loaded_count == 2
    assert report.missing_count == 0
    assert report.warnings == []


def test_retired_load_flag_cannot_import_missing_guards(monkeypatch, capsys):
    owners = ("guard.missing",)
    monkeypatch.setattr(diagnostics, "EXPECTED_STARTUP_OWNER_MODULES", owners)
    diagnostics.sys.modules.pop("guard.missing", None)

    import_calls: list[str] = []
    original_import = builtins.__import__

    def tracking_import(name, *args, **kwargs):
        import_calls.append(str(name))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", tracking_import)

    assert diagnostics.main(["--load"]) == 0
    output = capsys.readouterr().out

    assert import_calls == []
    assert "Bulk startup-guard loading is retired" in output
    assert "guard.missing" in output


def test_format_startup_health_report_is_plain_language():
    report = diagnostics.StartupHealthReport(
        status="warning",
        expected_count=2,
        loaded_count=1,
        failed_count=0,
        missing_count=1,
        blockers=[],
        warnings=["guard.missing: expected startup owner is not loaded"],
        guards=[],
    )

    text = diagnostics.format_startup_health_report(report)

    assert "Dank Shield startup ownership health" in text
    assert "Status: WARNING" in text
    assert "expected=2" in text
    assert "loaded=1" in text
    assert "missing=1" in text
    assert "guard.missing: expected startup owner is not loaded" in text
