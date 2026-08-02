from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


durable = ROOT / "stoney_verify" / "durable_invite_stats.py"
replace_once(
    durable,
    "import random\nimport time\n",
    "import random\nimport threading\nimport time\n",
    "threading import",
)
replace_once(
    durable,
    '''_RETRY_MAX_SECONDS = 60.0\n\n_GUILD_LOCKS: dict[int, asyncio.Lock] = {}\n''',
    '''_RETRY_MAX_SECONDS = 60.0\n_RECONCILE_CONCURRENCY = 8\n\n_GUILD_LOCKS: dict[int, asyncio.Lock] = {}\n''',
    "reconcile concurrency constant",
)
replace_once(
    durable,
    '''_RETRY_TASK: Optional[asyncio.Task[Any]] = None\n_INSTALLED = False\n''',
    '''_RETRY_TASK: Optional[asyncio.Task[Any]] = None\n_RECOVERY_TASK: Optional[asyncio.Task[Any]] = None\n_OUTBOX_FILE_LOCK = threading.Lock()\n_INSTALLED = False\n''',
    "recovery and outbox globals",
)
replace_once(
    durable,
    '''def _persist_outbox() -> None:\n    path = _outbox_path()\n    try:\n        path.parent.mkdir(parents=True, exist_ok=True)\n        payload = [event.to_json() for event in _PENDING.values()]\n        temporary = path.with_suffix(".tmp")\n        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")\n        temporary.replace(path)\n    except Exception as exc:\n        _warn(f"could not persist retry outbox: {type(exc).__name__}: {exc}")\n\n\ndef _load_outbox() -> None:\n''',
    '''def _persist_outbox(payload: Optional[list[dict[str, Any]]] = None) -> None:\n    path = _outbox_path()\n    try:\n        with _OUTBOX_FILE_LOCK:\n            path.parent.mkdir(parents=True, exist_ok=True)\n            snapshot = payload if payload is not None else [\n                event.to_json() for event in _PENDING.values()\n            ]\n            temporary = path.with_suffix(".tmp")\n            temporary.write_text(\n                json.dumps(snapshot, separators=(",", ":")),\n                encoding="utf-8",\n            )\n            temporary.replace(path)\n    except Exception as exc:\n        _warn(f"could not persist retry outbox: {type(exc).__name__}: {exc}")\n\n\nasync def _persist_outbox_async() -> None:\n    # Take the cheap immutable snapshot on the event loop, then move JSON\n    # serialization and filesystem I/O to a worker thread. The file lock keeps\n    # coalesced retry and moderation writes from racing on the same temp path.\n    snapshot = [event.to_json() for event in list(_PENDING.values())]\n    await asyncio.to_thread(_persist_outbox, snapshot)\n\n\ndef _load_outbox() -> None:\n''',
    "nonblocking outbox persistence",
)
replace_once(
    durable,
    '''def _queue_pending(event: PendingInviteEvent) -> None:\n    existing = _PENDING.get(event.event_hash)\n    if existing is None or event.blocked_count > existing.blocked_count:\n        _PENDING[event.event_hash] = event\n    _persist_outbox()\n    _ensure_retry_task()\n''',
    '''async def _queue_pending(event: PendingInviteEvent) -> None:\n    existing = _PENDING.get(event.event_hash)\n    if existing is None or event.blocked_count > existing.blocked_count:\n        _PENDING[event.event_hash] = event\n    await _persist_outbox_async()\n    _ensure_retry_task()\n''',
    "async pending queue",
)
replace_once(
    durable,
    '''                    _PENDING.pop(event_hash, None)\n                    _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)\n                    _persist_outbox()\n                    await _sync_compatibility_count(event.guild_id, result.invites_blocked)\n''',
    '''                    _PENDING.pop(event_hash, None)\n                    _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)\n                    await _persist_outbox_async()\n                    await _sync_compatibility_count(event.guild_id, result.invites_blocked)\n''',
    "async retry outbox persistence",
)
replace_once(
    durable,
    '''        except Exception as exc:\n            _queue_pending(event)\n            _warn(\n''',
    '''        except Exception as exc:\n            await _queue_pending(event)\n            _warn(\n''',
    "await pending queue",
)
replace_once(
    durable,
    '''        _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)\n        _PENDING.pop(event_hash, None)\n        _persist_outbox()\n        await _sync_compatibility_count(guild_id, result.invites_blocked)\n''',
    '''        _RECENT_EVENTS[event_hash] = (time.monotonic(), result.invites_blocked)\n        _PENDING.pop(event_hash, None)\n        await _persist_outbox_async()\n        await _sync_compatibility_count(guild_id, result.invites_blocked)\n''',
    "async successful write outbox persistence",
)
replace_once(
    durable,
    '''async def _on_ready() -> None:\n    _ensure_retry_task()\n    guilds = list(getattr(bot, "guilds", []) or [])\n    for guild in guilds:\n        try:\n            await reconcile_guild(int(guild.id))\n        except Exception as exc:\n            _warn(\n                f"startup reconcile failed guild={getattr(guild, 'id', 0)} "\n                f"error={type(exc).__name__}: {str(exc)[:180]}"\n            )\n\n\ndef install() -> bool:\n''',
    '''async def _run_startup_recovery() -> None:\n    _ensure_retry_task()\n    guilds = list(getattr(bot, "guilds", []) or [])\n    if not guilds:\n        return\n\n    semaphore = asyncio.Semaphore(max(1, int(_RECONCILE_CONCURRENCY)))\n\n    async def reconcile_one(guild: Any) -> None:\n        async with semaphore:\n            try:\n                await reconcile_guild(int(guild.id))\n            except Exception as exc:\n                _warn(\n                    f"startup reconcile failed guild={getattr(guild, 'id', 0)} "\n                    f"error={type(exc).__name__}: {str(exc)[:180]}"\n                )\n\n    await asyncio.gather(*(reconcile_one(guild) for guild in guilds))\n\n\ndef _schedule_startup_recovery() -> bool:\n    global _RECOVERY_TASK\n\n    try:\n        loop = asyncio.get_running_loop()\n    except RuntimeError:\n        loop = getattr(bot, "loop", None)\n        if loop is None or not bool(getattr(loop, "is_running", lambda: False)()):\n            return False\n        try:\n            loop.call_soon_threadsafe(_schedule_startup_recovery)\n            return True\n        except Exception:\n            return False\n\n    if _RECOVERY_TASK is not None and not _RECOVERY_TASK.done():\n        return True\n\n    task = loop.create_task(_run_startup_recovery())\n    _RECOVERY_TASK = task\n\n    def clear_finished(completed: asyncio.Task[Any]) -> None:\n        global _RECOVERY_TASK\n        if _RECOVERY_TASK is completed:\n            _RECOVERY_TASK = None\n        try:\n            completed.exception()\n        except asyncio.CancelledError:\n            pass\n        except Exception as exc:\n            _warn(\n                f"startup recovery task failed error={type(exc).__name__}: "\n                f"{str(exc)[:180]}"\n            )\n\n    task.add_done_callback(clear_finished)\n    return True\n\n\nasync def _on_ready() -> None:\n    # Recovery runs in its own bounded background task so this listener returns\n    # quickly and repeated ready events cannot start competing reconciliations.\n    _schedule_startup_recovery()\n\n\ndef install() -> bool:\n''',
    "bounded startup recovery",
)
replace_once(
    durable,
    '''        _INSTALLED = True\n        _log("active; atomic event ledger, retry outbox, and display reconciliation enabled")\n        return True\n''',
    '''        _INSTALLED = True\n        already_ready = False\n        try:\n            already_ready = bool(bot.is_ready())\n        except Exception:\n            already_ready = False\n        if already_ready and not _schedule_startup_recovery():\n            _warn("bot is already ready but startup recovery could not be scheduled")\n        _log(\n            "active; atomic event ledger, async retry outbox, and bounded "\n            "display reconciliation enabled"\n        )\n        return True\n''',
    "late import startup recovery",
)

