from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURABLE = ROOT / "stoney_verify" / "durable_invite_stats.py"
TESTS = ROOT / "tests" / "test_durable_invite_stats.py"
ACTIVE = ROOT / "ACTIVE_TASK.md"

text = DURABLE.read_text(encoding="utf-8")
start = text.index("def _fetch_config_row_sync")
end = text.index("\ndef _write_event_sync", start)
replacement = r'''_CONFIG_JSON_BUCKETS = ("settings", "config", "metadata", "meta")
_CONFIG_JSON_PRECEDENCE = ("meta", "metadata", "config", "settings")


def _fetch_config_row_sync(sb: Any, table_name: str, guild_id: int) -> Optional[dict[str, Any]]:
    """Fetch every config bucket so compatibility precedence is visible."""

    response = (
        sb.table(table_name)
        .select("*")
        .eq("guild_id", str(guild_id))
        .limit(1)
        .execute()
    )
    rows = _rows(response)
    return rows[0] if rows else None


def _merged_config_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Mirror guild_config's JSON-bucket precedence for reliable readback."""

    merged: dict[str, Any] = {}
    raw = _mapping(row)
    for bucket_name in _CONFIG_JSON_BUCKETS:
        bucket = _mapping(raw.get(bucket_name))
        if bucket:
            merged.update(bucket)
    return merged


def _preferred_config_bucket(row: Mapping[str, Any]) -> str:
    """Write to the bucket that currently owns the counter/event ledger.

    guild_config merges settings -> config -> metadata -> meta. Writing a new
    value only to settings can therefore be hidden by an older value in a
    higher-precedence compatibility bucket. Prefer the highest-precedence bucket
    already carrying either stats key, then the highest-precedence non-empty
    bucket, and finally settings for a clean modern row.
    """

    raw = _mapping(row)
    for bucket_name in _CONFIG_JSON_PRECEDENCE:
        bucket = _mapping(raw.get(bucket_name))
        if COUNTS_KEY in bucket or FALLBACK_EVENTS_KEY in bucket:
            return bucket_name
    for bucket_name in _CONFIG_JSON_PRECEDENCE:
        if _mapping(raw.get(bucket_name)):
            return bucket_name
    for bucket_name in _CONFIG_JSON_BUCKETS:
        if bucket_name in raw:
            return bucket_name
    return "settings"


def _config_column_missing(error: BaseException) -> bool:
    text = repr(error).lower()
    return (
        "pgrst204" in text
        or "undefinedcolumn" in text
        or ("column" in text and "does not exist" in text)
        or ("could not find" in text and "column" in text)
    )


def _config_result_from_row(
    event: PendingInviteEvent,
    row: Mapping[str, Any],
    *,
    applied: bool,
) -> Optional[InviteStatWriteResult]:
    merged = _merged_config_payload(row)
    hashes = _fallback_event_hashes(merged)
    if event.event_hash not in hashes:
        return None
    counts = _normalize_counts(merged.get(COUNTS_KEY))
    return InviteStatWriteResult(
        event_hash=event.event_hash,
        blocked_count=event.blocked_count,
        invites_blocked=counts["invites_blocked"],
        applied=bool(applied),
        persisted=True,
        queued=False,
        backend="guild_config_cas",
    )


def _new_config_payload(event: PendingInviteEvent) -> dict[str, Any]:
    return {
        COUNTS_KEY: {
            "spam_blocked": 0,
            "invites_blocked": int(event.seed_count) + int(event.blocked_count),
            "timeouts_issued": 0,
            "quarantines": 0,
        },
        FALLBACK_EVENTS_KEY: [event.event_hash],
    }


def _insert_new_config_event_sync(
    sb: Any,
    table_name: str,
    event: PendingInviteEvent,
) -> Optional[InviteStatWriteResult]:
    settings = _new_config_payload(event)
    last_error: Optional[BaseException] = None
    for bucket_name in _CONFIG_JSON_BUCKETS:
        payload = {
            "guild_id": str(event.guild_id),
            bucket_name: settings,
        }
        try:
            try:
                response = (
                    sb.table(table_name)
                    .upsert(payload, on_conflict="guild_id")
                    .select("*")
                    .execute()
                )
            except TypeError:
                response = sb.table(table_name).upsert(payload).select("*").execute()
            rows = _rows(response)
            verified = rows[0] if rows else _fetch_config_row_sync(
                sb,
                table_name,
                event.guild_id,
            )
            if verified:
                result = _config_result_from_row(event, verified, applied=True)
                if result is not None:
                    return result
        except Exception as exc:
            last_error = exc
            if _config_column_missing(exc):
                continue
            raise
    if last_error is not None and not _config_column_missing(last_error):
        raise last_error
    return None


def _record_with_config_cas_sync(event: PendingInviteEvent, max_attempts: int = 24) -> InviteStatWriteResult:
    """Migration-safe fallback that respects legacy config-bucket precedence."""

    sb = get_supabase()
    if sb is None:
        raise RuntimeError("Supabase client unavailable")

    last_error: Optional[BaseException] = None
    for table_name in GUILD_CONFIG_TABLE_FALLBACKS:
        for attempt in range(1, max_attempts + 1):
            try:
                row = _fetch_config_row_sync(sb, table_name, event.guild_id)
                if row is None:
                    inserted = _insert_new_config_event_sync(sb, table_name, event)
                    if inserted is not None:
                        return inserted
                    time.sleep(min(0.04 * attempt, 0.5))
                    continue

                merged = _merged_config_payload(row)
                hashes = _fallback_event_hashes(merged)
                counts = _normalize_counts(merged.get(COUNTS_KEY))
                counts["invites_blocked"] = max(
                    counts["invites_blocked"],
                    int(event.seed_count),
                )
                if event.event_hash in hashes:
                    return InviteStatWriteResult(
                        event_hash=event.event_hash,
                        blocked_count=event.blocked_count,
                        invites_blocked=counts["invites_blocked"],
                        applied=False,
                        persisted=True,
                        queued=False,
                        backend="guild_config_cas",
                    )

                counts["invites_blocked"] += int(event.blocked_count)
                hashes.append(event.event_hash)
                merged[COUNTS_KEY] = counts
                merged[FALLBACK_EVENTS_KEY] = hashes[-_MAX_FALLBACK_EVENT_HASHES:]
                bucket_name = _preferred_config_bucket(row)

                query = (
                    sb.table(table_name)
                    .update({bucket_name: merged})
                    .eq("guild_id", str(event.guild_id))
                )
                updated_at = row.get("updated_at")
                if updated_at is not None:
                    query = query.eq("updated_at", updated_at)
                response = query.select("*").execute()
                rows = _rows(response)
                verified = rows[0] if rows else _fetch_config_row_sync(
                    sb,
                    table_name,
                    event.guild_id,
                )
                if verified:
                    result = _config_result_from_row(event, verified, applied=True)
                    if result is not None:
                        return result
                time.sleep(min((0.04 * attempt) + random.uniform(0.01, 0.08), 0.75))
            except Exception as exc:
                last_error = exc
                if _rpc_or_table_missing(exc):
                    break
                if _is_retryable_db_error(exc):
                    time.sleep(min(0.15 * attempt, 1.5))
                    continue
                raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("No compatible guild-config table accepted invite stats CAS")
'''
DURABLE.write_text(text[:start] + replacement + text[end:], encoding="utf-8")

