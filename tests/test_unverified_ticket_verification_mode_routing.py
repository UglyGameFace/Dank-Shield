from __future__ import annotations

from types import SimpleNamespace

from stoney_verify.startup_guards import unverified_ticket_panel_flow as flow


ALLOWED_ID_GUILD = 1357215261001912320


def guild(guild_id: int = 4242) -> SimpleNamespace:
    return SimpleNamespace(id=guild_id, name=f"Guild {guild_id}")


def test_basic_verify_does_not_hijack_public_support_ticket() -> None:
    cfg = {
        "setup_choice": "basic_verify",
        "verification_enabled": True,
        "basic_verify_enabled": True,
        "basic_button_verify_enabled": True,
    }

    assert flow._should_auto_route_unverified_ticket(guild(), cfg) is False


def test_simple_plus_voice_still_keeps_public_support_as_support() -> None:
    # This is the production regression: Basic Verify is intentionally
    # authoritative even when Voice Verify is also available.
    cfg = {
        "setup_choice": "custom_setup",
        "verification_enabled": True,
        "basic_verify_enabled": True,
        "basic_button_verify_enabled": True,
        "voice_verification_enabled": True,
        "vc_verify_enabled": True,
    }

    assert flow._should_auto_route_unverified_ticket(guild(), cfg) is False


def test_voice_only_can_auto_route_unverified_ticket() -> None:
    cfg = {
        "setup_choice": "voice_check",
        "verification_enabled": True,
        "basic_verify_enabled": False,
        "basic_button_verify_enabled": False,
        "voice_verification_enabled": True,
        "vc_verify_enabled": True,
    }

    assert flow._should_auto_route_unverified_ticket(guild(), cfg) is True


def test_allowlisted_id_mode_can_auto_route_unverified_ticket() -> None:
    cfg = {
        "setup_choice": "id_check",
        "verification_enabled": True,
        "basic_verify_enabled": False,
        "id_verify_enabled": True,
        "verification_requires_id": True,
    }

    assert (
        flow._should_auto_route_unverified_ticket(
            guild(ALLOWED_ID_GUILD),
            cfg,
        )
        is True
    )


def test_unavailable_id_mode_does_not_force_broken_verification_ticket() -> None:
    cfg = {
        "setup_choice": "id_check",
        "verification_enabled": True,
        "basic_verify_enabled": False,
        "id_verify_enabled": True,
        "verification_requires_id": True,
    }

    assert flow._should_auto_route_unverified_ticket(guild(999), cfg) is False
