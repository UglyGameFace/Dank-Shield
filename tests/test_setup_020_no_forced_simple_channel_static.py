from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_create_simple_channel_only_for_basic_verify():
    source = (ROOT / "stoney_verify/commands_ext/public_setup_defaults.py").read_text(encoding="utf-8")
    assert 'if services["basic_verify"]:\n        verify_channel = (' in source
    assert 'if services["verify"]:\n        verify_channel = (' not in source


def test_guided_requirement_uses_basic_verify_for_text_channel():
    source = (ROOT / "stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
    marker = '"Choose the Simple Verify Channel"'
    before = source[: source.index(marker)]
    assert 'if services["basic_verify"]:' in before[-700:]
