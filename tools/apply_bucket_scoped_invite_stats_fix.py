from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "stoney_verify" / "durable_invite_stats.py"
TESTS = ROOT / "tests" / "test_durable_invite_stats.py"
ACTIVE = ROOT / "ACTIVE_TASK.md"

text = SOURCE.read_text(encoding="utf-8")
old_preference = '''    for bucket_name in _CONFIG_JSON_PRECEDENCE:\n        if _mapping(raw.get(bucket_name)):\n            return bucket_name\n    for bucket_name in _CONFIG_JSON_BUCKETS:\n        if bucket_name in raw:\n            return bucket_name\n    return "settings"\n'''
new_preference = '''    # When no bucket owns either stats key, start in the canonical modern\n    # settings bucket rather than promoting unrelated metadata/config values.\n    if "settings" in raw:\n        return "settings"\n    for bucket_name in _CONFIG_JSON_BUCKETS:\n        if bucket_name in raw:\n            return bucket_name\n    return "settings"\n'''
if text.count(old_preference) != 1:
    raise RuntimeError(f"preferred bucket replacement expected once, found {text.count(old_preference)}")
text = text.replace(old_preference, new_preference, 1)

old_update = '''                counts["invites_blocked"] += int(event.blocked_count)\n                hashes.append(event.event_hash)\n                merged[COUNTS_KEY] = counts\n                merged[FALLBACK_EVENTS_KEY] = hashes[-_MAX_FALLBACK_EVENT_HASHES:]\n                bucket_name = _preferred_config_bucket(row)\n\n                query = (\n                    sb.table(table_name)\n                    .update({bucket_name: merged})\n                    .eq("guild_id", str(event.guild_id))\n                )\n'''
new_update = '''                counts["invites_blocked"] += int(event.blocked_count)\n                hashes.append(event.event_hash)\n                bucket_name = _preferred_config_bucket(row)\n                target_bucket = _mapping(row.get(bucket_name))\n                target_bucket[COUNTS_KEY] = counts\n                target_bucket[FALLBACK_EVENTS_KEY] = hashes[-_MAX_FALLBACK_EVENT_HASHES:]\n\n                query = (\n                    sb.table(table_name)\n                    .update({bucket_name: target_bucket})\n                    .eq("guild_id", str(event.guild_id))\n                )\n'''
if text.count(old_update) != 1:
    raise RuntimeError(f"bucket-scoped update replacement expected once, found {text.count(old_update)}")
text = text.replace(old_update, new_update, 1)
SOURCE.write_text(text, encoding="utf-8")

with TESTS.open("a", encoding="utf-8") as handle:
    handle.write(r'''


def test_fallback_updates_only_stats_keys_in_authoritative_bucket(monkeypatch) -> None:
    state = {
        "row": {
            "guild_id": "321",
            "settings": {
                "settings_only": "must-not-move",
                durable_invite_stats.COUNTS_KEY: {"invites_blocked": 1},
            },
            "config": {
                "config_only": "must-stay",
                durable_invite_stats.COUNTS_KEY: {"invites_blocked": 4},
            },
            "metadata": {"metadata_only": "must-not-move"},
            "meta": {"meta_only": "must-not-move"},
            "updated_at": "2026-08-03T02:00:00+00:00",
        }
    }
    updates = []

    class FakeQuery:
        def __init__(self, payload):
            self.payload = payload

        def eq(self, *_args):
            return self

        def select(self, columns):
            assert columns == "*"
            return self

        def execute(self):
            updates.append(self.payload)
            state["row"].update(self.payload)
            return SimpleNamespace(data=[dict(state["row"])])

    class FakeTable:
        def update(self, payload):
            return FakeQuery(dict(payload))

    class FakeSupabase:
        def table(self, name):
            assert name == "guild_configs"
            return FakeTable()

    monkeypatch.setattr(durable_invite_stats, "get_supabase", lambda: FakeSupabase())
    monkeypatch.setattr(durable_invite_stats, "GUILD_CONFIG_TABLE_FALLBACKS", ("guild_configs",))
    monkeypatch.setattr(
        durable_invite_stats,
        "_fetch_config_row_sync",
        lambda _sb, _table, _guild_id: dict(state["row"]),
    )

    event = durable_invite_stats.PendingInviteEvent(
        event_hash="f" * 64,
        guild_id=321,
        blocked_count=2,
        seed_count=4,
        source="bucket-scope-test",
    )
    result = durable_invite_stats._record_with_config_cas_sync(event, max_attempts=1)

    assert result.invites_blocked == 6
    assert len(updates) == 1
    assert set(updates[0]) == {"config"}
    target = updates[0]["config"]
    assert target["config_only"] == "must-stay"
    assert target[durable_invite_stats.COUNTS_KEY]["invites_blocked"] == 6
    assert event.event_hash in target[durable_invite_stats.FALLBACK_EVENTS_KEY]
    assert "settings_only" not in target
    assert "metadata_only" not in target
    assert "meta_only" not in target


def test_new_stats_without_existing_owner_prefer_settings_bucket() -> None:
    row = {
        "settings": {},
        "config": {"config_only": True},
        "metadata": {"metadata_only": True},
        "meta": {"meta_only": True},
    }
    assert durable_invite_stats._preferred_config_bucket(row) == "settings"
''')

active = ACTIVE.read_text(encoding="utf-8")
active = active.replace(
    "- [x] Add a regression reproducing stale `settings` versus authoritative `config`.\n",
    "- [x] Add a regression reproducing stale `settings` versus authoritative `config`.\n"
    "- [x] Restrict fallback writes to the selected bucket's stats/event keys instead of copying the fully merged config.\n"
    "- [x] Preserve unrelated values in every JSON compatibility bucket.\n",
)
active = active.replace(
    "- [x] Focused repair suite passed: `18 passed`.\n",
    "- [x] Initial focused repair suite passed: `18 passed`.\n"
    "- [ ] Bucket-scoped focused repair suite passes.\n",
)
ACTIVE.write_text(active, encoding="utf-8")

Path(__file__).unlink(missing_ok=True)
