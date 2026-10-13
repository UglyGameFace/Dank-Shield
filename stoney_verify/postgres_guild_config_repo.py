"""Explicit PG17 guild configuration repository, NOT production-routed.

This repository intentionally leaves stoney_verify.guild_config unchanged.
Callers must pass a real connection; there are no global clients, monkey
patches, implicit fallback writers or startup migrations. Only staged,
verified guild rows may exist in the PG table during this phase.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from .core_data_stage import canonical_json

MAX_PATCH_BYTES = 32 * 1024
MAX_ROW_BYTES = 2 * 1024 * 1024
MAX_SHADOW_ROWS = 500
MAX_SEED_BYTES = 12 * 1024 * 1024
JSON_COLUMNS = ("settings", "config", "metadata", "meta")
RESERVED = frozenset(("id", "guild_id", "created_at", "updated_at", *JSON_COLUMNS))
ALLOWED_MODES = frozenset((
    "setup_builder", "explicit_override", "force", "fill_missing",
    "runtime_discovery", "auto_discover", "auto_create"
))
OVERWRITE_MODES = frozenset(("setup_builder", "explicit_override", "force"))
PROTECTED_KEYS = frozenset((
    "unverified_role_id", "verified_role_id", "resident_role_id",
    "member_role_id", "staff_role_id", "vc_staff_role_id",
    "server_control_role_id", "control_role_id", "perm_role_id",
    "bot_manager_role_id", "verify_channel_id", "vc_verify_channel_id",
    "vc_verify_queue_channel_id", "welcome_channel_id",
    "ticket_category_id", "ticket_archive_category_id",
    "ticket_closed_category_id", "ticket_panel_channel_id",
    "support_channel_id", "start_category_id", "welcome_category_id",
    "management_category_id", "staff_tools_category_id",
    "transcripts_channel_id", "modlog_channel_id", "raidlog_channel_id",
    "raid_log_channel_id", "join_log_channel_id", "join_exit_log_channel_id",
    "force_verify_log_channel_id", "status_channel_id",
    "bot_status_channel_id", "uptime_channel_id", "health_channel_id",
    "ticket_prefix", "verify_kick_hours", "use_env_fallbacks",
    "allow_runtime_discovery"
))
_SQL_READ = "select row_payload, revision from dank_core.guild_config_state where guild_id=%s"
_SQL_LOCK = _SQL_READ + " for update"
_SQL_WRITE = (
    "update dank_core.guild_config_state "
    "set row_payload=%s, revision=revision+1, changed_at=now() "
    "where guild_id=%s and revision=%s returning revision"
)


@dataclass(frozen=True)
class ConfigWriteResult:
    applied: bool
    revision: int
    row: dict[str, Any]
    changed: tuple[str, ...]
    blocked: tuple[str, ...]
    reason: str


def _guild_id(value: Any) -> str:
    s = str(value).strip()
    if not s.isascii() or not s.isdecimal() or len(s) > 20 or s[0:1] == "0":
        raise ValueError("invalid guild ID")
    return s


def _protected(key: str) -> bool:
    return (key in PROTECTED_KEYS or
            key.endswith(("_role_id", "_channel_id", "_category_id")))


def _clean_map(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    if len(value) > 100:
        raise ValueError(f"{name} has too many keys")
    result = {}
    for k, v in value.items():
        if (not isinstance(k, str) or not 1 <= len(k) <= 120
                or k in RESERVED or k.startswith("__") or
                k.startswith("config_last_")):
            raise ValueError(f"{name} includes a reserved key")
        result[k] = v
    canonical_json(result)
    return result


def _current_value(row: Mapping[str, Any], key: str) -> Any:
    if key in row and row[key] is not None:
        return row[key]
    for col in JSON_COLUMNS:
        block = row.get(col)
        if isinstance(block, Mapping) and key in block:
            return block[key]
    return None


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _same(a: Any, b: Any) -> bool:
    return a == b or (_empty(a) and _empty(b))


def _expected_matches(row: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    # Existing Supabase CAS compares the first JSON compatibility column,
    # normally settings, rather than coalescing flat-column values.
    block = next((row[c] for c in JSON_COLUMNS
                  if isinstance(row.get(c), Mapping)), {})
    for key, value in expected.items():
        stored = block.get(key)
        if value is None:
            if stored is not None:
                return False
        elif stored != value:
            return False
    return True


def _prepare_change(
    current: Mapping[str, Any],
    patch: Mapping[str, Any],
    *,
    mode: str,
    allow_keys: frozenset[str],
    clear_keys: frozenset[str],
    timestamp: datetime,
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    if mode not in ALLOWED_MODES:
        raise ValueError("unsupported configuration write mode")
    if timestamp.tzinfo is None:
        raise ValueError("a timezone-aware timestamp is required")
    changes = _clean_map(patch, "patch")
    if any(not isinstance(x, str) for x in clear_keys):
        raise ValueError("invalid clear key")
    if len(clear_keys) > 100 or any(
        not 1 <= len(x) <= 120 or x in RESERVED or x.startswith("__")
        or x.startswith("config_last_") for x in clear_keys
    ):
        raise ValueError("unsafe clear key")
    if set(changes) & clear_keys:
        raise ValueError("cannot patch and clear the same key")
    if len(canonical_json(changes).encode("utf-8")) > MAX_PATCH_BYTES:
        raise ValueError("configuration patch exceeds 32 KiB")
    updates: dict[str, Any] = {}
    blocked = []
    for key, value in list(changes.items()) + [(key, None) for key in sorted(clear_keys)]:
        if key in changes and value is None:
            raise ValueError("use explicit clear_keys instead of null patch")
        existing = _current_value(current, key)
        if _same(existing, value):
            continue
        if _protected(key) and not _empty(existing):
            if mode not in OVERWRITE_MODES and key not in allow_keys:
                blocked.append(key)
                continue
        if mode in ("fill_missing", "runtime_discovery", "auto_discover", "auto_create") and not _empty(existing):
            if key not in allow_keys:
                blocked.append(key)
                continue
        updates[key] = value
    if not updates:
        return dict(current), (), tuple(sorted(blocked))

    # Sparse merge of only changed keys. A row lock covers the read and write,
    # so two workers changing unrelated settings cannot overwrite each other.
    raw = dict(current)
    for col in JSON_COLUMNS:
        if isinstance(current.get(col), Mapping):
            block = dict(current[col])
            for key, value in updates.items():
                if key in clear_keys:
                    block.pop(key, None)
                else:
                    block[key] = value
            raw[col] = block
    if not any(isinstance(current.get(c), Mapping) for c in JSON_COLUMNS):
        raise RuntimeError("imported guild row has no JSON compatibility column")
    for key, value in updates.items():
        if key in raw and key not in JSON_COLUMNS:
            raw[key] = value
    raw["updated_at"] = timestamp.astimezone(timezone.utc).isoformat()
    if len(canonical_json(raw).encode("utf-8")) > MAX_ROW_BYTES:
        raise ValueError("updated config row exceeds 2 MiB")
    return raw, tuple(sorted(updates)), tuple(sorted(blocked))


class GuildConfigPgRepository:
    """One-connection, explicit-operation PG17 repository. No global routing."""

    def __init__(self, connection: Any):
        self.connection = connection

    def read(self, guild_id: Any) -> ConfigWriteResult | None:
        gid = _guild_id(guild_id)
        with self.connection.cursor() as cursor:
            cursor.execute(_SQL_READ, (gid,))
            found = cursor.fetchone()
        if found is None:
            return None
        row, revision = found
        if not isinstance(row, Mapping) or str(row.get("guild_id")) != gid:
            raise RuntimeError("PG guild config integrity mismatch")
        return ConfigWriteResult(False, int(revision), dict(row), (), (), "read")

    def patch(
        self,
        guild_id: Any,
        changes: Mapping[str, Any],
        *,
        mode: str = "explicit_override",
        clear_keys: Sequence[str] = (),
        allow_keys: Sequence[str] = (),
        expected: Mapping[str, Any] | None = None,
        expected_revision: int | None = None,
        timestamp: datetime | None = None,
    ) -> ConfigWriteResult:
        gid = _guild_id(guild_id)
        if expected_revision is not None and expected_revision < 0:
            raise ValueError("revision must be nonnegative")
        expected_map = _clean_map(expected, "expected") if expected is not None else None
        clearing = frozenset(clear_keys)
        allowing = frozenset(allow_keys)
        if len(allowing) > 100 or any(not isinstance(k, str) or not k or k.startswith("__")
                                      for k in allowing):
            raise ValueError("invalid explicit allowlist")
        stamp = timestamp or datetime.now(timezone.utc)
        # Validate mode, keys and size before acquiring any lock.
        _prepare_change(
            {"settings": {}, "guild_id": gid}, changes, mode=mode,
            allow_keys=allowing, clear_keys=clearing, timestamp=stamp,
        )
        from psycopg.types.json import Jsonb
        with self.connection.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(_SQL_LOCK, (gid,))
                found = cursor.fetchone()
                if found is None:
                    raise LookupError("guild not seeded; refuse silent creation")
                current, rev = found
                if not isinstance(current, Mapping) or str(current.get("guild_id")) != gid:
                    raise RuntimeError("stored guild config integrity mismatch")
                row = dict(current)
                revision = int(rev)
                if (expected_revision is not None and revision != expected_revision):
                    return ConfigWriteResult(False, revision, row, (), (), "stale_revision")
                if expected_map is not None and not _expected_matches(row, expected_map):
                    return ConfigWriteResult(False, revision, row, (), (), "compare_mismatch")
                updated, changed, blocked = _prepare_change(
                    row, changes, mode=mode, allow_keys=allowing,
                    clear_keys=clearing, timestamp=stamp,
                )
                if not changed:
                    return ConfigWriteResult(False, revision, row, (), blocked, "no_change")
                cursor.execute(_SQL_WRITE, (Jsonb(updated), gid, revision))
                written = cursor.fetchone()
                if not written or int(written[0]) != revision + 1:
                    raise RuntimeError("atomic PG revision update failed")
                return ConfigWriteResult(
                    True, int(written[0]), updated, changed, blocked, "updated"
                )


def seed_from_verified_shadow(
    connection: Any, *, expected_database: str, disk_budget_mb: int
) -> dict[str, Any]:
    """Explicit PG17 seed; never edits shadow/source/voice-bot state.

    Requires earlier verified shadow import, a signed-off source backup,
    and real private destination disk allowance. No user-facing feature calls
    this function. Existing modified PG state is never reset.
    """
    if not expected_database or not expected_database.isidentifier():
        raise ValueError("expected database name is required")
    if disk_budget_mb <= 0:
        raise ValueError("verified disk budget is required")
    from psycopg.types.json import Jsonb
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("select current_database(),pg_database_size(current_database())")
            name, size = cursor.fetchone()
            if name != expected_database:
                raise RuntimeError("unexpected PG destination")
            cursor.execute(
                "select guild_id, raw_row, source_sha256 from "
                "dank_core.guild_config_shadow order by guild_id limit %s",
                (MAX_SHADOW_ROWS + 1,),
            )
            records = cursor.fetchall()
            if not records or len(records) > MAX_SHADOW_ROWS:
                raise RuntimeError("shadow records missing or exceed safe first-slice bound")
            budget = disk_budget_mb * 1024 * 1024 * .70
            projected = 0
            for guild_id, row, digest in records:
                gid = _guild_id(guild_id)
                if not isinstance(row, Mapping) or _guild_id(row.get("guild_id")) != gid:
                    raise RuntimeError("shadow guild identity mismatch")
                raw = canonical_json(row).encode("utf-8")
                if sha256(raw).hexdigest() != digest:
                    raise RuntimeError("shadow source checksum mismatch")
                projected += len(raw) + 256
                if projected > MAX_SEED_BYTES or len(raw) > MAX_ROW_BYTES:
                    raise RuntimeError("shadow seed exceeds safe data budget")
            if int(size) + 4 * projected >= budget:
                raise RuntimeError("insufficient verified PG disk headroom")
            for gid, row, _digest in records:
                cursor.execute(
                    "insert into dank_core.guild_config_state "
                    "(guild_id, row_payload) values (%s,%s) "
                    "on conflict (guild_id) do nothing",
                    (gid, Jsonb(dict(row))),
                )
                cursor.execute(_SQL_LOCK, (gid,))
                found = cursor.fetchone()
                if (not found or int(found[1]) != 0
                        or canonical_json(found[0]) != canonical_json(row)):
                    raise RuntimeError("PG state is not an unchanged verified seed")
            cursor.execute(
                "select guild_id from dank_core.guild_config_state order by guild_id"
            )
            actual = [row[0] for row in cursor.fetchall()]
            if actual != [row[0] for row in records]:
                raise RuntimeError("PG state contains unexpected guilds")
    return {"seeded_guilds": len(records), "status": "offline_state_only"}