# Update existing test helpers for async outbox ownership and add regressions for
# every review concern.
tests = ROOT / "tests" / "test_durable_invite_stats.py"
replace_once(
    tests,
    '''    durable_invite_stats._LAST_REFRESH_AT.clear()\n    monkeypatch.setattr(durable_invite_stats, "_persist_outbox", lambda: None)\n    monkeypatch.setattr(durable_invite_stats, "_ensure_retry_task", lambda: None)\n''',
    '''    durable_invite_stats._LAST_REFRESH_AT.clear()\n\n    async def no_persist() -> None:\n        return None\n\n    monkeypatch.setattr(durable_invite_stats, "_persist_outbox_async", no_persist)\n    monkeypatch.setattr(durable_invite_stats, "_ensure_retry_task", lambda: None)\n''',
    "async test outbox helper",
)
replace_once(
    tests,
    '''    def fake_queue(event):\n        queued.append(event)\n        durable_invite_stats._PENDING[event.event_hash] = event\n''',
    '''    async def fake_queue(event):\n        queued.append(event)\n        durable_invite_stats._PENDING[event.event_hash] = event\n''',
    "async fake pending queue",
)
with tests.open("a", encoding="utf-8") as handle:
    handle.write('''\n\ndef test_outbox_persistence_moves_json_and_file_work_off_event_loop(monkeypatch) -> None:\n    _clear_runtime_state(monkeypatch)\n    event = durable_invite_stats.PendingInviteEvent(\n        event_hash="d" * 64,\n        guild_id=1,\n        blocked_count=2,\n        seed_count=3,\n        source="test",\n    )\n    durable_invite_stats._PENDING[event.event_hash] = event\n    calls = []\n\n    def fake_persist(payload):\n        calls.append(payload)\n\n    async def fake_to_thread(function, *args):\n        calls.append("to_thread")\n        return function(*args)\n\n    monkeypatch.setattr(durable_invite_stats, "_persist_outbox", fake_persist)\n    monkeypatch.setattr(durable_invite_stats.asyncio, "to_thread", fake_to_thread)\n\n    asyncio.run(durable_invite_stats._persist_outbox_async())\n\n    assert calls[0] == "to_thread"\n    assert calls[1] == [event.to_json()]\n\n\ndef test_install_schedules_recovery_when_imported_after_bot_ready(monkeypatch) -> None:\n    listeners = []\n    scheduled = []\n\n    class FakeBot:\n        extra_events = {}\n\n        @staticmethod\n        def is_ready():\n            return True\n\n        @staticmethod\n        def add_listener(listener, event_name):\n            listeners.append((listener, event_name))\n\n    monkeypatch.setattr(durable_invite_stats, "_INSTALLED", False)\n    monkeypatch.setattr(durable_invite_stats, "bot", FakeBot())\n    monkeypatch.setattr(durable_invite_stats, "_load_outbox", lambda: None)\n    monkeypatch.setattr(\n        durable_invite_stats,\n        "_schedule_startup_recovery",\n        lambda: scheduled.append(True) or True,\n    )\n\n    assert durable_invite_stats.install() is True\n    assert listeners == [(durable_invite_stats._on_ready, "on_ready")]\n    assert scheduled == [True]\n\n\ndef test_startup_recovery_is_bounded_and_concurrent(monkeypatch) -> None:\n    guilds = [SimpleNamespace(id=index) for index in range(1, 25)]\n    active = 0\n    maximum_active = 0\n    completed = []\n    retry_started = []\n\n    async def fake_reconcile(guild_id: int):\n        nonlocal active, maximum_active\n        active += 1\n        maximum_active = max(maximum_active, active)\n        await asyncio.sleep(0.005)\n        completed.append(guild_id)\n        active -= 1\n        return guild_id\n\n    monkeypatch.setattr(durable_invite_stats, "bot", SimpleNamespace(guilds=guilds))\n    monkeypatch.setattr(durable_invite_stats, "reconcile_guild", fake_reconcile)\n    monkeypatch.setattr(\n        durable_invite_stats,\n        "_ensure_retry_task",\n        lambda: retry_started.append(True),\n    )\n\n    asyncio.run(durable_invite_stats._run_startup_recovery())\n\n    assert retry_started == [True]\n    assert set(completed) == set(range(1, 25))\n    assert 1 < maximum_active <= durable_invite_stats._RECONCILE_CONCURRENCY\n''')

(ROOT / "tools" / "apply_durable_invite_stats_review_fixes.py").unlink(missing_ok=True)
(ROOT / ".github" / "workflows" / "apply-durable-invite-stats-review-fixes.yml").unlink(missing_ok=True)
