from pathlib import Path

SERVICE = Path('stoney_verify/tickets_new/managed_category_service.py')
SETUP = Path('stoney_verify/startup_guards/ticket_category_setup_guard.py')
TESTS = Path('tests/test_ticket_category_setup_selection.py')

source = SERVICE.read_text(encoding='utf-8')

anchor = '''_RECONCILE_LOCK = threading.Lock()
_RECONCILE_NOT_BEFORE: Dict[int, float] = {}
'''
replacement = '''_RECONCILE_LOCK = threading.Lock()
_RECONCILE_NOT_BEFORE: Dict[int, float] = {}
_HISTORY_RECOVERY_DEBOUNCE_SECONDS = 300.0
_HISTORY_RECOVERY_RETRY_SECONDS = 30.0
_HISTORY_RECOVERY_NOT_BEFORE: Dict[int, float] = {}
_DESTRUCTIVE_REVIEW_REASON_MARKER = "previous setup enabled duplicate or excessive"
'''
if anchor not in source:
    raise SystemExit('history constants anchor missing')
source = source.replace(anchor, replacement, 1)

anchor = '''def _state_from_rows(
    cfg: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> CategorySetupState:
'''
insert = r'''def _claim_history_recovery_window(guild_id: int, *, now: float | None = None) -> bool:
    current = time.monotonic() if now is None else float(now)
    key = int(guild_id)
    with _RECONCILE_LOCK:
        if _HISTORY_RECOVERY_NOT_BEFORE.get(key, 0.0) > current:
            return False
        _HISTORY_RECOVERY_NOT_BEFORE[key] = current + _HISTORY_RECOVERY_DEBOUNCE_SECONDS
        return True


def _schedule_history_recovery_retry(guild_id: int, *, now: float | None = None) -> None:
    current = time.monotonic() if now is None else float(now)
    with _RECONCILE_LOCK:
        _HISTORY_RECOVERY_NOT_BEFORE[int(guild_id)] = current + _HISTORY_RECOVERY_RETRY_SECONDS


def _runtime_history_recovery_eligible(
    cfg: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> bool:
    """Limit REST history recovery to the exact old starter-reset footprint.

    Custom-only setups deliberately store an empty managed selection.  Never use
    old history to widen those.  The runtime fallback is intentionally narrower
    than the SQL migration: it only repairs a required-review guild with no
    saved keys, no enabled custom rows, and exactly the old starter trio live.
    """
    if not _config_required(cfg) or _configured_selected_keys(cfg):
        return False
    if _enabled_custom_rows(rows):
        return False
    live_managed = {
        canonical_category_key(row)
        for row in rows
        if _managed(row)
        and _row_enabled(row)
        and canonical_category_key(row) in _CATALOG_BY_KEY
    }
    return live_managed == set(SAFE_STARTER_KEYS)


def _historical_managed_keys(snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    raw_rows = snapshot.get("rows")
    if not isinstance(raw_rows, list):
        return ()
    selected: List[str] = []
    for raw in raw_rows:
        if not isinstance(raw, Mapping) or not _row_enabled(raw):
            continue
        key = canonical_category_key(raw)
        if key in _CATALOG_BY_KEY and key not in selected:
            selected.append(key)
    return tuple(selected[:25])


def _recover_erased_selection_keys_sync(guild_id: int) -> tuple[str, ...]:
    """Recover an old destructive-reset selection using Supabase REST history.

    This is the production fallback for deployments that have the version-history
    table but no direct Postgres DSN capable of applying the v4 SQL migration.
    It mirrors the migration's strict transaction-boundary rule: locate the
    destructive guild-config snapshot, then use the newest ticket-category
    snapshot whose timestamp is strictly earlier than that reset transaction.
    """
    from .. import config_history as history

    sb = _supabase()
    gid = str(int(guild_id))
    candidates: List[Dict[str, Any]] = []
    config_tables = tuple(dict.fromkeys((_config_table_name(), "guild_configs", "guild_config")))

    for table_name in config_tables:
        try:
            response = (
                sb.table(history.CONFIG_HISTORY_TABLE)
                .select("version_id,guild_id,config_table,snapshot,created_at")
                .eq("guild_id", gid)
                .eq("config_table", table_name)
                .order("created_at", desc=True)
                .order("version_id", desc=True)
                .limit(history.CONFIG_HISTORY_RETENTION)
                .execute()
            )
        except Exception:
            continue
        for raw in getattr(response, "data", None) or []:
            if not isinstance(raw, Mapping):
                continue
            snapshot = raw.get("snapshot")
            if not isinstance(snapshot, Mapping):
                continue
            if not _safe_bool(_row_value(snapshot, "ticket_category_setup_required", False), False):
                continue
            reason = str(
                _row_value(snapshot, "ticket_category_setup_required_reason", "") or ""
            ).strip().lower()
            if _DESTRUCTIVE_REVIEW_REASON_MARKER not in reason:
                continue
            row = dict(raw)
            if str(row.get("created_at") or "").strip():
                candidates.append(row)

    if not candidates:
        return ()
    reset = max(
        candidates,
        key=lambda row: (
            str(row.get("created_at") or ""),
            _safe_int(row.get("version_id"), 0),
        ),
    )
    reset_at = str(reset.get("created_at") or "").strip()
    if not reset_at:
        return ()

    try:
        response = (
            sb.table(history.CONFIG_HISTORY_TABLE)
            .select("version_id,guild_id,config_table,snapshot,created_at")
            .eq("guild_id", gid)
            .eq("config_table", history.TICKET_CATEGORIES_TABLE)
            .lt("created_at", reset_at)
            .order("created_at", desc=True)
            .order("version_id", desc=True)
            .limit(1)
            .execute()
        )
    except Exception:
        return ()

    versions = getattr(response, "data", None) or []
    if not versions or not isinstance(versions[0], Mapping):
        return ()
    snapshot = versions[0].get("snapshot")
    if not isinstance(snapshot, Mapping):
        return ()
    return _historical_managed_keys(snapshot)


def _persist_runtime_recovered_selection_sync(
    guild_id: int,
    keys: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    selected = tuple(
        key for key in dict.fromkeys(str(value) for value in keys)
        if key in _CATALOG_BY_KEY
    )
    if not selected:
        return

    from ..commands_ext.public_setup_config_writer import upsert_guild_config_sync

    upsert_guild_config_sync(
        int(guild_id),
        {
            "ticket_category_setup_selected_keys": list(selected),
            "__config_write_mode": "force",
            "__config_write_source": "ticket category REST history recovery",
            "__config_write_reason": "Recovered pre-reset ticket category selection from same-guild version history.",
        },
    )

    # The saved keys are sufficient for member-facing projection.  Realign live
    # row flags best-effort as well so setup, database state, and the picker tell
    # the same story even before the SQL migration is installed.
    sb = _supabase()
    if "support" in selected:
        default_key = "support"
    else:
        default_key = min(
            selected,
            key=lambda key: (
                _safe_int(_CATALOG_BY_KEY[key].get("sort_order"), 999),
                key,
            ),
        )
    for row in rows:
        if not _managed(row):
            continue
        key = canonical_category_key(row)
        row_id = str(row.get("id") or "").strip()
        if not row_id or key not in _CATALOG_BY_KEY:
            continue
        enabled = key in selected
        try:
            sb.table("ticket_categories").update(
                {
                    "is_enabled": enabled,
                    "is_default": enabled and key == default_key,
                }
            ).eq("id", row_id).execute()
        except Exception:
            # Projection from the durably saved keys still keeps the menu safe;
            # the normal reconcile path can repair row flags later.
            continue

    try:
        print(
            "🗂️ ticket_category_history_recovery "
            f"guild={int(guild_id)} source=rest_version_history "
            f"recovered={','.join(selected)} setup_required=true"
        )
    except Exception:
        pass


'''
if anchor not in source:
    raise SystemExit('state insertion anchor missing')
