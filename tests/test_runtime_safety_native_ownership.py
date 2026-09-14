from __future__ import annotations

import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SAFETY = "stoney_verify.startup_guards.runtime_safety"
PUBLIC_STARTUP_SCOPE = "stoney_verify.startup_guards.public_startup_scope"


def test_temporary_runtime_import_hook_modules_are_not_importable() -> None:
    assert importlib.util.find_spec(RUNTIME_SAFETY) is None
    assert importlib.util.find_spec(PUBLIC_STARTUP_SCOPE) is None

    from stoney_verify.startup_guards import LEGACY_DORMANT_STARTUP_GUARDS

    assert RUNTIME_SAFETY not in LEGACY_DORMANT_STARTUP_GUARDS
    assert PUBLIC_STARTUP_SCOPE not in LEGACY_DORMANT_STARTUP_GUARDS


def test_host_startup_does_not_restore_retired_runtime_patchers() -> None:
    code = (
        "import sys, sitecustomize; "
        f"assert {RUNTIME_SAFETY!r} not in sys.modules; "
        f"assert {PUBLIC_STARTUP_SCOPE!r} not in sys.modules"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_identity_truth_command_executes_real_lookup_off_event_loop(monkeypatch) -> None:
    from stoney_verify.commands_ext import identity_admin

    class FakeTree:
        def __init__(self) -> None:
            self.commands = {}

        def command(self, *args, **kwargs):
            name = str(kwargs.get("name") or "")

            def decorator(fn):
                self.commands[name] = fn
                return fn

            return decorator

    class FakeGuild:
        def get_member(self, _user_id):
            return None

    member = SimpleNamespace(
        id=123,
        mention="<@123>",
        display_avatar=SimpleNamespace(url="https://example.invalid/avatar.png"),
    )
    interaction = SimpleNamespace(
        guild_id=456,
        guild=FakeGuild(),
        user=SimpleNamespace(id=789),
    )
    replies = []
    event_loop_thread = threading.get_ident()
    lookup_threads = []

    def truth_lookup(*, guild_id: str, user_id: str):
        lookup_threads.append(threading.get_ident())
        return {
            "proof_matches": [{"matched_user_id": "999", "match_confidence": 100}],
            "manual_confirmed": [],
            "manual_likely": [],
            "manual_not_linked": [],
            "guild_id": guild_id,
            "user_id": user_id,
        }

    async def require_target(_interaction, _member, **_kwargs):
        return member

    async def reply(_interaction, payload):
        replies.append(payload)

    monkeypatch.setattr(identity_admin, "_IDENTITY_ADMIN_REGISTERED", False)
    monkeypatch.setattr(identity_admin, "_staff_check", lambda _interaction: True)
    monkeypatch.setattr(identity_admin, "get_identity_truth_context", truth_lookup)
    monkeypatch.setattr(identity_admin, "require_target_member", require_target)
    monkeypatch.setattr(identity_admin, "reply_once", reply)
    monkeypatch.setattr(identity_admin, "build_member_risk_profile", None)

    tree = FakeTree()
    identity_admin.register_identity_admin_commands(None, tree)
    command = tree.commands["identity_truth"]

    asyncio.run(command(interaction, "123"))

    assert lookup_threads
    assert lookup_threads[0] != event_loop_thread
    assert replies
    embed = replies[-1]["embed"]
    totals = next(field for field in embed.fields if field.name == "Truth Totals")
    assert "proof_matches=`1`" in totals.value


def test_ticket_service_runtime_delegates_to_persistent_allocator(monkeypatch) -> None:
    from stoney_verify.tickets_new import service

    calls = []

    async def persistent_allocator(guild, *, parent=None, source="", max_retries=0):
        calls.append(
            {
                "guild": guild,
                "parent": parent,
                "source": source,
                "max_retries": max_retries,
            }
        )
        return 73

    monkeypatch.setattr(service, "reserve_persistent_ticket_number", persistent_allocator)
    guild = SimpleNamespace(id=456)
    result = asyncio.run(service._reserve_next_ticket_number(guild))

    assert result == 73
    assert len(calls) == 1
    assert calls[0]["guild"] is guild
    assert calls[0]["source"] == "tickets_new.service"


def test_startup_diagnostics_no_longer_expect_retired_patchers() -> None:
    from stoney_verify.startup_diagnostics import EXPECTED_STARTUP_OWNER_MODULES

    assert RUNTIME_SAFETY not in EXPECTED_STARTUP_OWNER_MODULES
    assert PUBLIC_STARTUP_SCOPE not in EXPECTED_STARTUP_OWNER_MODULES
    assert "stoney_verify.startup_guards.process_health" in EXPECTED_STARTUP_OWNER_MODULES
