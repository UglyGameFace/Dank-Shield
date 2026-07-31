from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.tickets_new.ticket_admin_claim_guard import (
    _action_for_command,
    install_ticket_admin_claim_guard,
)


ROOT = Path(__file__).resolve().parents[1]


def test_every_legacy_ticket_command_maps_to_claim_policy_action() -> None:
    expected = {
        "close_ticket": "close",
        "ticket_claim": "claim",
        "ticket_unclaim": "unclaim",
        "ticket_transfer": "transfer",
        "ticket_reopen": "reopen",
        "ticket_delete": "delete",
        "ticket_transcript": "transcript",
        "ticket_priority": "priority",
        "ticket_info": "view_info",
        "ticket_note_add": "note",
        "ticket_notes": "view_notes",
        "ticket_notes_list": "view_notes",
        "ticket_macro": "macro",
    }
    for command_name, action in expected.items():
        assert _action_for_command(command_name) == action


def test_legacy_guard_refuses_incomplete_modules() -> None:
    with pytest.raises(RuntimeError, match="missing"):
        install_ticket_admin_claim_guard(
            SimpleNamespace(),
            ticket_transcripts=SimpleNamespace(),
        )


def test_context_authorization_precedes_legacy_side_effect_helpers() -> None:
    source = (ROOT / "stoney_verify/tickets_new/ticket_admin_claim_guard.py").read_text(encoding="utf-8")

    assert "ticket_admin._ensure_ticket_context = guarded_context" in source
    assert "ticket_admin.transcript_staff_delete_closed_ticket = guarded_delete" in source
    assert "ticket_admin.transcript_post_to_channel = guarded_direct_transcript" in source
    assert "ticket_admin.send_tickettool_style_transcript = ticket_transcripts.send_tickettool_style_transcript" in source

    context_auth = source.index("decision = await ticket_transcripts.authorize_ticket_action")
    context_allow = source.index("if decision.allowed:")
    context_return = source.index("return resolved_channel, row", context_allow)
    assert context_auth < context_allow < context_return

    delete_guard = source.index('action="delete"')
    delete_call = source.index("result = await original_delete")
    assert delete_guard < delete_call

    transcript_guard = source.index('action="transcript"')
    transcript_call = source.index("return await original_direct_transcript")
    assert transcript_guard < transcript_call