source = source.replace(anchor, insert + anchor, 1)

anchor = '''    required = _config_required(cfg)
    if not required:
        detected_reason, reset = _shape_problem(rows, cfg)
        if detected_reason:
            require_category_setup_sync(
                guild_id,
                detected_reason,
                reset_to_starter=reset,
            )
            cfg = _fetch_config_sync(guild_id)
            rows = _fetch_rows_sync(guild_id)

    return _state_from_rows(cfg, rows)
'''
replacement = '''    required = _config_required(cfg)
    if not required:
        detected_reason, reset = _shape_problem(rows, cfg)
        if detected_reason:
            require_category_setup_sync(
                guild_id,
                detected_reason,
                reset_to_starter=reset,
            )
            cfg = _fetch_config_sync(guild_id)
            rows = _fetch_rows_sync(guild_id)
            required = _config_required(cfg)

    # Older review migrations could erase the saved managed keys entirely.
    # Recover that exact starter-reset footprint from same-guild REST history so
    # production does not depend on a direct Postgres DSN to regain its menu.
    if (
        required
        and _runtime_history_recovery_eligible(cfg, rows)
        and _claim_history_recovery_window(guild_id)
    ):
        try:
            recovered = _recover_erased_selection_keys_sync(guild_id)
            if recovered:
                _persist_runtime_recovered_selection_sync(guild_id, recovered, rows)
                cfg = _fetch_config_sync(guild_id)
                rows = _fetch_rows_sync(guild_id)
        except Exception:
            _schedule_history_recovery_retry(guild_id)

    return _state_from_rows(cfg, rows)
'''
if anchor not in source:
    raise SystemExit('ensure recovery anchor missing')
