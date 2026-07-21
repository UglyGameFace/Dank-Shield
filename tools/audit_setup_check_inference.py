#!/usr/bin/env python3
from __future__ import annotations

"""Sanity-check native setup-state inference.

The public setup flow no longer uses the retired
``setup_check_existing_server_inference_guard``. Setup Home, Review Setup, Quick
Setup, and Test Your Setup all read the canonical native service state.

This audit executes that behavior directly instead of inspecting source strings.
"""

from stoney_verify.commands_ext import public_setup_recommend as recommend


def _services(config: dict[str, object]) -> dict[str, bool]:
    return recommend._selected_setup_services(config)


def _assert(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    # Custom Setup must respect explicit choices instead of inventing template
    # defaults. This is the regression that originally broke Test Your Setup.
    custom = _services(
        {
            "setup_choice": "custom_setup",
            "tickets_enabled": False,
            "verification_enabled": True,
            "voice_verification_enabled": False,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        }
    )
    _assert(custom["tickets"] is False, "Custom Setup invented Tickets")
    _assert(custom["basic_verify"] is True, "Custom Setup lost Simple Verify")
    _assert(custom["voice"] is False, "Custom Setup invented Voice Verify")

    # Explicit saved switches must beat a template default.
    explicit_off = _services(
        {
            "setup_choice": "basic_server",
            "tickets_enabled": False,
            "spam_guard_enabled": True,
            "moderation_enabled": True,
        }
    )
    _assert(
        explicit_off["tickets"] is False,
        "Explicit Tickets OFF did not override the setup plan",
    )
    _assert(explicit_off["spam_guard"] is True, "SpamGuard state was lost")
    _assert(explicit_off["logs"] is True, "Essential Logs state was lost")

    # Voice verification depends on ticket/staff workflow and logging. The
    # canonical state normalizer must expose those dependencies everywhere.
    voice = _services(
        {
            "setup_choice": "custom_setup",
            "tickets_enabled": False,
            "verification_enabled": True,
            "voice_verification_enabled": True,
            "moderation_enabled": False,
        }
    )
    _assert(voice["voice"] is True, "Voice Verify did not remain enabled")
    _assert(voice["tickets"] is True, "Voice Verify dependency did not enable Tickets")
    _assert(voice["logs"] is True, "Voice Verify dependency did not enable Logs")

    # No saved plan and no explicit switches should remain genuinely unstarted.
    blank = _services({})
    _assert(not any(blank.values()), "Blank setup inferred enabled services")

    print("Native setup-state inference audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
