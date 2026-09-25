from pathlib import Path

SOURCE = Path("stoney_verify/setup_permission_repair_services.py").read_text(encoding="utf-8")
GUARD = Path("stoney_verify/startup_guards/setup_permission_repair_guard.py").read_text(encoding="utf-8")
POLICY = Path("stoney_verify/services/setup_permission_policy.py").read_text(encoding="utf-8")
DEFAULTS = Path("stoney_verify/commands_ext/public_setup_defaults.py").read_text(encoding="utf-8")
ASSISTANT = Path("stoney_verify/commands_ext/public_setup_assistant.py").read_text(encoding="utf-8")
README = Path("README.md").read_text(encoding="utf-8")
AUDIT = Path("tools/audit_public_invite_permissions.py").read_text(encoding="utf-8")


def test_permission_repair_can_restore_activity_coverage_only_when_opted_in():
    assert "_merge_activity_coverage_targets" in SOURCE
    assert "include_activity_coverage: bool = False" in SOURCE
    assert "if include_activity_coverage:" in SOURCE
    assert "ignore_administrator=temporary_admin_active" in SOURCE
    assert '"Read Message History": "read_message_history"' in SOURCE
    assert '"Manage Threads": "manage_threads"' in SOURCE
    assert "Member and staff overwrites are not changed" in SOURCE


def test_public_permissions_include_manage_threads():
    assert "Manage Threads" in README
    assert '"Manage Threads"' in AUDIT


def test_activity_repair_uses_parent_only_as_bot_template_and_never_full_sync():
    assert "current = channel.overwrites_for(me)" in SOURCE
    assert "seed_bot_overwrite_from_parent" in SOURCE
    assert "{me: expected}" in SOURCE
    assert 'for collection_name in ("channels", "threads")' in SOURCE
    assert "expected.manage_roles = True" in SOURCE
    assert "sync_permissions=True" not in SOURCE
    assert "build_bot_overwrite_bootstrap_plan" not in SOURCE
    assert "self-lockout bootstrap" not in SOURCE


def test_normal_setup_overwrites_do_not_manufacture_manage_permissions():
    assert "manage_roles=True" not in GUARD
    assert "manage_roles=True" not in POLICY
    assert "manage_roles=True" not in DEFAULTS
    assert "manage_roles=True" not in ASSISTANT
    assert "temporary_admin_active" in SOURCE
    assert "expected.manage_roles = True" in SOURCE
