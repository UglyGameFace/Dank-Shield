from __future__ import annotations

import builtins
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PROCESS_HEALTH = "stoney_verify.startup_guards.process_health"


def _run_isolated(code: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_startup_guards_package_import_is_inert_for_process_health() -> None:
    code = (
        "import builtins, sys; "
        "original_import = builtins.__import__; "
        "import stoney_verify.startup_guards; "
        "assert builtins.__import__ is original_import; "
        f"assert {PROCESS_HEALTH!r} not in sys.modules"
    )
    proc = _run_isolated(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_explicit_process_health_install_never_replaces_python_imports() -> None:
    code = (
        "import builtins, os, sys, tempfile; "
        "os.environ['DANK_PROCESS_BOOT_STATE'] = tempfile.mktemp(prefix='dank-health-'); "
        "original_import = builtins.__import__; "
        "import stoney_verify.startup_guards.process_health as health; "
        "assert builtins.__import__ is original_import; "
        "assert sys.excepthook is not health._sync_excepthook; "
        "assert health.install_process_health() is True; "
        "assert builtins.__import__ is original_import; "
        "assert sys.excepthook is health._sync_excepthook; "
        "assert health.install_process_health() is False"
    )
    proc = _run_isolated(code)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_process_health_bot_attachment_is_explicit_and_idempotent(monkeypatch) -> None:
    import stoney_verify.startup_guards.process_health as health

    listeners: list[tuple[object, str]] = []

    class FakeBot:
        user = None
        guilds = []

        def add_listener(self, listener, name: str) -> None:
            listeners.append((listener, name))

    monkeypatch.setattr(health, "_READY_LISTENER_ATTACHED", False)

    bot = FakeBot()
    assert health.attach_process_health(bot) is True
    assert health.attach_process_health(bot) is False
    assert len(listeners) == 1
    assert listeners[0][1] == "on_ready"


def test_process_health_is_explicit_startup_owner_but_not_dormant_inventory() -> None:
    from stoney_verify.startup_diagnostics import EXPECTED_STARTUP_OWNER_MODULES
    from stoney_verify.startup_guards import LEGACY_DORMANT_STARTUP_GUARDS

    assert PROCESS_HEALTH in EXPECTED_STARTUP_OWNER_MODULES
    assert PROCESS_HEALTH not in LEGACY_DORMANT_STARTUP_GUARDS
