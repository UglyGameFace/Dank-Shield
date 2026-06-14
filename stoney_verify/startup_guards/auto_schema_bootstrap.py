from __future__ import annotations

"""Optional automatic database schema bootstrap.

Supabase's normal REST client cannot create missing tables through PostgREST.
This guard can create the bot's required tables only when a direct Postgres DSN
is provided through one of these environment variables:

- SUPABASE_DB_URL
- DATABASE_URL
- POSTGRES_URL
- POSTGRES_PRISMA_URL

The SQL is intentionally idempotent. It only creates missing tables/columns and
never drops user data.
"""

import asyncio
import os
from typing import Optional

import discord

_HAS_RUN = False
_TASK: Optional[asyncio.Task] = None


def _log(message: str) -> None:
    try:
        print(f"🧱 auto_schema_bootstrap {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ auto_schema_bootstrap {message}")
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


SCHEMA_SQL = r"""
create table if not exists public.ticket_counters (
    guild_id text primary key,
    last_ticket_number integer not null default 0,
    updated_at timestamptz not null default now()
);

create table if not exists public.ticket_categories (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    slug text not null,
    name text not null,
    description text,
    intake_type text not null default 'general',
    match_keywords jsonb not null default '[]'::jsonb,
    button_label text,
    sort_order integer not null default 999,
    is_default boolean not null default false,
    is_enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, slug)
);

create table if not exists public.tickets (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    user_id text,
    owner_id text,
    requester_id text,
    username text,
    title text,
    category text not null default 'support',
    status text not null default 'open',
    priority text not null default 'medium',
    channel_id text,
    discord_thread_id text,
    channel_name text,
    ticket_number integer,
    is_ghost boolean not null default false,
    assigned_to text,
    claimed_by text,
    matched_category_id text,
    matched_category_name text,
    matched_category_slug text,
    matched_intake_type text,
    matched_category_reason text,
    matched_category_score integer,
    category_override boolean not null default false,
    metadata jsonb not null default '{}'::jsonb,
    meta jsonb not null default '{}'::jsonb,
    transcript_url text,
    close_reason text,
    deleted_reason text,
    created_at timestamptz not null default now(),
    updated_at timestamptz,
    closed_at timestamptz,
    deleted_at timestamptz
);

create table if not exists public.ticket_notes (
    id uuid primary key default gen_random_uuid(),
    guild_id text,
    ticket_id text,
    channel_id text,
    author_id text,
    author_name text,
    note text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.ticket_messages (
    id uuid primary key default gen_random_uuid(),
    guild_id text,
    ticket_id text,
    channel_id text,
    message_id text,
    author_id text,
    author_name text,
    content text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.ticket_panels (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    panel_key text not null default 'default',
    panel_name text not null default 'Default Ticket Panel',
    panel_style text not null default 'buttons',
    prompt_description text,
    panel_channel_id text,
    panel_message_id text,
    is_enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, panel_key)
);

create table if not exists public.ticket_panel_categories (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    panel_key text not null default 'default',
    category_slug text not null,
    created_at timestamptz not null default now(),
    unique (guild_id, panel_key, category_slug)
);

create table if not exists public.ticket_panel_rules (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    panel_key text not null default 'default',
    per_owner_open_limit integer not null default 1,
    cooldown_seconds integer not null default 0,
    auto_close_enabled boolean not null default false,
    auto_close_minutes integer not null default 1440,
    inactivity_reminders_enabled boolean not null default true,
    inactivity_reminder_minutes integer not null default 240,
    allow_unverified boolean not null default true,
    allow_verified boolean not null default true,
    allow_resident boolean not null default true,
    allow_staff boolean not null default true,
    allow_unknown_members boolean not null default true,
    ghost_allowed boolean not null default false,
    transcript_mode text not null default 'on_close',
    close_confirmation_required boolean not null default true,
    staff_alert_channel_id text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, panel_key)
);

create table if not exists public.activity_feed_events (
    id uuid primary key default gen_random_uuid(),
    guild_id text,
    event_type text,
    actor_id text,
    target_id text,
    channel_id text,
    message text,
    metadata jsonb not null default '{}'::jsonb,
    meta jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.member_joins (
    id uuid primary key default gen_random_uuid(),
    guild_id text not null,
    user_id text not null,
    username text,
    joined_at timestamptz,
    departed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (guild_id, user_id)
);

create table if not exists public.member_activity_scan_locks (
    guild_id text not null,
    user_id text not null,
    active boolean not null default true,
    reason text,
    locked_by text,
    locked_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (guild_id, user_id)
);

create table if not exists public.member_cleanup_settings (
    guild_id text primary key,
    require_queue_confirmation boolean not null default true,
    allow_low_confidence_queue boolean not null default false,
    default_queue_limit integer not null default 10,
    updated_by text,
    updated_at timestamptz not null default now()
);

alter table public.tickets add column if not exists owner_id text;
alter table public.tickets add column if not exists requester_id text;
alter table public.tickets add column if not exists channel_id text;
alter table public.tickets add column if not exists discord_thread_id text;
alter table public.tickets add column if not exists ticket_number integer;
alter table public.tickets add column if not exists channel_name text;
alter table public.tickets add column if not exists metadata jsonb not null default '{}'::jsonb;
alter table public.tickets add column if not exists meta jsonb not null default '{}'::jsonb;
alter table public.tickets add column if not exists matched_category_id text;
alter table public.tickets add column if not exists matched_category_name text;
alter table public.tickets add column if not exists matched_category_slug text;
alter table public.tickets add column if not exists matched_intake_type text;
alter table public.tickets add column if not exists matched_category_reason text;
alter table public.tickets add column if not exists matched_category_score integer;
alter table public.tickets add column if not exists category_override boolean not null default false;

alter table public.ticket_categories add column if not exists button_label text;
alter table public.ticket_categories add column if not exists is_enabled boolean not null default true;
alter table public.ticket_categories add column if not exists match_keywords jsonb not null default '[]'::jsonb;

alter table public.member_activity_scan_locks add column if not exists active boolean not null default true;
alter table public.member_activity_scan_locks add column if not exists reason text;
alter table public.member_activity_scan_locks add column if not exists locked_by text;
alter table public.member_activity_scan_locks add column if not exists locked_at timestamptz not null default now();
alter table public.member_activity_scan_locks add column if not exists updated_at timestamptz not null default now();

alter table public.member_cleanup_settings add column if not exists require_queue_confirmation boolean not null default true;
alter table public.member_cleanup_settings add column if not exists allow_low_confidence_queue boolean not null default false;
alter table public.member_cleanup_settings add column if not exists default_queue_limit integer not null default 10;
alter table public.member_cleanup_settings add column if not exists updated_by text;
alter table public.member_cleanup_settings add column if not exists updated_at timestamptz not null default now();

create index if not exists idx_tickets_guild_status on public.tickets (guild_id, status);
create index if not exists idx_tickets_channel_id on public.tickets (channel_id);
create index if not exists idx_tickets_discord_thread_id on public.tickets (discord_thread_id);
create index if not exists idx_tickets_owner on public.tickets (guild_id, owner_id);
create index if not exists idx_ticket_categories_guild_sort on public.ticket_categories (guild_id, sort_order);
create index if not exists idx_member_activity_scan_locks_guild_active on public.member_activity_scan_locks (guild_id, active);
"""


