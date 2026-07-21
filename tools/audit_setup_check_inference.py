#!/usr/bin/env python3
from __future__ import annotations

"""Sanity-check the canonical native setup-state normalizer.

The retired setup inference guards are gone. Setup Home, Review Setup, Quick
Setup, and Test Your Setup all normalize saved guild configuration through
``service_state_from_config``.

This audit executes that owner directly. It deliberately avoids importing the
public command package so command-registration/startup side effects cannot
pollute a pure state check.
"""

from stoney_verify.setup_service_state import service_state_from_config


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    # Custom Setup must respect explicit choices instead of inventing template
    # defaults. This is the regression that originally broke Test Your Setup.
    custom = service_state_from_config(
        {
            "setup_choice": "custom_setup",
            "tickets_enabled": False,
            "verification_enabled": True,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        }
    )
    _assert(custom.tickets is False, "Custom Setup invented Tickets")
    _assert(custom.simple_verify is True, "Custom Setup lost Simple Verify")
    _assert(custom.voice_verify is False, "Custom Setup invented Voice Verify")

    # Explicit saved switches must beat a template default.
    explicit_off = service_state_from_config(
        {
            "setup_choice": "basic_server",
            "tickets_enabled": False,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        }
    )
    _assert(
        explicit_off.tickets is False,
        "Explicit Tickets OFF did not override the setup plan",
    )
    _assert(explicit_off.spam_guard is True, "SpamGuard state was lost")
    _assert(explicit_off.logs is True, "Essential Logs state was lost")

    # Voice verification depends on ticket/staff workflow and logging. The
    # canonical normalizer must expose those dependencies everywhere.
    voice = service_state_from_config(
        {
            "setup_choice": "custom_setup",
            "tickets_enabled": False,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "moderation_enabled": False,
        }
    )
    _assert(voice.voice_verify is True, "Voice Verify did not remain enabled")
    _assert(
        voice.tickets is True,
        "Voice Verify dependency did not enable Tickets",
    )
    _assert(
        voice.logs is True,
        "Voice Verify dependency did not enable Logs",
    )

    # No saved plan and no explicit switches should remain genuinely unstarted.
    blank = service_state_from_config({})
    _assert(blank.any_enabled is False, "Blank setup inferred enabled services")
    _assert(blank.setup_choice == "", "Blank setup inferred a setup plan")

    print("Native setup-state inference audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
