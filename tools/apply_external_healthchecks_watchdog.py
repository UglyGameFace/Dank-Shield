from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESS = ROOT / "stoney_verify/startup_guards/process_health.py"
STATUS = ROOT / "stoney_verify/commands_ext/public_status_reporter.py"
ENV_DOC = ROOT / "docs/public-production-env.md"
TEST = ROOT / "tests/test_external_healthchecks_watchdog_static.py"

process = PROCESS.read_text(encoding="utf-8")

process = process.replace(
    "import traceback\nfrom pathlib import Path",
    "import traceback\nimport urllib.request\nfrom pathlib import Path",
    1,
)

process = process.replace(
    '''_INSTALLED = False\n''',
    '''_INSTALLED = False\n_EXTERNAL_WATCHDOG_LAST_OK_AT = 0.0\n_EXTERNAL_WATCHDOG_LAST_ERROR = ""\n_EXTERNAL_WATCHDOG_LAST_FAILURE_LOG_AT = 0.0\n_EXTERNAL_WATCHDOG_WAS_OK = False\n''',
    1,
)

helper_marker = '''def _memory_snapshot() -> str:\n'''
helpers = '''def _external_watchdog_url() -> str:\n    for name in (\n        "DANK_HEALTHCHECKS_PING_URL",\n        "HEALTHCHECKS_PING_URL",\n        "HEALTHCHECK_PING_URL",\n        "HC_PING_URL",\n    ):\n        try:\n            value = str(os.getenv(name, "") or "").strip()\n        except Exception:\n            value = ""\n        if value:\n            return value\n    return ""\n\n\ndef _external_watchdog_timeout_seconds() -> float:\n    try:\n        raw = float(str(os.getenv("DANK_HEALTHCHECKS_TIMEOUT_SECONDS", "5") or "5").strip())\n        return max(1.0, min(raw, 15.0))\n    except Exception:\n        return 5.0\n\n\ndef external_watchdog_configured() -> bool:\n    return bool(_external_watchdog_url())\n\n\ndef external_watchdog_status() -> tuple[bool, bool, str]:\n    configured = external_watchdog_configured()\n    if not configured:\n        return False, False, "not configured — set `DANK_HEALTHCHECKS_PING_URL`"\n\n    if _EXTERNAL_WATCHDOG_LAST_OK_AT > 0:\n        age = max(0, int(time.time() - _EXTERNAL_WATCHDOG_LAST_OK_AT))\n        if _EXTERNAL_WATCHDOG_LAST_ERROR:\n            return True, False, f"last success {age}s ago; latest ping failed"\n        return True, True, f"pinging Healthchecks.io; last success {age}s ago"\n\n    if _EXTERNAL_WATCHDOG_LAST_ERROR:\n        return True, False, "configured; ping has not succeeded yet"\n    return True, False, "configured; waiting for first successful ping"\n\n\ndef _ping_external_watchdog_sync() -> tuple[bool, str]:\n    url = _external_watchdog_url()\n    if not url:\n        return False, "not_configured"\n\n    request = urllib.request.Request(\n        url,\n        headers={"User-Agent": "Dank-Shield/healthcheck"},\n        method="GET",\n    )\n    try:\n        with urllib.request.urlopen(request, timeout=_external_watchdog_timeout_seconds()) as response:\n            status = int(getattr(response, "status", 200) or 200)\n            if 200 <= status < 300:\n                return True, f"http_{status}"\n            return False, f"http_{status}"\n    except Exception as exc:\n        return False, f"{type(exc).__name__}"\n\n\nasync def _ping_external_watchdog() -> bool:\n    global _EXTERNAL_WATCHDOG_LAST_OK_AT\n    global _EXTERNAL_WATCHDOG_LAST_ERROR\n    global _EXTERNAL_WATCHDOG_LAST_FAILURE_LOG_AT\n    global _EXTERNAL_WATCHDOG_WAS_OK\n\n    if not external_watchdog_configured():\n        return False\n\n    try:\n        ok, detail = await asyncio.wait_for(\n            asyncio.to_thread(_ping_external_watchdog_sync),\n            timeout=_external_watchdog_timeout_seconds() + 2.0,\n        )\n    except asyncio.CancelledError:\n        raise\n    except Exception as exc:\n        ok, detail = False, type(exc).__name__\n\n    now = time.time()\n    if ok:\n        recovered = bool(_EXTERNAL_WATCHDOG_LAST_ERROR)\n        _EXTERNAL_WATCHDOG_LAST_OK_AT = now\n        _EXTERNAL_WATCHDOG_LAST_ERROR = ""\n        if recovered or not _EXTERNAL_WATCHDOG_WAS_OK:\n            _log("external watchdog ping healthy")\n        _EXTERNAL_WATCHDOG_WAS_OK = True\n        return True\n\n    _EXTERNAL_WATCHDOG_LAST_ERROR = str(detail or "ping_failed")\n    _EXTERNAL_WATCHDOG_WAS_OK = False\n    if now - _EXTERNAL_WATCHDOG_LAST_FAILURE_LOG_AT >= 300:\n        _EXTERNAL_WATCHDOG_LAST_FAILURE_LOG_AT = now\n        _log(f"external watchdog ping failed error={_EXTERNAL_WATCHDOG_LAST_ERROR}")\n    return False\n\n\n'''
if helper_marker not in process:
    raise SystemExit("process_health insertion marker missing")