with TESTS.open("a", encoding="utf-8") as handle:
    handle.write(r'''


def test_fallback_uses_bucket_that_owns_visible_invite_count(monkeypatch) -> None:
    state = {
        "row": {
            "guild_id": "123",
            "settings": {
                durable_invite_stats.COUNTS_KEY: {
                    "spam_blocked": 1,
                    "invites_blocked": 2,
                    "timeouts_issued": 0,
                    "quarantines": 0,
                }
            },
            "config": {
                durable_invite_stats.COUNTS_KEY: {
                    "spam_blocked": 1,
                    "invites_blocked": 5,
                    "timeouts_issued": 0,
                    "quarantines": 0,
                }
            },
            "metadata": {},
            "meta": {},
            "updated_at": "2026-08-02T23:00:00+00:00",
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
    monkeypatch.setattr(
        durable_invite_stats,
        "GUILD_CONFIG_TABLE_FALLBACKS",
        ("guild_configs",),
    )
    monkeypatch.setattr(
        durable_invite_stats,
        "_fetch_config_row_sync",
        lambda _sb, _table, _guild_id: dict(state["row"]),
    )

    event = durable_invite_stats.PendingInviteEvent(
        event_hash="e" * 64,
        guild_id=123,
        blocked_count=2,
        seed_count=5,
        source="live-test",
    )
    result = durable_invite_stats._record_with_config_cas_sync(event, max_attempts=1)

    assert result.applied is True
    assert result.invites_blocked == 7
    assert result.backend == "guild_config_cas"
    assert len(updates) == 1
    assert set(updates[0]) == {"config"}
    assert updates[0]["config"][durable_invite_stats.COUNTS_KEY]["invites_blocked"] == 7
    assert event.event_hash in updates[0]["config"][durable_invite_stats.FALLBACK_EVENTS_KEY]


def test_config_bucket_precedence_matches_visible_guild_config_merge() -> None:
    row = {
        "settings": {durable_invite_stats.COUNTS_KEY: {"invites_blocked": 2}},
        "config": {durable_invite_stats.COUNTS_KEY: {"invites_blocked": 5}},
        "metadata": {},
        "meta": {},
    }
    merged = durable_invite_stats._merged_config_payload(row)
    assert merged[durable_invite_stats.COUNTS_KEY]["invites_blocked"] == 5
    assert durable_invite_stats._preferred_config_bucket(row) == "config"
''')

