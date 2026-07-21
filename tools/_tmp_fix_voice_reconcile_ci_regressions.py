from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(__file__).resolve()
GUIDED = ROOT / "tests/test_setup_guided_one_item_behavior.py"
VOICE = ROOT / "tests/test_setup_voice_resource_reconciliation_behavior.py"
PROBE = ROOT / ".github/workflows/_tmp_pytest_failure_probe.yml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return text.replace(old, new, 1)


guided = GUIDED.read_text(encoding="utf-8")
voice = VOICE.read_text(encoding="utf-8")

old_expected = '''    assert events[1] == (
        "save",
        {
            "vc_verify_queue_channel_id": "8080",
            "vc_queue_channel_id": "8080",
            "vc_request_channel_id": "8080",
            "vc_verify_requests_channel_id": "8080",
        },
    )
'''
new_expected = '''    assert events[1] == (
        "save",
        {
            "vc_verify_queue_channel_id": "8080",
            "vc_queue_channel_id": "8080",
            "vc_request_channel_id": "8080",
            "vc_verify_requests_channel_id": "8080",
            "vc_verify_queue_channel_managed_id": "8080",
        },
    )
'''
guided = replace_once(
    guided,
    old_expected,
    new_expected,
    "guided created voice staff provenance expectation",
)

voice = replace_once(
    voice,
    "from __future__ import annotations\n\nfrom pathlib import Path\n",
    "from __future__ import annotations\n\nimport asyncio\nfrom pathlib import Path\n",
    "voice test asyncio import",
)

voice = replace_once(
    voice,
    '''@pytest.mark.asyncio
async def test_saved_all_off_custom_state_is_not_resurrected(monkeypatch):
''',
    '''def test_saved_all_off_custom_state_is_not_resurrected(monkeypatch):
''',
    "saved all-off test sync wrapper",
)
voice = replace_once(
    voice,
    '''    resolved, message = await fresh._autofill_custom_state_from_existing(
        SimpleNamespace(id=123),
        state,
    )
''',
    '''    resolved, message = asyncio.run(
        fresh._autofill_custom_state_from_existing(
            SimpleNamespace(id=123),
            state,
        )
    )
''',
    "saved all-off asyncio.run",
)

voice = replace_once(
    voice,
    '''@pytest.mark.asyncio
async def test_voice_off_removes_proven_managed_defaults_and_clears_mappings(monkeypatch):
''',
    '''def test_voice_off_removes_proven_managed_defaults_and_clears_mappings(monkeypatch):
''',
    "voice off reconciliation test sync wrapper",
)
voice = replace_once(
    voice,
    '''    message = await reconcile.reconcile_disabled_voice_verify(guild)
''',
    '''    message = asyncio.run(
        reconcile.reconcile_disabled_voice_verify(guild)
    )
''',
    "voice off reconciliation asyncio.run",
)

if "@pytest.mark.asyncio" in voice:
    raise RuntimeError("unexpected pytest-asyncio marker remains in voice reconciliation tests")

compile(guided, str(GUIDED), "exec")
compile(voice, str(VOICE), "exec")

GUIDED.write_text(guided, encoding="utf-8")
VOICE.write_text(voice, encoding="utf-8")

if PROBE.exists():
    PROBE.unlink()

HELPER.unlink()
subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Updated guided Voice Verify creation expectation for managed provenance.")
print("✅ Rewrote async reconciliation tests to use asyncio.run without pytest-asyncio.")
print("✅ Temporary pytest probe workflow removed.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
