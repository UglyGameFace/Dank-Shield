from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.tickets_new.claim_runtime_guard import (
    install_transcript_claim_runtime_guards,
)


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_guard_refuses_incomplete_legacy_module() -> None:
    with pytest.raises(RuntimeError, match="missing"):
        install_transcript_claim_runtime_guards(SimpleNamespace())


def test_runtime_guard_covers_direct_legacy_side_effects() -> None:
    source = (ROOT / "stoney_verify/tickets_new/claim_runtime_guard.py").read_text(encoding="utf-8")

    assert 'action="delete"' in source
    assert 'action="transcript"' in source
    assert 'action="close"' in source
    assert 'action="reopen"' in source
    assert 'action="verification_review"' in source
    assert "allow_requester_cancel=True" in source

    assert "_staff_delete_closed_ticket_verified = guarded_delete" in source
    assert "send_tickettool_style_transcript = guarded_transcript" in source
    assert "_user_can_close_ticket = guarded_can_close" in source
    assert "_user_can_reopen_ticket = guarded_can_reopen" in source
    assert "VerificationStaffReviewView._ensure_staff = guarded_verification_staff_check" in source
    assert "StaffClosedTicketView._ensure_staff = guarded_closed_staff_check" in source

    delete_index = source.index('action="delete"')
    delete_call_index = source.index("return await original_delete")
    assert delete_index < delete_call_index

    transcript_index = source.index('action="transcript"')
    transcript_call_index = source.index("return await original_transcript")
    assert transcript_index < transcript_call_index
