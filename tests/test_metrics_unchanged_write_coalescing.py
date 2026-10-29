"""Keep staff/role metrics live on change, without endlessly rewriting identical rows."""
from __future__ import annotations

from types import SimpleNamespace

from stoney_verify.workers import metrics_sync_worker as worker


class _Execute:
    def __init__(self, result=None):
        self.data = result if result is not None else []

class _Table:
    def __init__(self, store, name):
        self.store = store
        self.name = name
    def upsert(self, rows, on_conflict=None):
        self.store.append((self.name, list(rows)))
        return self
    def select(self, *_args):
        return self
    def eq(self, *_args):
        return self
    def execute(self):
        if self.name == "guild_members":
            return _Execute([{"user_id":"300","username":"mod"}])
        return _Execute()

class _DB:
    def __init__(self):
        self.writes = []
    def table(self, name):
        return _Table(self.writes, name)


def _role(count):
    return SimpleNamespace(
        id=200, name="Members", position=2, members=[object()] * count
    )


def test_role_writes_only_when_changed_or_hourly_due(monkeypatch):
    db = _DB()
    monkeypatch.setattr(worker, "get_supabase", lambda **_kwargs: db)
    worker._LAST_ROLE_SIGNATURES.clear()
    guild = SimpleNamespace(id=987654, roles=[_role(1)])
    assert worker._sync_role_metrics(guild) == 1
    assert len(db.writes) == 1
    assert worker._sync_role_metrics(guild) == 1
    assert len(db.writes) == 1

    guild.roles = [_role(2)]
    worker._sync_role_metrics(guild)
    assert len(db.writes) == 2

    stamp, fingerprint = worker._LAST_ROLE_SIGNATURES[987654]
    worker._LAST_ROLE_SIGNATURES[987654] = (
        stamp - worker._FULL_METRICS_RESYNC_SECONDS - 1, fingerprint
    )
    worker._sync_role_metrics(guild)
    assert len(db.writes) == 3
    worker._LAST_ROLE_SIGNATURES.clear()


def test_staff_upsert_deduplicated_but_select_remains_live(monkeypatch):
    db = _DB()
    monkeypatch.setattr(worker, "get_supabase", lambda **_kwargs: db)
    worker._LAST_STAFF_SIGNATURES.clear()
    guild = SimpleNamespace(id=987654)
    assert worker._sync_staff_metrics(guild) == 1
    assert worker._sync_staff_metrics(guild) == 1
    assert [name for name, _ in db.writes] == ["staff_metrics"]
    worker._LAST_STAFF_SIGNATURES.clear()


def test_failed_role_write_never_marks_snapshot_persisted(monkeypatch):
    db = _DB()
    def fail(_name, _executor):
        raise RuntimeError("storage unavailable")
    monkeypatch.setattr(worker, "get_supabase", lambda **_kwargs: db)
    monkeypatch.setattr(worker, "_execute_db_write", fail)
    worker._LAST_ROLE_SIGNATURES.clear()
    guild = SimpleNamespace(id=987654, roles=[_role(1)])
    import pytest
    with pytest.raises(RuntimeError, match="storage unavailable"):
        worker._sync_role_metrics(guild)
    assert 987654 not in worker._LAST_ROLE_SIGNATURES
