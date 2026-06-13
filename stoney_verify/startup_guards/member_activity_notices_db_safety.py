from __future__ import annotations

"""Keep optional member-activity notice DB work from blocking Discord.

The Supabase Python client used by the bot is synchronous. If those PostgREST
calls run directly inside discord.py callbacks/workers, a slow HTTP/2 request can
block the event loop long enough to miss Discord heartbeats. This guard keeps the
existing optional notice feature fail-open: memory state is always updated first,
DB work is bounded, and slow optional DB reads/writes degrade to memory-only
instead of freezing setup, slash commands, or interactions.
"""

import concurrent.futures
from typing import Any, Mapping, Optional

_NOTICE_DB_TIMEOUT_SECONDS = 2.5
_NOTICE_SELECT_LIMIT_MAX = 250
_NOTICE_DB_WORKERS = 4
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=_NOTICE_DB_WORKERS,
    thread_name_prefix="dank_notice_db",
)
_PATCHED = False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _run_optional_db_call(label: str, fn: Any, *, timeout: float = _NOTICE_DB_TIMEOUT_SECONDS) -> tuple[Any, str]:
    """Run one synchronous optional DB call with a hard wait budget."""

    future = _EXECUTOR.submit(fn)
    try:
        return future.result(timeout=float(timeout)), ""
    except concurrent.futures.TimeoutError:
        future.cancel()
        return None, f"Optional member activity notice DB call timed out for `{label}`. Using memory-only results for this cycle."
    except Exception as exc:
        return None, f"Optional member activity notice DB call failed for `{label}` ({type(exc).__name__}). Using memory-only results."


def _install() -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        from stoney_verify.commands_ext import public_members_group as mod
    except Exception as exc:
        print(f"⚠️ member_activity_notices_db_safety could not import public_members_group: {exc!r}")
        return

    def _patched_upsert_notice_row(row: dict[str, Any]) -> tuple[bool, str]:
        table = mod._notice_table()
        mod._memory_upsert_notice(row)
        if table is None:
            return False, "Notice saved in memory only because Supabase is unavailable."

        _resp, warning = _run_optional_db_call(
            "upsert_notice_row",
            lambda: table.upsert(row, on_conflict="notice_id").execute(),
        )
        if warning:
            return False, warning
        return True, "Notice saved."

    def _patched_update_notice_row(notice_id: str, **fields: Any) -> tuple[bool, str]:
        notice_id_str = str(notice_id)
        payload = dict(fields)
        payload["updated_at"] = mod._utcnow().isoformat()
        mod._memory_update_notice(notice_id_str, **payload)

        table = mod._notice_table()
        if table is None:
            return False, "Notice updated in memory only."

        _resp, warning = _run_optional_db_call(
            "update_notice_row",
            lambda: table.update(payload).eq("notice_id", notice_id_str).execute(),
        )
        if warning:
            return False, warning
        return True, "Notice updated."

    def _patched_select_notice_rows(
        *,
        guild_id: Optional[int] = None,
        user_id: Optional[int] = None,
        limit: int = 500,
    ) -> tuple[list[dict[str, Any]], str]:
        safe_limit = max(1, min(_safe_int(limit, 100), _NOTICE_SELECT_LIMIT_MAX))
        rows: list[dict[str, Any]] = []
        warning = ""
        table = mod._notice_table()

        if table is not None:
            def _execute_select() -> Any:
                query = table.select("*")
                if guild_id is not None:
                    query = query.eq("guild_id", str(int(guild_id)))
                if user_id is not None:
                    query = query.eq("user_id", str(int(user_id)))
                return query.limit(safe_limit).execute()

            resp, warning = _run_optional_db_call("select_notice_rows", _execute_select)
            if resp is not None:
                rows = [dict(r) for r in (getattr(resp, "data", None) or []) if isinstance(r, Mapping)]

        seen = {str(r.get("notice_id")) for r in rows}
        for row in mod._NOTICE_MEMORY.values():
            if guild_id is not None and str(row.get("guild_id")) != str(int(guild_id)):
                continue
            if user_id is not None and str(row.get("user_id")) != str(int(user_id)):
                continue
            if str(row.get("notice_id")) in seen:
                continue
            rows.append(dict(row))

        rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
        return rows[:safe_limit], warning

    def _patched_due_notice_rows(now: Optional[Any] = None, *, limit: int = 25) -> tuple[list[dict[str, Any]], str]:
        current = now or mod._utcnow()
        rows, warning = _patched_select_notice_rows(limit=_NOTICE_SELECT_LIMIT_MAX)
        due: list[dict[str, Any]] = []
        for row in rows:
            status = str(row.get("status") or "")
            send_at = mod._coerce_utc(row.get("send_at"))
            if status == mod._NOTICE_STATUS_SCHEDULED and send_at is not None and send_at <= current:
                due.append(row)
        due.sort(key=lambda r: str(r.get("send_at") or ""))
        return due[: max(1, min(_safe_int(limit, 25), 50))], warning

    async def _patched_expire_passed_notice_deadlines() -> None:
        now = mod._utcnow()
        rows, warning = _patched_select_notice_rows(limit=_NOTICE_SELECT_LIMIT_MAX)
        if warning:
            print(f"⚠️ member activity notices: {warning}")
        for row in rows:
            status = str(row.get("status") or "")
            if status not in {mod._NOTICE_STATUS_DELIVERED, mod._NOTICE_STATUS_SCHEDULED}:
                continue
            deadline = mod._coerce_utc(row.get("deadline_at"))
            if deadline is not None and deadline < now:
                _patched_update_notice_row(str(row.get("notice_id")), status=mod._NOTICE_STATUS_DEADLINE_PASSED)

    mod._upsert_notice_row = _patched_upsert_notice_row
    mod._update_notice_row = _patched_update_notice_row
    mod._select_notice_rows = _patched_select_notice_rows
    mod._due_notice_rows = _patched_due_notice_rows
    mod._expire_passed_notice_deadlines = _patched_expire_passed_notice_deadlines

    _PATCHED = True
    print(
        "🛡️ member_activity_notices_db_safety active; optional notice Supabase calls are timeout-bounded "
        f"timeout={_NOTICE_DB_TIMEOUT_SECONDS}s max_select={_NOTICE_SELECT_LIMIT_MAX} workers={_NOTICE_DB_WORKERS}"
    )


_install()

__all__ = []
