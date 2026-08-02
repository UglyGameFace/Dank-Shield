from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def apply_source_fixes() -> None:
    path = ROOT / "stoney_verify" / "durable_invite_stats.py"

    replace_once(
        path,
        "import random\nimport time\n",
        "import random\nimport threading\nimport time\n",
        "threading import",
    )
    replace_once(
        path,
        "_RETRY_MAX_SECONDS = 60.0\n\n_GUILD_LOCKS: dict[int, asyncio.Lock] = {}",
        "_RETRY_MAX_SECONDS = 60.0\n_RECONCILE_CONCURRENCY = 8\n\n_GUILD_LOCKS: dict[int, asyncio.Lock] = {}",
        "reconcile concurrency",
    )
    replace_once(
        path,
        "_RETRY_TASK: Optional[asyncio.Task[Any]] = None\n_INSTALLED = False",
        "_RETRY_TASK: Optional[asyncio.Task[Any]] = None\n_RECOVERY_TASK: Optional[asyncio.Task[Any]] = None\n_OUTBOX_FILE_LOCK = threading.Lock()\n_INSTALLED = False",
        "recovery globals",
    )

    replace_once(
        path,
        '''def _persist_outbox() -> None:
    path = _outbox_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [event.to_json() for event in _PENDING.values()]
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)
    except Exception as exc:
        _warn(f"could not persist retry outbox: {type(exc).__name__}: {exc}")


def _load_outbox() -> None:
''',
        '''def _persist_outbox(payload: Optional[list[dict[str, Any]]] = None) -> None:
    path = _outbox_path()
    try:
        with _OUTBOX_FILE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            snapshot = payload if payload is not None else [
                event.to_json() for event in _PENDING.values()
            ]
            temporary = path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(snapshot, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
    except Exception as exc:
        _warn(f"could not persist retry outbox: {type(exc).__name__}: {exc}")


async def _persist_outbox_async() -> None:
    """Persist an immutable pending-event snapshot without blocking Discord."""

    snapshot = [event.to_json() for event in list(_PENDING.values())]
    await asyncio.to_thread(_persist_outbox, snapshot)


def _load_outbox() -> None:
''',
        "async outbox persistence",
    )

    replace_once(
        path,
        '''def _queue_pending(event: PendingInviteEvent) -> None:
    existing = _PENDING.get(event.event_hash)
    if existing is None or event.blocked_count > existing.blocked_count:
        _PENDING[event.event_hash] = event
    _persist_outbox()
    _ensure_retry_task()
''',
        '''async def _queue_pending(event: PendingInviteEvent) -> None:
    existing = _PENDING.get(event.event_hash)
    if existing is None or event.blocked_count > existing.blocked_count:
        _PENDING[event.event_hash] = event
    await _persist_outbox_async()
    _ensure_retry_task()
''',
        "async pending queue",
    )
    replace_once(
        path,
        '''                    _PENDING.pop(event_hash, None)
                    _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)
                    _persist_outbox()
                    await _sync_compatibility_count(event.guild_id, result.invites_blocked)
''',
        '''                    _PENDING.pop(event_hash, None)
                    _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)
                    await _persist_outbox_async()
                    await _sync_compatibility_count(event.guild_id, result.invites_blocked)
''',
        "retry async persistence",
    )
    replace_once(
        path,
        '''        except Exception as exc:
            _queue_pending(event)
            _warn(
''',
        '''        except Exception as exc:
            await _queue_pending(event)
            _warn(
''',
        "await pending queue",
    )
    replace_once(
        path,
        '''        _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)
        _PENDING.pop(event_hash, None)
        _persist_outbox()
        await _sync_compatibility_count(guild_id, result.invites_blocked)
''',
        '''        _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)
        _PENDING.pop(event_hash, None)
        await _persist_outbox_async()
        await _sync_compatibility_count(guild_id, result.invites_blocked)
''',
        "success async persistence",
    )

    replace_once(
        path,
        '''async def _on_ready() -> None:
    _ensure_retry_task()
    guilds = list(getattr(bot, "guilds", []) or [])
    for guild in guilds:
        try:
            await reconcile_guild(int(guild.id))
        except Exception as exc:
            _warn(
                f"startup reconcile failed guild={getattr(guild, 'id', 0)} "
                f"error={type(exc).__name__}: {str(exc)[:180]}"
            )


def install() -> bool:
''',
        '''async def _run_startup_recovery() -> None:
    """Drain restored events and reconcile guild totals with bounded concurrency."""

    _ensure_retry_task()
    guilds = list(getattr(bot, "guilds", []) or [])
    if not guilds:
        return

    semaphore = asyncio.Semaphore(max(1, int(_RECONCILE_CONCURRENCY)))

    async def reconcile_one(guild: Any) -> None:
        async with semaphore:
            try:
                await reconcile_guild(int(guild.id))
            except Exception as exc:
                _warn(
                    f"startup reconcile failed guild={getattr(guild, 'id', 0)} "
                    f"error={type(exc).__name__}: {str(exc)[:180]}"
                )

    await asyncio.gather(*(reconcile_one(guild) for guild in guilds))


def _schedule_startup_recovery() -> bool:
    global _RECOVERY_TASK

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = getattr(bot, "loop", None)
        if loop is None or not bool(getattr(loop, "is_running", lambda: False)()):
            return False
        try:
            loop.call_soon_threadsafe(_schedule_startup_recovery)
            return True
        except Exception:
            return False

    if _RECOVERY_TASK is not None and not _RECOVERY_TASK.done():
        return True

    task = loop.create_task(_run_startup_recovery())
    _RECOVERY_TASK = task

    def clear_finished(completed: asyncio.Task[Any]) -> None:
        global _RECOVERY_TASK
        if _RECOVERY_TASK is completed:
            _RECOVERY_TASK = None
        try:
            error = completed.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            _warn(
                f"startup recovery task failed error={type(error).__name__}: "
                f"{str(error)[:180]}"
            )

    task.add_done_callback(clear_finished)
    return True


async def _on_ready() -> None:
    # Return quickly; repeated ready events share one recovery task.
    _schedule_startup_recovery()


def install() -> bool:
''',
        "bounded startup recovery",
    )
    replace_once(
        path,
        '''        _INSTALLED = True
        _log("active; atomic event ledger, retry outbox, and display reconciliation enabled")
        return True
''',
        '''        _INSTALLED = True
        already_ready = False
        try:
            already_ready = bool(bot.is_ready())
        except Exception:
            already_ready = False
        if already_ready and not _schedule_startup_recovery():
            _warn("bot is already ready but startup recovery could not be scheduled")
        _log(
            "active; atomic event ledger, async retry outbox, and bounded "
            "display reconciliation enabled"
        )
        return True
''',
        "late-import recovery",
    )


