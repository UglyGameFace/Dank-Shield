from __future__ import annotations

"""Compatibility shim for the native SpamGuard startup-state reporter.

The real runtime owner now lives in ``stoney_verify.spam_guard_startup_state``.
Keeping these exports preserves older imports without registering a second
listener or depending on the dormant startup-guard loader.
"""

from stoney_verify.spam_guard_startup_state import (
    classify_spam_guard_startup_state,
    report_spam_guard_states_once,
    spam_guard_default_state_on_ready,
)

__all__ = [
    "classify_spam_guard_startup_state",
    "report_spam_guard_states_once",
    "spam_guard_default_state_on_ready",
]