source = source.replace(anchor, replacement, 1)
SERVICE.write_text(source, encoding='utf-8')

setup = SETUP.read_text(encoding='utf-8')
old = '''        if state.required:
            warning = "This server is using a temporary safe ticket menu until an admin confirms its ticket choices."
'''
new = '''        if state.required:
            warning = "This server's ticket choices are awaiting owner confirmation; the last trustworthy selection remains active."
'''
if old not in setup:
    raise SystemExit('setup warning anchor missing')
setup = setup.replace(old, new, 1)
SETUP.write_text(setup, encoding='utf-8')

tests = TESTS.read_text(encoding='utf-8')
if 'test_runtime_history_recovery_only_targets_old_starter_reset' in tests:
    raise SystemExit('runtime history tests already present')
append = r'''


def test_runtime_history_recovery_only_targets_old_starter_reset() -> None:
    rows = categories.catalog_category_rows()
    for row in rows:
        key = row["category_key"]
        row["is_enabled"] = key in categories.SAFE_STARTER_KEYS
        row["is_default"] = key == "support"
    cfg = {
        "ticket_category_setup_required": True,
        "ticket_category_setup_version": 0,
        "ticket_category_setup_selected_keys": [],
    }
    assert categories._runtime_history_recovery_eligible(cfg, rows) is True

    custom = {
        "id": "custom-1",
        "slug": "custom_help",
        "name": "Custom Help",
        "is_enabled": True,
        "is_default": False,
        "managed_by_dank": False,
    }
    assert categories._runtime_history_recovery_eligible(cfg, [*rows, custom]) is False
    assert categories._runtime_history_recovery_eligible(
        {**cfg, "ticket_category_setup_selected_keys": ["support"]}, rows
    ) is False


def test_historical_managed_keys_ignore_disabled_and_custom_rows() -> None:
    snapshot = {
        "rows": [
            {"slug": "cod_services", "name": "COD Services", "is_enabled": True},
            {"slug": "staff_complaint", "name": "Staff Complaint", "is_enabled": True},
            {"slug": "partnership", "name": "Partnerships", "is_enabled": False},
            {"slug": "clan_help", "name": "Support", "is_enabled": True, "managed_by_dank": False},
        ]
    }
    assert categories._historical_managed_keys(snapshot) == (
        "cod-services",
        "staff-complaint",
    )


def test_erased_selection_runtime_recovery_keeps_review_required(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = categories.catalog_category_rows()
    for row in rows:
        key = row["category_key"]
        row["id"] = f"row-{key}"
        row["is_enabled"] = key in categories.SAFE_STARTER_KEYS
        row["is_default"] = key == "support"

    before = {
        "ticket_category_setup_required": True,
        "ticket_category_setup_required_reason": "Old category review",
        "ticket_category_setup_version": 0,
        "ticket_category_setup_selected_keys": [],
    }
    recovered_cfg = {
        **before,
        "ticket_category_setup_selected_keys": [
            "report", "staff-complaint", "cod-services", "partnership", "support"
        ],
    }
    config_reads = iter([before, recovered_cfg])
    persisted: list[tuple[str, ...]] = []

    monkeypatch.setattr(categories, "_fetch_config_sync", lambda _gid: dict(next(config_reads)))
    monkeypatch.setattr(categories, "_fetch_rows_sync", lambda _gid: [dict(row) for row in rows])
    monkeypatch.setattr(categories, "_catalog_reconcile_needed", lambda _rows: False)
    monkeypatch.setattr(categories, "_saved_selection_reconcile_needed", lambda _rows, _cfg: False)
    monkeypatch.setattr(categories, "_claim_history_recovery_window", lambda _gid: True)
    monkeypatch.setattr(
        categories,
        "_recover_erased_selection_keys_sync",
        lambda _gid: ("report", "staff-complaint", "cod-services", "partnership", "support"),
    )
    monkeypatch.setattr(
        categories,
        "_persist_runtime_recovered_selection_sync",
        lambda _gid, keys, _rows: persisted.append(tuple(keys)),
    )

    state = categories.ensure_category_setup_state_sync(1234)
    assert persisted == [
        ("report", "staff-complaint", "cod-services", "partnership", "support")
    ]
    assert state.required is True
    assert state.version == 0
    assert state.selected_keys == (
        "report", "staff-complaint", "cod-services", "partnership", "support"
    )
    assert {categories.canonical_category_key(row) for row in state.active_rows if row.get("managed_by_dank")} == {
        "report", "staff-complaint", "cod-services", "partnership", "support"
    }
'''
tests += append
TESTS.write_text(tests, encoding='utf-8')