def _execute_schema_sql_sync(url: str) -> None:
    try:
        import psycopg
    except Exception as e:
        raise RuntimeError("psycopg is not installed. Install requirements.txt after this update.") from e

    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)


async def ensure_schema_once() -> bool:
    global _HAS_RUN
    if _HAS_RUN:
        return True
    _HAS_RUN = True

    if not _env_bool("STONEY_AUTO_SCHEMA_BOOTSTRAP", True):
        _log("disabled by STONEY_AUTO_SCHEMA_BOOTSTRAP=false")
        return False

    url = _db_url()
    if not url:
        _log(
            "direct bootstrap skipped; no SUPABASE_DB_URL/DATABASE_URL set. "
            "Manual SQL migrations / REST schema health own production table readiness."
        )
        return False

    try:
        await asyncio.to_thread(_execute_schema_sql_sync, url)
        _log("required tables/columns verified")
        return True
    except Exception as e:
        _warn(f"schema bootstrap failed: {type(e).__name__}: {e}")
        return False


def _schedule_schema_bootstrap(bot: discord.Client) -> None:
    global _TASK
    if _TASK is not None and not _TASK.done():
        return
    try:
        _TASK = bot.loop.create_task(ensure_schema_once())
    except Exception as e:
        _warn(f"could not schedule schema bootstrap: {e!r}")


def _attach_listener() -> None:
    try:
        from ..globals import bot
    except Exception as e:
        _warn(f"could not import bot for listener: {e!r}")
        return

    if getattr(bot, "_stoney_auto_schema_bootstrap_attached", False):
        return

    @bot.listen("on_ready")
    async def _auto_schema_bootstrap_on_ready() -> None:
        await ensure_schema_once()

    try:
        setattr(bot, "_stoney_auto_schema_bootstrap_attached", True)
    except Exception:
        pass
    _log("listener attached")


_attach_listener()

__all__ = ["ensure_schema_once", "SCHEMA_SQL"]
