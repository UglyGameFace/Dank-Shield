from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import setup_020_entitled_id_guard
from stoney_verify import setup_service_state as service_state
from stoney_verify.commands_ext import public_setup_defaults as defaults
from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.setup_engine.verification_modes import id_verify_allowed_for_guild


ENTITLED_GUILD_ID = 1357215261001912320

# Production installs this compatibility layer from vc_setup_one_press_fix after
# legacy setup modules load. These focused unit tests import the modules directly,
# so install the same runtime integration explicitly.
assert setup_020_entitled_id_guard.install() is True


def test_partner_guild_has_builtin_id_entitlement():
    guild = SimpleNamespace(id=ENTITLED_GUILD_ID, name="Partner")
    assert id_verify_allowed_for_guild(guild) is True


def test_entitled_custom_id_voice_does_not_enable_simple_verify():
    patch = service_state.normalize_custom_service_patch(
        {
            "tickets_enabled": False,
            "verification_enabled": False,
            "voice_verification_enabled": True,
            "id_verify_enabled": True,
            "spam_guard_enabled": False,
            "moderation_enabled": False,
        },
        allow_id_verify=True,
    )
    assert patch["tickets_enabled"] is True
    assert patch["verification_enabled"] is False
    assert patch["basic_verify_enabled"] is False
    assert patch["id_verify_enabled"] is True
    assert patch["voice_verification_enabled"] is True
    assert patch["verification_panel_style"] == "id_voice_check"
    assert patch["moderation_enabled"] is True


def test_non_entitled_custom_save_cannot_self_enable_id_verify():
    patch = service_state.normalize_custom_service_patch(
        {"id_verify_enabled": True},
        allow_id_verify=False,
    )
    assert patch["id_verify_enabled"] is False
    assert patch["verification_requires_id"] is False


def test_id_toggle_is_visible_only_for_entitled_guild():
    state = service_state.SetupServiceState(
        setup_choice="custom_setup",
        setup_label="Custom",
        tickets=False,
        simple_verify=False,
        voice_verify=False,
        id_verify=False,
        spam_guard=False,
        logs=False,
    )
    entitled = SimpleNamespace(id=ENTITLED_GUILD_ID, name="Partner")
    normal = SimpleNamespace(id=123, name="Normal")
    entitled_view = fresh.CustomServiceModeView(state, guild=entitled)
    normal_view = fresh.CustomServiceModeView(state, guild=normal)
    entitled_ids = {getattr(item, "key", "") for item in entitled_view.children}
    normal_ids = {getattr(item, "key", "") for item in normal_view.children}
    assert "id_verify_enabled" in entitled_ids
    assert "id_verify_enabled" not in normal_ids


def test_stale_toggle_refreshes_without_overwriting_newer_state(monkeypatch):
    current = service_state.SetupServiceState(
        setup_choice="custom_setup",
        setup_label="Custom",
        tickets=True,
        simple_verify=False,
        voice_verify=False,
        id_verify=True,
        spam_guard=False,
        logs=True,
    )

    async def load_state(_guild_id: int):
        return current

    monkeypatch.setattr(service_state, "load_setup_service_state", load_state)

    async def scenario():
        return await service_state.toggle_custom_service_state(
            ENTITLED_GUILD_ID,
            "id_verify_enabled",
            allow_id_verify=True,
            expected_current=False,
        )

    saved, effective, changed, note = asyncio.run(scenario())
    assert saved is current
    assert effective is True
    assert changed is False
    assert "out of date" in note


def test_id_only_service_scope_never_creates_simple_verify_channel():
    scope = defaults._service_scope_from_config(
        {
            "setup_choice": "custom_setup",
            "tickets_enabled": True,
            "verification_enabled": False,
            "basic_verify_enabled": False,
            "voice_verification_enabled": False,
            "id_verify_enabled": True,
            "moderation_enabled": True,
        }
    )
    assert scope["verify"] is True
    assert scope["id"] is True
    assert scope["basic_verify"] is False


def test_guided_target_does_not_request_simple_channel_for_id_only(monkeypatch):
    cfg = {
        "setup_choice": "custom_setup",
        "tickets_enabled": False,
        "verification_enabled": False,
        "basic_verify_enabled": False,
        "voice_verification_enabled": False,
        "id_verify_enabled": True,
        "moderation_enabled": True,
        "verified_role_id": "20",
        "unverified_role_id": "21",
        "staff_role_id": "22",
        "ticket_category_id": "30",
        "modlog_channel_id": "40",
    }
    role_ids = {20, 21, 22}
    channel_ids = {30, 40}
    guild = SimpleNamespace(
        id=ENTITLED_GUILD_ID,
        name="Partner",
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                read_message_history=True,
                manage_channels=True,
                manage_roles=True,
                attach_files=True,
                manage_messages=True,
            )
        ),
        get_role=lambda value: SimpleNamespace(id=value) if value in role_ids else None,
        get_channel=lambda value: SimpleNamespace(id=value) if value in channel_ids else None,
    )

    async def get_cfg(*args, **kwargs):
        return cfg

    async def category_load(_guild):
        return SimpleNamespace(error="", rows=[1])

    monkeypatch.setattr(recommend, "get_guild_config", get_cfg)
    monkeypatch.setattr(recommend.solid, "_category_load", category_load)
    target = asyncio.run(recommend._guided_setup_target(guild))
    assert target[3] != "verification_channel"