def apply_test_fixes() -> None:
    path = ROOT / "tests" / "test_durable_invite_stats.py"
    replace_once(
        path,
        '''    durable_invite_stats._LAST_REFRESH_AT.clear()
    monkeypatch.setattr(durable_invite_stats, "_persist_outbox", lambda: None)
    monkeypatch.setattr(durable_invite_stats, "_ensure_retry_task", lambda: None)
''',
        '''    durable_invite_stats._LAST_REFRESH_AT.clear()

    async def no_persist() -> None:
        return None

    monkeypatch.setattr(durable_invite_stats, "_persist_outbox_async", no_persist)
    monkeypatch.setattr(durable_invite_stats, "_ensure_retry_task", lambda: None)
''',
        "test async outbox mock",
    )
    replace_once(
        path,
        '''    def fake_queue(event):
        queued.append(event)
        durable_invite_stats._PENDING[event.event_hash] = event
''',
        '''    async def fake_queue(event):
        queued.append(event)
        durable_invite_stats._PENDING[event.event_hash] = event
''',
        "test async queue mock",
    )

    addition = '''


def test_outbox_persistence_moves_file_work_off_event_loop(monkeypatch) -> None:
    durable_invite_stats._PENDING.clear()
    event = durable_invite_stats.PendingInviteEvent(
        event_hash="d" * 64,
        guild_id=1,
        blocked_count=2,
        seed_count=3,
        source="test",
    )
    durable_invite_stats._PENDING[event.event_hash] = event
    calls = []

    def fake_persist(payload):
        calls.append(payload)

    async def fake_to_thread(function, *args):
        calls.append("to_thread")
        return function(*args)

    monkeypatch.setattr(durable_invite_stats, "_persist_outbox", fake_persist)
    monkeypatch.setattr(durable_invite_stats.asyncio, "to_thread", fake_to_thread)

    asyncio.run(durable_invite_stats._persist_outbox_async())

    assert calls[0] == "to_thread"
    assert calls[1] == [event.to_json()]


def test_install_schedules_recovery_when_loaded_after_ready(monkeypatch) -> None:
    listeners = []
    scheduled = []

    class FakeBot:
        extra_events = {}

        @staticmethod
        def is_ready():
            return True

        @staticmethod
        def add_listener(listener, event_name):
            listeners.append((listener, event_name))

    monkeypatch.setattr(durable_invite_stats, "_INSTALLED", False)
    monkeypatch.setattr(durable_invite_stats, "bot", FakeBot())
    monkeypatch.setattr(durable_invite_stats, "_load_outbox", lambda: None)
    monkeypatch.setattr(
        durable_invite_stats,
        "_schedule_startup_recovery",
        lambda: scheduled.append(True) or True,
    )

    assert durable_invite_stats.install() is True
    assert listeners == [(durable_invite_stats._on_ready, "on_ready")]
    assert scheduled == [True]


def test_startup_recovery_is_bounded_and_concurrent(monkeypatch) -> None:
    guilds = [SimpleNamespace(id=index) for index in range(1, 25)]
    active = 0
    maximum_active = 0
    completed = []
    retry_started = []

    async def fake_reconcile(guild_id: int):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.005)
        completed.append(guild_id)
        active -= 1
        return guild_id

    monkeypatch.setattr(durable_invite_stats, "bot", SimpleNamespace(guilds=guilds))
    monkeypatch.setattr(durable_invite_stats, "reconcile_guild", fake_reconcile)
    monkeypatch.setattr(
        durable_invite_stats,
        "_ensure_retry_task",
        lambda: retry_started.append(True),
    )

    asyncio.run(durable_invite_stats._run_startup_recovery())

    assert retry_started == [True]
    assert set(completed) == set(range(1, 25))
    assert 1 < maximum_active <= durable_invite_stats._RECONCILE_CONCURRENCY
'''
    text = path.read_text(encoding="utf-8")
    marker = "def test_outbox_persistence_moves_file_work_off_event_loop"
    if marker in text:
        raise RuntimeError("review regression tests already exist")
    path.write_text(text.rstrip() + addition + "\n", encoding="utf-8")


def cleanup() -> None:
    for relative in (
        "tools/apply_durable_review_fixes_v2.py",
        ".github/workflows/apply-durable-review-fixes-v2.yml",
        "tools/apply_durable_invite_stats_review_fixes.py",
        ".github/workflows/apply-durable-invite-stats-review-fixes.yml",
        "tools/fix_durable_review_patch_test.py",
    ):
        (ROOT / relative).unlink(missing_ok=True)


if __name__ == "__main__":
    apply_source_fixes()
    apply_test_fixes()
    cleanup()
