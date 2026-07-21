from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(__file__).resolve()

writer_path = ROOT / "stoney_verify/commands_ext/public_setup_config_writer.py"
recommend_path = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
test_path = ROOT / "tests/test_setup_voice_resource_reconciliation_behavior.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


writer = writer_path.read_text(encoding="utf-8")
recommend = recommend_path.read_text(encoding="utf-8")
tests = test_path.read_text(encoding="utf-8")

writer_old = '''    attempts: list[dict[str, Any]] = []
    if "settings" in columns:
        attempts.append({**base_fields, "settings": settings, **flat_clear, **flat_metadata})
    if "config" in columns:
        attempts.append({**base_fields, "config": settings, **flat_clear, **flat_metadata})
    if flat_clear or flat_metadata:
        attempts.append({**base_fields, **flat_clear, **flat_metadata})

    if not attempts:
        return dict(existing)

    table = _config_table_name()
    last_error: Optional[Exception] = None
    for payload in attempts:
        try:
            response = (
                sb.table(table)
                .update(payload)
                .eq("guild_id", str(gid))
                .execute()
            )
            rows = getattr(response, "data", None) or []
            if rows and isinstance(rows[0], Mapping):
                return dict(rows[0])
            refreshed = _fetch_existing_config_row_sync(gid)
            return refreshed or payload
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Failed clearing guild config keys: {last_error!r}")
'''
writer_new = '''    # Keep every storage shape synchronized in one atomic update.  Returning
    # after updating only `settings` or only `config` would recreate the same
    # split-brain state this writer exists to prevent.
    json_updates: dict[str, Any] = {}
    if "settings" in columns:
        json_updates["settings"] = settings
    if "config" in columns:
        json_updates["config"] = settings

    payload = {
        **base_fields,
        **json_updates,
        **flat_clear,
        **flat_metadata,
    }
    if len(payload) == len(base_fields):
        return dict(existing)

    table = _config_table_name()
    try:
        response = (
            sb.table(table)
            .update(payload)
            .eq("guild_id", str(gid))
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if rows and isinstance(rows[0], Mapping):
            return dict(rows[0])
        refreshed = _fetch_existing_config_row_sync(gid)
        return refreshed or payload
    except Exception as exc:
        raise RuntimeError(f"Failed clearing guild config keys: {exc!r}") from exc
'''
writer = replace_once(writer, writer_old, writer_new, "atomic config clear")

recommend_anchor = '''def _guided_item_payload(
    requirement_key: str,
    item_id: int,
) -> dict[str, str]:
    spec = _guided_item_spec(requirement_key)

    if not spec:
        return {}

    clean_id = str(int(item_id))

    return {
        str(key): clean_id
        for key in spec.get("save_keys", ())
    }


'''
recommend_replacement = recommend_anchor + '''def _guided_managed_resource_patch(
    requirement_key: str,
    item_id: int,
    *,
    created: bool,
) -> dict[str, str]:
    """Record provenance only when Quick Setup actually created the resource."""

    if not created or int(item_id) <= 0:
        return {}
    key = str(requirement_key or "")
    if key == "voice_verify_channel":
        return {"vc_verify_channel_managed_id": str(int(item_id))}
    if key == "voice_verify_staff_channel":
        return {"vc_verify_queue_channel_managed_id": str(int(item_id))}
    return {}


'''
recommend = replace_once(
    recommend,
    recommend_anchor,
    recommend_replacement,
    "guided managed resource helper",
)

payload_old = '''    payload = _guided_item_payload(
        requirement_key,
        item_id,
    )

    if item_id <= 0 or not payload:
'''
payload_new = '''    payload = _guided_item_payload(
        requirement_key,
        item_id,
    )
    payload.update(
        _guided_managed_resource_patch(
            requirement_key,
            item_id,
            created=bool(created),
        )
    )

    if item_id <= 0 or not payload:
'''
recommend = replace_once(
    recommend,
    payload_old,
    payload_new,
    "guided creation provenance save",
)

if "def test_clear_writer_updates_both_json_buckets_atomically" not in tests:
    tests += '''\n\ndef test_clear_writer_updates_both_json_buckets_atomically(monkeypatch):\n    existing = {\n        "guild_id": "1",\n        "settings": {"vc_verify_channel_id": "123", "keep": "yes"},\n        "config": {"vc_verify_channel_id": "123", "keep": "yes"},\n        "vc_verify_channel_id": "123",\n    }\n    captured = {}\n\n    class _Table:\n        def update(self, payload):\n            captured.update(payload)\n            return self\n\n        def eq(self, *_args, **_kwargs):\n            return self\n\n        def execute(self):\n            return SimpleNamespace(data=[dict(captured)])\n\n    class _SB:\n        def table(self, _name):\n            return _Table()\n\n    monkeypatch.setattr(writer, "get_supabase", lambda: _SB())\n    monkeypatch.setattr(\n        writer,\n        "_fetch_existing_config_row_sync",\n        lambda _guild_id: dict(existing),\n    )\n\n    writer.clear_guild_config_keys_sync(\n        1,\n        {"vc_verify_channel_id"},\n    )\n\n    assert "settings" in captured\n    assert "config" in captured\n    assert "vc_verify_channel_id" not in captured["settings"]\n    assert "vc_verify_channel_id" not in captured["config"]\n    assert captured["settings"]["keep"] == "yes"\n    assert captured["config"]["keep"] == "yes"\n    assert captured["vc_verify_channel_id"] is None\n\n\ndef test_guided_created_voice_resources_record_provenance():\n    voice = fresh.recommend._guided_managed_resource_patch(\n        "voice_verify_channel",\n        101,\n        created=True,\n    )\n    queue = fresh.recommend._guided_managed_resource_patch(\n        "voice_verify_staff_channel",\n        202,\n        created=True,\n    )\n    reused = fresh.recommend._guided_managed_resource_patch(\n        "voice_verify_channel",\n        303,\n        created=False,\n    )\n\n    assert voice == {"vc_verify_channel_managed_id": "101"}\n    assert queue == {"vc_verify_queue_channel_managed_id": "202"}\n    assert reused == {}\n'''

# The test module already imports public_setup_fresh_choice as `fresh`; expose
# recommend through that module is an implementation accident, so test the
# canonical owner directly instead.
tests = tests.replace(
    "from stoney_verify.commands_ext import public_setup_fresh_choice as fresh\n",
    "from stoney_verify.commands_ext import public_setup_fresh_choice as fresh\nfrom stoney_verify.commands_ext import public_setup_recommend as recommend\n",
    1,
)
tests = tests.replace("fresh.recommend._guided_managed_resource_patch(", "recommend._guided_managed_resource_patch(")

for path, text in (
    (writer_path, writer),
    (recommend_path, recommend),
    (test_path, tests),
):
    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")

HELPER.unlink()
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
print("✅ Config key clearing now updates settings/config/flat storage atomically.")
print("✅ Quick Setup-created Voice Verify resources now persist managed provenance IDs.")
print("✅ Reused/custom Voice Verify resources are never marked as bot-managed.")
print("✅ Added regression coverage for both hardening fixes.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
