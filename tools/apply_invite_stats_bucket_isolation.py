from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURABLE = ROOT / "stoney_verify" / "durable_invite_stats.py"
TESTS = ROOT / "tests" / "test_durable_invite_stats.py"
ACTIVE = ROOT / "ACTIVE_TASK.md"

source = DURABLE.read_text(encoding="utf-8")
old_write = '''                counts["invites_blocked"] += int(event.blocked_count)
                hashes.append(event.event_hash)
                merged[COUNTS_KEY] = counts
                merged[FALLBACK_EVENTS_KEY] = hashes[-_MAX_FALLBACK_EVENT_HASHES:]
                bucket_name = _preferred_config_bucket(row)

                query = (
                    sb.table(table_name)
                    .update({bucket_name: merged})
'''
new_write = '''                counts["invites_blocked"] += int(event.blocked_count)
                hashes.append(event.event_hash)
                bucket_name = _preferred_config_bucket(row)
                target_bucket = _mapping(row.get(bucket_name))
                target_bucket[COUNTS_KEY] = counts
                target_bucket[FALLBACK_EVENTS_KEY] = hashes[-_MAX_FALLBACK_EVENT_HASHES:]

                query = (
                    sb.table(table_name)
                    .update({bucket_name: target_bucket})
'''
if source.count(old_write) != 1:
    raise SystemExit(
        "Expected exactly one merged-payload fallback write block; "
        f"found {source.count(old_write)}"
    )
source = source.replace(old_write, new_write, 1)
DURABLE.write_text(source, encoding="utf-8")

tests = TESTS.read_text(encoding="utf-8")
old_settings = '''            "settings": {
                durable_invite_stats.COUNTS_KEY: {
                    "spam_blocked": 1,
                    "invites_blocked": 2,
                    "timeouts_issued": 0,
                    "quarantines": 0,
                }
            },
            "config": {
                durable_invite_stats.COUNTS_KEY: {
'''
new_settings = '''            "settings": {
                "settings_only": "must-stay-in-settings",
                durable_invite_stats.COUNTS_KEY: {
                    "spam_blocked": 1,
                    "invites_blocked": 2,
                    "timeouts_issued": 0,
                    "quarantines": 0,
                },
            },
            "config": {
                "config_only": "must-stay-in-config",
                durable_invite_stats.COUNTS_KEY: {
'''
if tests.count(old_settings) != 1:
    raise SystemExit(
        "Expected exactly one visible-count mixed-bucket fixture; "
        f"found {tests.count(old_settings)}"
    )
tests = tests.replace(old_settings, new_settings, 1)

old_assertions = '''    assert set(updates[0]) == {"config"}
    assert updates[0]["config"][durable_invite_stats.COUNTS_KEY]["invites_blocked"] == 7
    assert event.event_hash in updates[0]["config"][durable_invite_stats.FALLBACK_EVENTS_KEY]
'''
new_assertions = '''    assert set(updates[0]) == {"config"}
    saved_config = updates[0]["config"]
    assert saved_config["config_only"] == "must-stay-in-config"
    assert "settings_only" not in saved_config
    assert state["row"]["settings"]["settings_only"] == "must-stay-in-settings"
    assert saved_config[durable_invite_stats.COUNTS_KEY]["invites_blocked"] == 7
    assert event.event_hash in saved_config[durable_invite_stats.FALLBACK_EVENTS_KEY]
'''
if tests.count(old_assertions) != 1:
    raise SystemExit(
        "Expected exactly one mixed-bucket assertion block; "
        f"found {tests.count(old_assertions)}"
    )
tests = tests.replace(old_assertions, new_assertions, 1)
TESTS.write_text(tests, encoding="utf-8")

active = ACTIVE.read_text(encoding="utf-8")
active = active.replace(
    "**Status:** ACTIVE — PR #167 REPAIR IMPLEMENTED; FINAL CI RUNNING",
    "**Status:** ACTIVE — REVIEW FOUND BUCKET-CLOBBER RISK; ISOLATION REPAIR IN PROGRESS",
)
active = active.replace(
    "- [ ] Run normal repository CI on the owner-authored PR #167 head.\n- [ ] Run the full regression suite and conflict/cleanup inspection.",
    "- [x] Normal repository CI passed on the first repair head: `925 passed, 9 warnings`.\n"
    "- [x] Review caught a correctness risk before merge: the fallback wrote the fully merged config into one compatibility bucket, which could promote unrelated lower-precedence keys.\n"
    "- [x] Preserve the selected bucket's own unrelated keys while updating only the stats count and event ledger.\n"
    "- [x] Add a regression proving a settings-only key is not copied into config.\n"
    "- [ ] Run focused and full regression suites on the isolation repair.\n"
    "- [ ] Confirm all review threads are resolved and the branch remains conflict-free.",
)
ACTIVE.write_text(active, encoding="utf-8")
