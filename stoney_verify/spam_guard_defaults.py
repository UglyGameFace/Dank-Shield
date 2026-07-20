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


def _install_startup_state_reporting() -> None:
    """Attach SpamGuard's native startup reporter through a guaranteed live import.

    ``spam_guard.py`` imports this policy module on the real runtime path. Importing
    the small SpamGuard-owned reporter here registers its ``on_ready`` listener
    without enabling the dormant startup-guard registry.
    """

    try:
        from . import spam_guard_startup_state as _spam_guard_startup_state  # noqa: F401
    except Exception as exc:
        print(f"⚠️ SpamGuard startup state reporter registration failed: {type(exc).__name__}: {exc}")


_install_startup_state_reporting()
