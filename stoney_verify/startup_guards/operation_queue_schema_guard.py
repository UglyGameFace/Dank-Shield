from __future__ import annotations

"""Optional schema bootstrap for the shared operation queue.

Kept separate from the older auto_schema_bootstrap module so the queue can be
added safely without rewriting the existing ticket/member schema block.
"""

import asyncio
import os
from typing import Optional

import discord

_HAS_RUN = False
_TASK: Optional[asyncio.Task] = None
MIGRATION_PATH = "supabase/migrations/20260613_bot_operation_jobs.sql"

SCHEMA_SQL = r"""
create table if not exists public.bot_operation_jobs (
    id uuid primary key,
    guild_id text not null,
    actor_id text,
    operation_type text not null,
    risk_level text not null,
    source text not null,
    idempotency_key text not null,
    payload_hash text not null,
    status text not null,
    progress_current integer not null default 0,
    progress_total integer not null default 0,
    result_json jsonb not null default '{}'::jsonb,
    error_code text,
    error_message text,
    locked_by text,
    lock_expires_at timestamptz,
    created_at timestamptz not null default now(),
    started_at timestamptz,
    finished_at timestamptz,
    unique (guild_id, idempotency_key)
);

alter table public.bot_operation_jobs add column if not exists actor_id text;
alter table public.bot_operation_jobs add column if not exists operation_type text not null default 'operation';
alter table public.bot_operation_jobs add column if not exists risk_level text not null default 'moderate';
alter table public.bot_operation_jobs add column if not exists source text not null default 'system';
alter table public.bot_operation_jobs add column if not exists idempotency_key text not null default '';
alter table public.bot_operation_jobs add column if not exists payload_hash text not null default '';
alter table public.bot_operation_jobs add column if not exists status text not null default 'queued';
alter table public.bot_operation_jobs add column if not exists progress_current integer not null default 0;
alter table public.bot_operation_jobs add column if not exists progress_total integer not null default 0;
alter table public.bot_operation_jobs add column if not exists result_json jsonb not null default '{}'::jsonb;
alter table public.bot_operation_jobs add column if not exists error_code text;
alter table public.bot_operation_jobs add column if not exists error_message text;
alter table public.bot_operation_jobs add column if not exists locked_by text;
alter table public.bot_operation_jobs add column if not exists lock_expires_at timestamptz;
alter table public.bot_operation_jobs add column if not exists started_at timestamptz;
alter table public.bot_operation_jobs add column if not exists finished_at timestamptz;

create index if not exists idx_bot_operation_jobs_guild_status_created
    on public.bot_operation_jobs (guild_id, status, created_at desc);

create index if not exists idx_bot_operation_jobs_type_status_created
    on public.bot_operation_jobs (operation_type, status, created_at desc);

create index if not exists idx_bot_operation_jobs_lock_expires
    on public.bot_operation_jobs (lock_expires_at)
    where lock_expires_at is not null;
"""


def _log(message: str) -> None:
    try:
        print(f"🧱 operation_queue_schema {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ operation_queue_schema {message}")
    except Exception:
        pass


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "y", "on"}


def _db_url() -> str:
    for name in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL"):
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _execute_schema_sql_sync(url: str) -> None:
    try:
        import psycopg
    except Exception as e:
        raise RuntimeError("psycopg is not installed; operation queue persistence schema cannot be bootstrapped") from e

    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)


async def ensure_schema_once() -> bool:
    global _HAS_RUN
    if _HAS_RUN:
        return True
    _HAS_RUN = True

    if not _env_bool("DANK_AUTO_SCHEMA_BOOTSTRAP", True):
        _log("disabled by DANK_AUTO_SCHEMA_BOOTSTRAP=false")
        return False

    url = _db_url()
    if not url:
        _log(
            "direct bootstrap skipped; no SUPABASE_DB_URL/DATABASE_URL set. "
            f"Manual migration path: {MIGRATION_PATH}. REST persistence health will report table visibility."
        )
        return False
    try:
        await asyncio.to_thread(_execute_schema_sql_sync, url)
        _log("bot_operation_jobs table/indexes verified")
        return True
    except Exception as e:
        _warn(f"schema bootstrap failed: {type(e).__name__}: {e}; run {MIGRATION_PATH} manually if needed")
        return False


def _attach_listener() -> None:
    try:
        from ..globals import bot
    except Exception as e:
        _warn(f"could not import bot for listener: {e!r}")
        return
    if getattr(bot, "_stoney_operation_queue_schema_attached", False):
        return

    @bot.listen("on_ready")
    async def _operation_queue_schema_on_ready() -> None:
        await ensure_schema_once()

    try:
        setattr(bot, "_stoney_operation_queue_schema_attached", True)
    except Exception:
        pass
    _log("listener attached")


_attach_listener()

__all__ = ["ensure_schema_once", "SCHEMA_SQL", "MIGRATION_PATH"]
