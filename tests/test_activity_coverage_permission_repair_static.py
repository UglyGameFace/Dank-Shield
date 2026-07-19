from pathlib import Path

SOURCE = Path("stoney_verify/setup_permission_repair_services.py").read_text(encoding="utf-8")
README = Path("README.md").read_text(encoding="utf-8")
AUDIT = Path("tools/audit_public_invite_permissions.py").read_text(encoding="utf-8")


def test_permission_repair_can_restore_activity_coverage():
    assert "_merge_activity_coverage_targets" in SOURCE
    assert "read_message_history=True" in SOURCE
    assert "expected.manage_threads = True" in SOURCE
    assert "Member visibility is not changed" in SOURCE


def test_public_permissions_include_manage_threads():
    assert "Manage Threads" in README
    assert '"Manage Threads"' in AUDIT
