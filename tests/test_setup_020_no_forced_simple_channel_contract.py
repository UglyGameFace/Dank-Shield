from __future__ import annotations

from stoney_verify import setup_020_entitled_id_guard
from stoney_verify.commands_ext import public_setup_defaults as defaults
from stoney_verify.commands_ext import public_setup_fresh_choice as fresh


assert setup_020_entitled_id_guard.install() is True


def test_specialized_plans_do_not_force_simple_verify():
    voice = fresh.get_plain_setup_choice("voice_check")
    id_only = fresh.get_plain_setup_choice("id_check")
    id_voice = fresh.get_plain_setup_choice("id_voice_check")

    assert voice is not None
    assert id_only is not None
    assert id_voice is not None

    for choice in (voice, id_only, id_voice):
        flags = fresh._service_flags_for_choice(choice)
        assert flags["verification_enabled"] is False
        assert flags["tickets_enabled"] is True
        assert flags["moderation_enabled"] is True


def test_voice_and_id_scope_require_verification_roles_not_simple_channel():
    for payload in (
        {
            "setup_choice": "custom_setup",
            "tickets_enabled": True,
            "verification_enabled": False,
            "basic_verify_enabled": False,
            "voice_verification_enabled": True,
            "id_verify_enabled": False,
            "moderation_enabled": True,
        },
        {
            "setup_choice": "custom_setup",
            "tickets_enabled": True,
            "verification_enabled": False,
            "basic_verify_enabled": False,
            "voice_verification_enabled": False,
            "id_verify_enabled": True,
            "moderation_enabled": True,
        },
    ):
        scope = defaults._service_scope_from_config(payload)
        assert scope["verify"] is True
        assert scope["basic_verify"] is False
