from __future__ import annotations

"""Authoritative product policy for SpamGuard's enabled default.

The product rule is intentionally tiny and singular:

- no saved security-settings row => SpamGuard defaults to enabled;
- an existing persisted owner choice wins, including explicit disabled.

Runtime and setup implementations are regression-audited against this constant so
another module cannot quietly reintroduce a conflicting ``default=False`` truth.
"""

SPAM_GUARD_DEFAULT_ENABLED = True


def default_spam_guard_enabled() -> bool:
    return SPAM_GUARD_DEFAULT_ENABLED


__all__ = [
    "SPAM_GUARD_DEFAULT_ENABLED",
    "default_spam_guard_enabled",
]
