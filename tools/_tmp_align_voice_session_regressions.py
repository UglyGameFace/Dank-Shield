from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCOREBOARD = ROOT / "stoney_verify/startup_guards/setup_feature_health_scoreboard.py"
GUIDED_TEST = ROOT / "tests/test_setup_guided_one_item_behavior.py"
RETIRED_TEST = ROOT / "tests/test_vc_verified_health_guard_retired.py"
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"
SELF = Path(__file__).resolve()


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return source.replace(old, new, 1)


def replace_between(
    source: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
    label: str,
) -> str:
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker not found")
    end = source.find(end_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(f"{label}: end marker not found")
    if source.find(start_marker, start + 1) >= 0:
        raise RuntimeError(f"{label}: duplicate start marker found")
    return source[:start] + replacement + source[end:]


scoreboard = SCOREBOARD.read_text(encoding="utf-8")
guided_test = GUIDED_TEST.read_text(encoding="utf-8")
recommend = RECOMMEND.read_text(encoding="utf-8")

# The canonical setup must already have retired the permanent approved-role
# Voice-room access rule before these regressions are aligned.
for forbidden in (
    "def _verified_role_voice_access(",
    '"verified_voice_access"',
    "Allow Approved Members Into Voice Verify",
):
    if forbidden in recommend:
        raise RuntimeError(
            f"canonical setup still contains retired Voice access marker: {forbidden}"
        )

# Retire the duplicate old rule from the optional/dormant feature scoreboard too.
scoreboard = replace_between(
    scoreboard,
    "def _verified_role_voice_access(\n",
    "def _db_table_readable_sync(table: str) -> bool:\n",
    "",
    "remove scoreboard approved-role Voice helper",
)

old_voice_block = '''    if voice is not None:\n        member_access_ok, member_access_text = (\n            _verified_role_voice_access(\n                guild,\n                cfg,\n                voice,\n            )\n        )\n\n        if not member_access_ok:\n            blockers.append(member_access_text)\n'''
scoreboard = replace_once(
    scoreboard,
    old_voice_block,
    "",
    "remove scoreboard approved-role blocker",
)
scoreboard = replace_once(
    scoreboard,
    '''    return FeatureHealth("Voice Verify", "🎙️", "ready", f"Voice `{getattr(voice, 'name', 'configured')}` and queue `{queue.name}` are ready.")\n''',
    '''    return FeatureHealth(\n        "Voice Verify",\n        "🎙️",\n        "ready",\n        (\n            f"Voice `{getattr(voice, 'name', 'configured')}` and queue "\n            f"`{queue.name}` are ready for session-based requester and "\n            "assigned-staff access."\n        ),\n    )\n''',
    "scoreboard session-ready summary",
)

# The existing callback regression must assert the new one-action bundle, not
# the retired behavior where the queue was created alone.
new_guided_test = '''def test_voice_setup_action_saves_room_and_staff_queue_as_bundle(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    interaction = FakeInteraction()\n    events: list[tuple[str, Any]] = []\n\n    async def allow(*args: Any, **kwargs: Any) -> bool:\n        return True\n\n    async def defer(*args: Any, **kwargs: Any) -> None:\n        events.append(("defer", None))\n\n    async def current(*args: Any, **kwargs: Any) -> bool:\n        return True\n\n    async def get_config(*args: Any, **kwargs: Any) -> dict[str, Any]:\n        return {}\n\n    async def create_exact(\n        _guild: Any,\n        _cfg: Any,\n        requirement_key: str,\n    ) -> tuple[Any, list[str], list[str], list[str]]:\n        if requirement_key == "voice_verify_channel":\n            return (\n                SimpleNamespace(id=7070),\n                [],\n                ["Voice: created"],\n                [],\n            )\n        assert requirement_key == "voice_verify_staff_channel"\n        return (\n            SimpleNamespace(id=8080),\n            [],\n            ["Channel: created"],\n            [],\n        )\n\n    async def save(\n        interaction_arg: Any,\n        payload: dict[str, str],\n    ) -> None:\n        assert interaction_arg is interaction\n        events.append(("save", payload))\n\n    async def guided(\n        interaction_arg: Any,\n        **kwargs: Any,\n    ) -> None:\n        assert interaction_arg is interaction\n        events.append(("guided", kwargs))\n\n    monkeypatch.setattr(\n        recommend.solid,\n        "_require_setup_permission",\n        allow,\n    )\n    monkeypatch.setattr(\n        recommend.solid,\n        "_safe_defer_update",\n        defer,\n    )\n    monkeypatch.setattr(\n        recommend,\n        "_guided_step_is_current",\n        current,\n    )\n    monkeypatch.setattr(\n        recommend,\n        "get_guild_config",\n        get_config,\n    )\n    monkeypatch.setattr(\n        recommend,\n        "_guided_create_exact_item",\n        create_exact,\n    )\n    monkeypatch.setattr(\n        recommend.solid,\n        "_save_config",\n        save,\n    )\n    monkeypatch.setattr(\n        recommend,\n        "_open_guided_setup",\n        guided,\n    )\n\n    run(\n        recommend._guided_create_item(\n            interaction,\n            "voice_verify_staff_channel",\n        )\n    )\n\n    assert events[0] == ("defer", None)\n    assert events[1] == (\n        "save",\n        {\n            "vc_verify_channel_id": "7070",\n            "vc_verify_channel_managed_id": "7070",\n            "vc_verify_queue_channel_id": "8080",\n            "vc_queue_channel_id": "8080",\n            "vc_request_channel_id": "8080",\n            "vc_verify_requests_channel_id": "8080",\n            "vc_verify_queue_channel_managed_id": "8080",\n        },\n    )\n    assert events[2][0] == "guided"\n    saved_message = events[2][1]["saved_message"]\n    assert "private Voice Verify room" in saved_message\n    assert "staff request channel together" in saved_message\n'''
start = "def test_created_voice_staff_channel_saves_all_aliases_and_advances(\n"
position = guided_test.find(start)
if position < 0:
    raise RuntimeError("guided bundle regression: old test start not found")
guided_test = guided_test[:position] + new_guided_test

# Replace the obsolete static contract with behavior that fails if either the
# canonical setup or the optional scoreboard resurrects permanent Verified-role
# Voice access.
retired_test = '''from __future__ import annotations\n\nimport ast\nfrom pathlib import Path\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom stoney_verify.startup_guards import (\n    setup_feature_health_scoreboard as scoreboard,\n)\n\n\nROOT = Path(__file__).resolve().parents[1]\nTARGET = (\n    ROOT\n    / "stoney_verify"\n    / "startup_guards"\n    / "vc_verified_health_check_guard.py"\n)\nRECOMMEND = (\n    ROOT\n    / "stoney_verify"\n    / "commands_ext"\n    / "public_setup_recommend.py"\n)\nPROTECTION = (\n    ROOT\n    / "stoney_verify"\n    / "startup_guards"\n    / "protection_center_invite_simple_flow_guard.py"\n)\n\n\ndef _source(path: Path) -> str:\n    return path.read_text(encoding="utf-8")\n\n\ndef _owners(path: Path) -> dict[str, ast.AST]:\n    source = _source(path)\n    tree = ast.parse(source, filename=str(path))\n    return {\n        node.name: node\n        for node in tree.body\n        if isinstance(\n            node,\n            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),\n        )\n    }\n\n\ndef _owner_source(path: Path, name: str) -> str:\n    source = _source(path)\n    return ast.get_source_segment(source, _owners(path)[name]) or ""\n\n\ndef test_legacy_guard_is_deleted() -> None:\n    assert not TARGET.exists()\n\n\ndef test_hidden_protection_loader_is_removed() -> None:\n    assert "vc_verified_health_check_guard" not in _source(PROTECTION)\n\n\ndef test_canonical_setup_uses_session_access_not_approved_role_access() -> None:\n    owners = _owners(RECOMMEND)\n    assert "_verified_role_voice_access" not in owners\n\n    health = _owner_source(RECOMMEND, "_build_plain_setup_health_embed")\n    guided = _owner_source(RECOMMEND, "_guided_setup_target")\n    dispatcher = _owner_source(RECOMMEND, "_open_guided_target")\n\n    combined = "\\n".join((health, guided, dispatcher))\n    for forbidden in (\n        "verified_voice_access",\n        "Allow Approved Members Into Voice Verify",\n        "Edit Channel → Permissions",\n        "View Channel, Connect, Speak",\n    ):\n        assert forbidden not in combined\n\n    assert "_has_typed_channel" in health\n    assert "session-based" in health\n    assert "active requester" in health\n    assert "assigned staff" in health\n\n\ndef test_scoreboard_voice_health_uses_session_contract(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    voice = SimpleNamespace(name="Voice Verification")\n    queue = SimpleNamespace(name="vc-verify-queue")\n    staff = SimpleNamespace(name="Support Team")\n\n    monkeypatch.setattr(scoreboard, "_voice_channel", lambda *_args: voice)\n    monkeypatch.setattr(scoreboard, "_text_channel", lambda *_args: queue)\n    monkeypatch.setattr(scoreboard, "_role", lambda *_args: staff)\n    monkeypatch.setattr(scoreboard, "_can_use_channel", lambda *_args, **_kwargs: True)\n\n    health = scoreboard._voice_score(\n        SimpleNamespace(),\n        {\n            "vc_verify_channel_id": "101",\n            "vc_verify_queue_channel_id": "202",\n            # Deliberately no verified/member/approved role ID.\n        },\n        True,\n    )\n\n    assert health.status == "ready"\n    assert "session-based" in health.summary\n    assert "assigned-staff" in health.summary\n\n\ndef test_no_production_health_wrapper_remains() -> None:\n    forbidden = (\n        "vc_verified_health_check_guard",\n        "_ORIGINAL_HEALTH",\n        "patched_health",\n        "Verified VC Access",\n    )\n    roots = (\n        ROOT / "stoney_verify" / "commands_ext",\n        ROOT / "stoney_verify" / "startup_guards",\n    )\n    for root in roots:\n        for path in root.rglob("*.py"):\n            source = _source(path)\n            for marker in forbidden:\n                assert marker not in source, f"{marker!r} remains in {path}"\n\n\ndef test_has_role_accepts_alias_keys() -> None:\n    body = _owner_source(RECOMMEND, "_has_role")\n    assert "*keys: str" in body\n    assert "for key in keys:" in body\n'''

# Validate all generated code before any file is written.
for path, text in (
    (SCOREBOARD, scoreboard),
    (GUIDED_TEST, guided_test),
    (RETIRED_TEST, retired_test),
):
    compile(text, str(path), "exec")

# Confirm the duplicate old access helper is gone from both setup owners.
if "def _verified_role_voice_access(" in scoreboard:
    raise RuntimeError("scoreboard still contains retired approved-role Voice helper")
if "_verified_role_voice_access(" in scoreboard:
    raise RuntimeError("scoreboard still calls retired approved-role Voice helper")

SCOREBOARD.write_text(scoreboard, encoding="utf-8")
GUIDED_TEST.write_text(guided_test, encoding="utf-8")
RETIRED_TEST.write_text(retired_test, encoding="utf-8")
SELF.unlink()

subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Removed the duplicate approved-role Voice blocker from the feature scoreboard.")
print("✅ Guided creation regression now requires the room + staff queue bundle.")
print("✅ Retired-health tests now protect session-only requester/assigned-staff access.")
print("✅ Generated Python compiles.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
