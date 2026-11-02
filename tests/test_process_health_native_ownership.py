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
    monkeypatch.setattr(health, "_GATEWAY_LISTENERS_ATTACHED", False)

    bot = FakeBot()
    assert health.attach_process_health(bot) is True
    assert health.attach_process_health(bot) is False
    assert [name for _listener, name in listeners] == [
        "on_ready",
        "on_connect",
        "on_disconnect",
        "on_resumed",
    ]


def test_optional_interaction_ingress_is_private_and_idempotent(monkeypatch, capsys) -> None:
    import asyncio
    from types import SimpleNamespace
    import stoney_verify.startup_guards.process_health as health

    monkeypatch.setenv("DANK_INTERACTION_INGRESS_TRACE", "true")
    monkeypatch.setattr(health, "_READY_LISTENER_ATTACHED", False)
    monkeypatch.setattr(health, "_GATEWAY_LISTENERS_ATTACHED", False)
    monkeypatch.setattr(health, "_INTERACTION_TRACE_LISTENERS_ATTACHED", False)
    listeners = {}

    class FakeBot:
        user = None
        guilds = []

        def add_listener(self, listener, name: str) -> None:
            listeners[name] = listener

    bot = FakeBot()
    assert health.attach_process_health(bot) is True
    assert health.attach_process_health(bot) is False
    assert "on_socket_event_type" in listeners
    assert "on_interaction" in listeners

    interaction = SimpleNamespace(
        data={"name": "test\\nprivate", "custom_id": "secret-token"},
        type=SimpleNamespace(name="application_command"),
        user=SimpleNamespace(id=987654321),
        guild_id=123456789,
    )
    asyncio.run(listeners["on_socket_event_type"]("INTERACTION_CREATE"))
    asyncio.run(listeners["on_interaction"](interaction))
    output = capsys.readouterr().out
    assert "RAW_INTERACTION_CREATE" in output
    assert "INTERACTION_INGRESS" in output
    assert "test_private" in output
    assert "secret-token" not in output
    assert "987654321" not in output
    assert "123456789" not in output
    assert "test\\nprivate" not in output


def test_interaction_trace_disabled_by_default(monkeypatch) -> None:
    import stoney_verify.startup_guards.process_health as health

    monkeypatch.delenv("DANK_INTERACTION_INGRESS_TRACE", raising=False)
    monkeypatch.setattr(health, "_READY_LISTENER_ATTACHED", False)
    monkeypatch.setattr(health, "_GATEWAY_LISTENERS_ATTACHED", False)
    monkeypatch.setattr(health, "_INTERACTION_TRACE_LISTENERS_ATTACHED", False)
    listeners = []

    class FakeBot:
        user = None
        guilds = []

        def add_listener(self, listener, name: str) -> None:
            listeners.append(name)

    assert health.attach_process_health(FakeBot()) is True
    assert listeners == ["on_ready", "on_connect", "on_disconnect", "on_resumed"]
    assert health._safe_trace_label("x" * 100) == "x" * 64
    assert health._safe_trace_label("bad\\nname") == "bad_name"


def test_process_health_is_explicit_startup_owner_but_not_dormant_inventory() -> None:
    from stoney_verify.startup_diagnostics import EXPECTED_STARTUP_OWNER_MODULES
    from stoney_verify.startup_guards import LEGACY_DORMANT_STARTUP_GUARDS

    assert PROCESS_HEALTH in EXPECTED_STARTUP_OWNER_MODULES
    assert PROCESS_HEALTH not in LEGACY_DORMANT_STARTUP_GUARDS



def test_process_health_exposes_gateway_and_event_loop_stall_diagnostics() -> None:
    source = (ROOT / "stoney_verify" / "startup_guards" / "process_health.py").read_text(
        encoding="utf-8"
    )

    assert "EVENT_LOOP_LAG" in source
    assert "GATEWAY_CONNECTED" in source
    assert "GATEWAY_DISCONNECT" in source
    assert "GATEWAY_RESUMED" in source
    assert 'name="process_health_loop_lag"' in source
