from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from stoney_verify.commands_ext import public_staff_scope


ROOT = Path(__file__).resolve().parents[1]


def test_configured_ticket_staff_roles_support_object_and_dict_configs(monkeypatch) -> None:
    from stoney_verify import guild_config

    monkeypatch.setattr(
        guild_config,
        "get_cached_guild_config",
        lambda _guild_id: SimpleNamespace(
            staff_role_id="111",
            vc_staff_role_id=222,
            effective_vc_staff_role_id="222",
        ),
    )
    assert public_staff_scope.configured_ticket_staff_role_ids(1) == [111, 222]

    monkeypatch.setattr(
        guild_config,
        "get_cached_guild_config",
        lambda _guild_id: {
            "staff_role_id": "333",
            "vc_staff_role_id": None,
            "effective_vc_staff_role_id": 444,
        },
    )
    assert public_staff_scope.configured_ticket_staff_role_ids(2) == [333, 444]


def test_ticket_ui_and_permission_sync_use_per_guild_staff_truth() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_staff_scope.py").read_text(encoding="utf-8")
    assert "ticket_panel._is_staff_member = scoped_is_staff" in source
    assert "ticket_transcripts._is_staff_member = scoped_is_staff" in source
    assert "ticket_service._default_staff_role_ids = configured_ticket_staff_role_ids" in source
    assert "install_transcript_claim_runtime_guards(ticket_transcripts)" in source
    assert "install_ticket_admin_claim_guard(" in source
    assert "install_api_claim_runtime_guards(ticket_api_server)" in source
    assert 'globals().get("STAFF_ROLE_ID")' not in source
    assert 'globals().get("MOD_ROLE_ID")' not in source
    assert 'globals().get("ADMIN_ROLE_ID")' not in source


def test_ticket_staff_scope_fails_closed_when_a_critical_patch_is_missing() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_staff_scope.py").read_text(encoding="utf-8")
    assert 'installed = {' in source
    assert '"ticket_panel": False' in source
    assert '"ticket_transcripts": False' in source
    assert '"ticket_permissions": False' in source
    assert '"ticket_claim_runtime": False' in source
    assert '"ticket_admin_claim_runtime": False' in source
    assert '"ticket_api_claim_runtime": False' in source
    assert "missing = sorted" in source
    assert "failed closed; missing patches" in source
    assert 'if not _PATCHED:' in source