active = ACTIVE.read_text(encoding="utf-8")
active = active.replace(
    "**Status:** MERGED — PRODUCTION REBUILD/LIVE VERIFICATION PENDING\n**Merged pull request:** `#166`\n**Merge commit:** `2e89fd84b6c9c8e503c06782e4592a723a4c7c49`",
    "**Status:** ACTIVE — LIVE VERIFICATION FAILED; CONFIG PRECEDENCE REPAIR IN PROGRESS\n**Branch:** `fix/dank-stats-live-recovery`\n**Previous merged pull request:** `#166`\n**Previous merge commit:** `2e89fd84b6c9c8e503c06782e4592a723a4c7c49`",
)
active = active.replace(
    "## Remaining production verification\n\n- [ ] Rebuild/restart Dank Shield on Discloud from current `main`.\n- [ ] Confirm startup includes durable invite stats activation and no schema/bootstrap failure.\n- [ ] Send one message containing two unique external Discord invite codes.\n- [ ] Confirm the message is deleted once and the visible `Invites Blocked` channel increases by exactly `2`.\n- [ ] Confirm replay/edit/fallback handling does not increment the same message again.\n",
    "## Live failure under repair\n\n- [x] User rebuilt and tested the merged implementation.\n- [x] Live verification failed: the visible counter did not update as required.\n- [x] Root cause found in the migration-safe fallback: it always wrote `settings`, while runtime compatibility precedence can let stale `config`, `metadata`, or `meta` values override that successful write.\n- [ ] Update the JSON bucket that actually owns the visible counter.\n- [ ] Verify merged readback before claiming persistence.\n- [ ] Run focused and full regression suites.\n- [ ] Merge the repair and repeat the two-invite live test.\n",
)
ACTIVE.write_text(active, encoding="utf-8")

Path(__file__).unlink(missing_ok=True)
