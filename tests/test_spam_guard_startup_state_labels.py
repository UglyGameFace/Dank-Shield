from __future__ import annotations

from stoney_verify.startup_guards.spam_guard_default_state_guard import classify_spam_guard_startup_state


def test_spam_guard_startup_state_labels_distinguish_default_persisted_and_error() -> None:
    assert (
        classify_spam_guard_startup_state(
            {"enabled": True},
            {"status": "ok", "original_row_found": False, "persisted": True},
        )
        == "DEFAULT ENABLED"
    )
    assert (
        classify_spam_guard_startup_state(
            {"enabled": True},
            {"status": "ok", "original_row_found": True, "persisted": True},
        )
        == "PERSISTED ENABLED"
    )
    assert (
        classify_spam_guard_startup_state(
            {"enabled": False},
            {"status": "ok", "original_row_found": True, "persisted": True},
        )
        == "PERSISTED DISABLED"
    )
    assert (
        classify_spam_guard_startup_state(
            {"enabled": True},
            {"status": "unavailable", "reason": "fetch_error:TimeoutError"},
        )
        == "DATABASE LOAD ERROR"
    )
