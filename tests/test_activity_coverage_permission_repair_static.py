from pathlib import Path

SOURCE = Path("stoney_verify/setup_permission_repair_services.py").read_text(encoding="utf-8")
README = Path("README.md").read_text(encoding="utf-8")
AUDIT = Path("tools/audit_public_invite_permissions.py").read_text(encoding="utf-8")


def test_permission_repair_can_restore_activity_coverage_only_when_opted_in():
    assert "_merge_activity_coverage_targets" in SOURCE
    assert "include_activity_coverage: bool = False" in SOURCE
    assert "if include_activity_coverage:" in SOURCE
    assert "audit_activity_scope(guild)" in SOURCE
    assert '"Read Message History" in required' in SOURCE
    assert '"Manage Threads" in required' in SOURCE
    assert "Member and staff overwrites are not changed" in SOURCE


def test_public_permissions_include_manage_threads():
    assert "Manage Threads" in README
    assert '"Manage Threads"' in AUDIT



def test_activity_repair_preserves_existing_bot_overwrite_before_adding_access():
    assert "expected = channel.overwrites_for(me)" in SOURCE
    assert '"View Channel" in required' in SOURCE
    assert '"Read Message History" in required' in SOURCE
    assert '"Manage Threads" in required' in SOURCE
    assert "{me: expected}" in SOURCE
    assert 'for collection_name in ("channels", "threads")' in SOURCE