process = process.replace(helper_marker, helpers + helper_marker, 1)

process = process.replace(
    '''            uptime = time.time() - _BOOT_TS\n            loop = asyncio.get_running_loop()''',
    '''            watchdog_ok = await _ping_external_watchdog() if external_watchdog_configured() else False\n            uptime = time.time() - _BOOT_TS\n            loop = asyncio.get_running_loop()''',
    1,
)
process = process.replace(
    '''            _log(f"heartbeat uptime={uptime:.1f}s tasks={task_count} {_memory_snapshot()}{_operation_queue_snapshot()}")''',
    '''            watchdog_state = "ok" if watchdog_ok else ("off" if not external_watchdog_configured() else "failed")\n            _log(f"heartbeat uptime={uptime:.1f}s tasks={task_count} {_memory_snapshot()}{_operation_queue_snapshot()} external_watchdog={watchdog_state}")''',
    1,
)

process = process.replace(
    '''            start_health_loop()\n            user = getattr(bot, "user", None)''',
    '''            start_health_loop()\n            if external_watchdog_configured():\n                await _ping_external_watchdog()\n            user = getattr(bot, "user", None)''',
    1,
)

process = process.replace(
    '''__all__ = ["install", "install_loop_exception_handler", "start_health_loop"]''',
    '''__all__ = [\n    "install",\n    "install_loop_exception_handler",\n    "start_health_loop",\n    "external_watchdog_configured",\n    "external_watchdog_status",\n]''',
    1,
)
PROCESS.write_text(process, encoding="utf-8")

status = STATUS.read_text(encoding="utf-8")
old_lines = '''    lines = [\n        _service_line("Discord gateway", gateway_ok, gateway_detail),\n        _service_line("Supabase", db_ok, db_detail),\n        _service_line("Guild config", cfg_ok, cfg_detail),\n        _service_line("Bot permissions", perm_ok, perm_detail),\n        _service_line("Slash commands", True, "registered with Discord if this message posted"),\n        _service_line("Status heartbeat", _heartbeat_enabled(), "enabled" if _heartbeat_enabled() else "disabled by env"),\n    ]\n\n    return lines, bool(gateway_ok and db_ok and cfg_ok and perm_ok)'''
new_lines = '''    watchdog_configured = False\n    watchdog_ok = False\n    watchdog_detail = "not configured — set `DANK_HEALTHCHECKS_PING_URL`"\n    try:\n        from ..startup_guards.process_health import external_watchdog_status\n\n        watchdog_configured, watchdog_ok, watchdog_detail = external_watchdog_status()\n    except Exception:\n        pass\n\n    lines = [\n        _service_line("Discord gateway", gateway_ok, gateway_detail),\n        _service_line("Supabase", db_ok, db_detail),\n        _service_line("Guild config", cfg_ok, cfg_detail),\n        _service_line("Bot permissions", perm_ok, perm_detail),\n        _service_line("Slash commands", True, "registered with Discord if this message posted"),\n        _service_line("Internal DB heartbeat", _heartbeat_enabled(), "enabled" if _heartbeat_enabled() else "disabled by env"),\n        _service_line("External uptime watchdog", watchdog_ok, watchdog_detail),\n    ]\n\n    return lines, bool(gateway_ok and db_ok and cfg_ok and perm_ok and watchdog_configured and watchdog_ok)'''
if status.count(old_lines) != 1:
    raise SystemExit(f"status reporter service block matches={status.count(old_lines)}")
