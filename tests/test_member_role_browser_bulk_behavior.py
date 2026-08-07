from __future__ import annotations

from stoney_verify.commands_ext import member_role_browser_bulk as bulk
from stoney_verify.commands_ext.member_role_browser_bulk import (
    _confirmation_matches,
    bulk_confirmation_phrase,
)
from stoney_verify.commands_ext.member_role_browser_bulk_role_confirmation import (
    ConfirmedBulkRoleActionView,
    _confirmation_matches as role_confirmation_matches,
    bulk_role_confirmation_phrase,
    install_confirmed_bulk_role_actions,
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


def test_bulk_role_confirmation_binds_action_role_and_target_count() -> None:
    assert bulk_role_confirmation_phrase("add_role", 3, 987654321) == (
        "ADD ROLE 987654321 TO 3"
    )
    assert bulk_role_confirmation_phrase("remove_role", 3, 987654321) == (
        "REMOVE ROLE 987654321 FROM 3"
    )
    assert role_confirmation_matches(
        " add  role 987654321 to 3 ",
        action="add_role",
        count=3,
        role_id=987654321,
    ) is True
    assert role_confirmation_matches(
        "ADD ROLE 987654322 TO 3",
        action="add_role",
        count=3,
        role_id=987654321,
    ) is False
    assert role_confirmation_matches(
        "ADD ROLE 987654321 TO 4",
        action="add_role",
        count=3,
        role_id=987654321,
    ) is False
    assert role_confirmation_matches(
        "REMOVE ROLE 987654321 FROM 3",
        action="add_role",
        count=3,
        role_id=987654321,
    ) is False


def test_bulk_runtime_uses_confirmed_role_view() -> None:
    assert install_confirmed_bulk_role_actions() is True
    assert bulk.BulkRoleActionView is ConfirmedBulkRoleActionView
