from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify.tickets_new.api_claim_runtime_guard import (
    install_api_claim_runtime_guards,
)


ROOT = Path(__file__).resolve().parents[1]


def test_api_guard_refuses_incomplete_server_module() -> None:
    with pytest.raises(RuntimeError, match="missing"):
        install_api_claim_runtime_guards(SimpleNamespace())


def test_dashboard_mutations_require_human_actor_identity() -> None:
    source = (ROOT / "stoney_verify/tickets_new/api_claim_runtime_guard.py").read_text(encoding="utf-8")

    assert 'actor_keys=("staff_id",)' in source
    assert 'actor_keys=("actor_id", "staff_id")' in source
    assert 'actor_keys=("actor_id",)' in source
    assert "human ticket action" in source

    assert 'action="close"' in source
    assert 'action="reopen"' in source
    assert 'action="delete"' in source
    assert 'action="unclaim"' in source
    assert 'action="transfer"' in source
    assert 'action="transcript"' in source


def test_api_helpers_are_patched_before_remote_side_effects() -> None:
    source = (ROOT / "stoney_verify/tickets_new/api_claim_runtime_guard.py").read_text(encoding="utf-8")

    assert "api_server.close_ticket = guarded_close" in source
    assert "api_server.reopen_ticket_endpoint = guarded_reopen" in source
    assert "api_server.delete_ticket = guarded_delete_endpoint" in source
    assert "api_server.unclaim_ticket_endpoint = guarded_unclaim_endpoint" in source
    assert "api_server.transfer_ticket_endpoint = guarded_transfer_endpoint" in source
    assert "api_server.delete_ticket_with_optional_transcript = guarded_delete_service" in source
    assert "api_server.post_transcript_to_channel = guarded_transcript_service" in source
    assert "api_server.unclaim_ticket = guarded_unclaim_service" in source
    assert "api_server.transfer_ticket = guarded_transfer_service" in source

    delete_auth = source.index('action="delete"')
    delete_call = source.index("result = await original_delete_service")
    assert delete_auth < delete_call

    transcript_auth = source.index('action="transcript"')
    transcript_call = source.index("return await original_transcript_service")
    assert transcript_auth < transcript_call
