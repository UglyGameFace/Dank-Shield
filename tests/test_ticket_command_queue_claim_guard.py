from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.tickets_new.command_queue_claim_guard import (
    install_bot_command_worker_claim_guard,
    install_tasks_command_queue_claim_guard,
)


ROOT = Path(__file__).resolve().parents[1]


def test_queue_guards_refuse_missing_execution_surfaces() -> None:
    with pytest.raises(RuntimeError, match="unavailable"):
        install_tasks_command_queue_claim_guard(SimpleNamespace())
    with pytest.raises(RuntimeError, match="unavailable"):
        install_bot_command_worker_claim_guard(SimpleNamespace())


def test_dashboard_queue_lifecycle_actions_use_canonical_services() -> None:
    source = (ROOT / "stoney_verify/tickets_new/command_queue_claim_guard.py").read_text(encoding="utf-8")

    assert "tasks_queue._execute_command = guarded_execute" in source
    assert "worker.execute_command = guarded_execute" in source
    assert "await assign_ticket(" in source
    assert "await mark_ticket_closed(" in source
    assert "await reopen_ticket(" in source
    assert "await delete_ticket_with_optional_transcript(" in source
    assert "await _require_claimant(" in source

    assert '"close_ticket": "close"' in source
    assert '"delete_ticket": "delete"' in source
    assert '"reopen_ticket": "reopen"' in source
    assert '"portal_ticket_reply": "message"' in source
    assert '"approve_verification": "verification_review"' in source
    assert '"deny_verification": "verification_review"' in source


def test_dashboard_reply_does_not_rewrite_ticket_lifecycle() -> None:
    source = (ROOT / "stoney_verify/tickets_new/command_queue_claim_guard.py").read_text(encoding="utf-8")
    reply_block = source[source.index('if action == "portal_ticket_reply":'):]
    reply_block = reply_block[: reply_block.index("# Verification worker paths")]

    assert "channel.send" in reply_block
    assert "_persist_reopened_ticket" not in reply_block
    assert '"mirrored": True' in reply_block
