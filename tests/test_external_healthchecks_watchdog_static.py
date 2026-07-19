from __future__ import annotations

from pathlib import Path

PROCESS = Path("stoney_verify/startup_guards/process_health.py").read_text(encoding="utf-8")
STATUS = Path("stoney_verify/commands_ext/public_status_reporter.py").read_text(encoding="utf-8")
SPAM = Path("stoney_verify/spam_guard.py").read_text(encoding="utf-8")
COMMANDS = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")


def test_external_healthchecks_ping_is_real_http_heartbeat():
    assert "DANK_HEALTHCHECKS_PING_URL" in PROCESS
    assert "urllib.request.urlopen" in PROCESS
    assert "await _ping_external_watchdog()" in PROCESS
    assert "external_watchdog={watchdog_state}" in PROCESS
    assert "hc-ping.com/" not in PROCESS


def test_status_report_distinguishes_internal_and_external_heartbeats():
    assert '"Internal DB heartbeat"' in STATUS
    assert '"External uptime watchdog"' in STATUS
    assert '"Status heartbeat"' not in STATUS
    assert "external_watchdog_status" in STATUS


def test_spamguard_missing_rows_default_on_and_bootstrap():
    defaults = SPAM[SPAM.index("def _default_settings("):SPAM.index("def _normalize_settings(")]
    load = SPAM[SPAM.index("async def get_spam_settings("):SPAM.index("async def save_spam_settings(")]
    assert '"enabled": True' in defaults
    assert "_upsert_settings_sync" in load
    assert 'source = "db" if row_found else ("db-bootstrap" if persisted else "defaults")' in load


def test_child_prune_skip_log_is_once_per_process():
    assert "_CHILD_PRUNE_SKIP_LOGGED = False" in COMMANDS
    assert "if not _CHILD_PRUNE_SKIP_LOGGED:" in COMMANDS
    assert "_CHILD_PRUNE_SKIP_LOGGED = True" in COMMANDS
