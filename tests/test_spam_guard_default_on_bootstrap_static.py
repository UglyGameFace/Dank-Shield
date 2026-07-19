from pathlib import Path

from stoney_verify.spam_guard_defaults import SPAM_GUARD_DEFAULT_ENABLED

SOURCE = Path("stoney_verify/spam_guard.py").read_text(encoding="utf-8")
MODES = Path("stoney_verify/startup_guards/setup_service_modes.py").read_text(encoding="utf-8")
FRESH = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(encoding="utf-8")


def test_authoritative_spam_guard_enabled_policy_is_on():
    assert SPAM_GUARD_DEFAULT_ENABLED is True


def test_spam_guard_runtime_defaults_match_authoritative_on_policy():
    block = SOURCE[SOURCE.index("def _default_settings("):SOURCE.index("def _normalize_settings(")]
    assert '"enabled": True' in block
    assert '"enabled": False' not in block
    assert SPAM_GUARD_DEFAULT_ENABLED is True


def test_missing_settings_row_is_bootstrapped_to_database():
    load = SOURCE[SOURCE.index("async def get_spam_settings("):SOURCE.index("async def save_spam_settings(")]
    assert "created_default_row=True" in load
    assert "db-bootstrap" in load
    assert "_upsert_settings_sync" in load
    assert "effective_row_found = bool(row_found or persisted)" in load


def test_existing_saved_off_state_is_not_forced_on():
    normalize = SOURCE[SOURCE.index("def _normalize_settings("):SOURCE.index("def _settings_payload_for_db(")]
    assert 'row.get("spam_blocker_enabled", row.get("enabled"))' in normalize


def test_setup_defaults_match_authoritative_on_policy():
    assert SPAM_GUARD_DEFAULT_ENABLED is True
    assert 'ServiceState(True, False, False, True, True, "defaults")' in MODES
    assert '_cfg_value(cfg, "spam_guard_enabled", True), True' in MODES
    default_block = MODES[MODES.index("def _default_spam_settings("):MODES.index("def _normalize_spam_settings(")]
    assert '"enabled": True' in default_block
    assert '"enabled": False' not in default_block


def test_normal_setup_choices_select_spam_guard_by_default():
    choice_block = FRESH[FRESH.index("def _service_flags_for_choice("):FRESH.index("def _choice_payload(")]
    for key in ("basic_server", "basic_verify", "help_desk", "voice_check", "id_check"):
        assert key in choice_block
    assert choice_block.count('"spam_guard_enabled": True') >= 4
