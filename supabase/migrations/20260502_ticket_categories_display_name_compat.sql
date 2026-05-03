-- Ticket category compatibility for auto-build/setup discovery
-- ------------------------------------------------------------
-- Fixes Supabase/PostgREST errors like:
--   PGRST204: Could not find the 'display_name' column of 'ticket_categories' in the schema cache
--
-- Some older installs already have ticket_categories, but without the newer
-- display_name field used by the setup auto-builder. This migration is safe to
-- run repeatedly and does not overwrite existing category data.

create table if not exists ticket_categories (
  id bigserial primary key,
  guild_id text not null,
  category_key text not null,
  display_name text null,
  discord_category_id text null,
  category_type text not null default 'ticket',
  is_enabled boolean not null default true,
  sort_order integer not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table ticket_categories
  add column if not exists guild_id text,
  add column if not exists category_key text,
  add column if not exists display_name text,
  add column if not exists discord_category_id text,
  add column if not exists category_type text not null default 'ticket',
  add column if not exists is_enabled boolean not null default true,
  add column if not exists sort_order integer not null default 0,
  add column if not exists metadata jsonb not null default '{}'::jsonb,
  add column if not exists created_at timestamptz not null default now(),
  add column if not exists updated_at timestamptz not null default now();

-- Backfill display_name without assuming every legacy schema has the same
-- column names. Dynamic SQL avoids failing when optional old columns do not
-- exist.
do $$
declare
  has_name boolean;
  has_category_name boolean;
  has_slug boolean;
  has_key boolean;
begin
  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'ticket_categories'
      and column_name = 'name'
  ) into has_name;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'ticket_categories'
      and column_name = 'category_name'
  ) into has_category_name;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'ticket_categories'
      and column_name = 'slug'
  ) into has_slug;

  select exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'ticket_categories'
      and column_name = 'key'
  ) into has_key;

  if has_name then
    execute 'update ticket_categories set display_name = coalesce(display_name, nullif(name::text, '''')) where display_name is null';
  end if;

  if has_category_name then
    execute 'update ticket_categories set display_name = coalesce(display_name, nullif(category_name::text, '''')) where display_name is null';
  end if;

  if has_slug then
    execute 'update ticket_categories set display_name = coalesce(display_name, nullif(slug::text, '''')) where display_name is null';
  end if;

  if has_key then
    execute 'update ticket_categories set category_key = coalesce(category_key, nullif(key::text, '''')) where category_key is null';
    execute 'update ticket_categories set display_name = coalesce(display_name, nullif(key::text, '''')) where display_name is null';
  end if;

  update ticket_categories
  set category_key = coalesce(nullif(category_key, ''), lower(regexp_replace(coalesce(display_name, discord_category_id, id::text), '[^a-zA-Z0-9]+', '-', 'g')))
  where category_key is null or category_key = '';

  update ticket_categories
  set display_name = coalesce(nullif(display_name, ''), category_key, discord_category_id, id::text)
  where display_name is null or display_name = '';
end $$;

create index if not exists idx_ticket_categories_guild_id
  on ticket_categories (guild_id);

create index if not exists idx_ticket_categories_discord_category_id
  on ticket_categories (discord_category_id);

create unique index if not exists uq_ticket_categories_guild_key
  on ticket_categories (guild_id, category_key);

create or replace function set_ticket_categories_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_ticket_categories_updated_at on ticket_categories;
create trigger trg_ticket_categories_updated_at
before update on ticket_categories
for each row
execute function set_ticket_categories_updated_at();

-- Supabase/PostgREST can keep a stale schema cache briefly after DDL. This
-- nudges it to reload so API writes stop failing with PGRST204.
notify pgrst, 'reload schema';