status = status.replace(old_lines, new_lines, 1)
STATUS.write_text(status, encoding="utf-8")

env_doc = ENV_DOC.read_text(encoding="utf-8")
if "DANK_HEALTHCHECKS_PING_URL" not in env_doc:
    env_doc += '''\n\n## External uptime watchdog\n\nFor true bot-down alerts, configure a Healthchecks.io ping URL in the host environment:\n\n```bash\nDANK_HEALTHCHECKS_PING_URL=<your private Healthchecks.io ping URL>\nDANK_HEALTHCHECKS_TIMEOUT_SECONDS=5\n```\n\nKeep the ping URL private. Do not commit it to GitHub. Dank Shield sends an immediate success ping after Discord `on_ready`, then another ping from the process-health loop every `DANK_PROCESS_HEALTH_INTERVAL_SECONDS` (120 seconds by default). A 5-minute Healthchecks.io period with a 10-minute grace window is compatible with the default interval.\n'''
ENV_DOC.write_text(env_doc, encoding="utf-8")

TEST.write_text('''from __future__ import annotations\n\nfrom pathlib import Path\n\nPROCESS = Path("stoney_verify/startup_guards/process_health.py").read_text(encoding="utf-8")\nSTATUS = Path("stoney_verify/commands_ext/public_status_reporter.py").read_text(encoding="utf-8")\nSPAM = Path("stoney_verify/spam_guard.py").read_text(encoding="utf-8")\nCOMMANDS = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")\n\n\ndef test_external_healthchecks_ping_is_real_http_heartbeat():\n    assert "DANK_HEALTHCHECKS_PING_URL" in PROCESS\n    assert "urllib.request.urlopen" in PROCESS\n    assert "await _ping_external_watchdog()" in PROCESS\n    assert "external_watchdog=\"" not in PROCESS\n    assert "external_watchdog={watchdog_state}" in PROCESS\n    assert "hc-ping.com/" not in PROCESS\n\n\ndef test_status_report_distinguishes_internal_and_external_heartbeats():\n    assert '"Internal DB heartbeat"' in STATUS\n    assert '"External uptime watchdog"' in STATUS\n    assert '"Status heartbeat"' not in STATUS\n    assert "external_watchdog_status" in STATUS\n\n\ndef test_spamguard_missing_rows_default_on_and_bootstrap():\n    defaults = SPAM[SPAM.index("def _default_settings("):SPAM.index("def _normalize_settings(")]\n    load = SPAM[SPAM.index("async def get_spam_settings("):SPAM.index("async def save_spam_settings(")]\n    assert '"enabled": True' in defaults\n    assert "_upsert_settings_sync" in load\n    assert 'source = "db" if row_found else ("db-bootstrap" if persisted else "defaults")' in load\n\n\ndef test_child_prune_skip_log_is_once_per_process():\n    assert "_CHILD_PRUNE_SKIP_LOGGED = False" in COMMANDS\n    assert "if not _CHILD_PRUNE_SKIP_LOGGED:" in COMMANDS\n    assert "_CHILD_PRUNE_SKIP_LOGGED = True" in COMMANDS\n''', encoding="utf-8")

for path in (PROCESS, STATUS, TEST):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: external Healthchecks.io watchdog heartbeat implemented")
