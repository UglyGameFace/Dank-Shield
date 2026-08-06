from __future__ import annotations

from stoney_verify.commands_ext.member_role_browser_bulk import (
    _confirmation_matches,
    bulk_confirmation_phrase,
)


def test_bulk_confirmation_phrase_binds_action_and_target_count() -> None:
    assert bulk_confirmation_phrase("verify", 2) == "VERIFY 2"
    assert bulk_confirmation_phrase("timeout", 2) == "TIMEOUT 2"
    assert bulk_confirmation_phrase("clear_timeout", 2) == "CLEAR 2"
    assert bulk_confirmation_phrase("kick", 2) == "KICK 2"
    assert bulk_confirmation_phrase("ban", 2) == "BAN 2"


def test_bulk_confirmation_rejects_wrong_action_or_stale_count() -> None:
    assert _confirmation_matches(" kick   2 ", "kick", 2) is True
    assert _confirmation_matches("KICK 3", "kick", 2) is False
    assert _confirmation_matches("BAN 2", "kick", 2) is False
    assert _confirmation_matches("KICK", "kick", 2) is False
