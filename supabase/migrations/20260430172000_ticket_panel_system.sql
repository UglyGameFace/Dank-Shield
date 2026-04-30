-- ============================================================
-- Ticket Panel System Tables
-- ------------------------------------------------------------
-- Supports public multi-panel ticket setup, per-panel category
-- bindings, per-panel behavior rules, and reusable presets.
--
-- These tables are intentionally guild-scoped so one public bot
-- deployment can safely serve many Discord servers without leaking
-- setup/config between guilds.
-- ============================================================

create extension if not exists pgcrypto;

-- ============================================================
-- updated_at helper
-- ============================================================
create or replace function public.set_current_timestamp_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ============================================================
-- ticket_panels
-- ------------------------------------------------------------
-- One row per configured public ticket panel in a guild.
-- panel_key is the stable slug used by commands/runtime.
-- ============================================================
create table if not exists public.ticket_panels (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  panel_key text not null,
  panel_name text not null,

  panel_channel_id text null,
  panel_message_id text null,

  panel_style text not null default 'buttons',
  prompt_title text null,
  prompt_description text null,
  embed_title text null,
  embed_description text null,
  button_label text null,
  menu_placeholder text null,

  preset_key text null,
  is_enabled boolean not null default true,
  sort_order integer not null default 0,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint ticket_panels_guild_panel_key_unique unique (guild_id, panel_key),
  constraint ticket_panels_panel_style_check check (panel_style in ('buttons', 'select', 'hybrid', 'modal')),
  constraint ticket_panels_panel_key_not_blank check (length(trim(panel_key)) > 0),
  constraint ticket_panels_panel_name_not_blank check (length(trim(panel_name)) > 0)
);

create index if not exists idx_ticket_panels_guild_enabled_sort
  on public.ticket_panels (guild_id, is_enabled, sort_order, panel_name);

create index if not exists idx_ticket_panels_message_lookup
  on public.ticket_panels (guild_id, panel_channel_id, panel_message_id)
  where panel_channel_id is not null and panel_message_id is not null;

drop trigger if exists set_ticket_panels_updated_at on public.ticket_panels;
create trigger set_ticket_panels_updated_at
before update on public.ticket_panels
for each row
execute function public.set_current_timestamp_updated_at();

-- ============================================================
-- ticket_panel_categories
-- ------------------------------------------------------------
-- Category whitelist/binding per panel. Empty bindings mean the
-- panel may use all enabled ticket_categories for the guild.
-- ============================================================
create table if not exists public.ticket_panel_categories (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  panel_key text not null,
  category_slug text not null,
  sort_order integer not null default 0,
  is_enabled boolean not null default true,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint ticket_panel_categories_unique unique (guild_id, panel_key, category_slug),
  constraint ticket_panel_categories_panel_key_not_blank check (length(trim(panel_key)) > 0),
  constraint ticket_panel_categories_slug_not_blank check (length(trim(category_slug)) > 0),
  constraint ticket_panel_categories_panel_fk foreign key (guild_id, panel_key)
    references public.ticket_panels (guild_id, panel_key)
    on update cascade
    on delete cascade
);

create index if not exists idx_ticket_panel_categories_guild_panel_sort
  on public.ticket_panel_categories (guild_id, panel_key, is_enabled, sort_order, category_slug);

drop trigger if exists set_ticket_panel_categories_updated_at on public.ticket_panel_categories;
create trigger set_ticket_panel_categories_updated_at
before update on public.ticket_panel_categories
for each row
execute function public.set_current_timestamp_updated_at();

-- ============================================================
-- ticket_panel_rules
-- ------------------------------------------------------------
-- Runtime behavior for each panel.
-- These are intentionally conservative defaults that match the
-- current Python DEFAULT_PANEL_RULES fallback.
-- ============================================================
create table if not exists public.ticket_panel_rules (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  panel_key text not null,

  cooldown_seconds integer not null default 0,
  max_tickets_per_window integer not null default 0,
  window_minutes integer not null default 0,

  auto_close_enabled boolean not null default false,
  auto_close_minutes integer not null default 1440,

  inactivity_reminders_enabled boolean not null default true,
  inactivity_reminder_minutes integer not null default 240,

  staff_alert_channel_id text null,

  allow_unverified boolean not null default true,
  allow_verified boolean not null default true,
  allow_resident boolean not null default true,
  allow_staff boolean not null default true,
  allow_unknown_members boolean not null default true,

  ghost_allowed boolean not null default false,
  transcript_mode text not null default 'on_close',
  close_confirmation_required boolean not null default true,
  per_owner_open_limit integer not null default 1,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint ticket_panel_rules_unique unique (guild_id, panel_key),
  constraint ticket_panel_rules_panel_key_not_blank check (length(trim(panel_key)) > 0),
  constraint ticket_panel_rules_panel_fk foreign key (guild_id, panel_key)
    references public.ticket_panels (guild_id, panel_key)
    on update cascade
    on delete cascade,

  constraint ticket_panel_rules_cooldown_nonnegative check (cooldown_seconds >= 0),
  constraint ticket_panel_rules_window_nonnegative check (window_minutes >= 0),
  constraint ticket_panel_rules_max_window_nonnegative check (max_tickets_per_window >= 0),
  constraint ticket_panel_rules_auto_close_min check (auto_close_minutes >= 5),
  constraint ticket_panel_rules_inactivity_min check (inactivity_reminder_minutes >= 1),
  constraint ticket_panel_rules_owner_limit_min check (per_owner_open_limit >= 1),
  constraint ticket_panel_rules_transcript_mode_check check (transcript_mode in ('always', 'on_close', 'manual', 'disabled'))
);

create index if not exists idx_ticket_panel_rules_guild_panel
  on public.ticket_panel_rules (guild_id, panel_key);

create index if not exists idx_ticket_panel_rules_auto_close
  on public.ticket_panel_rules (guild_id, auto_close_enabled, auto_close_minutes)
  where auto_close_enabled = true;

create index if not exists idx_ticket_panel_rules_inactivity
  on public.ticket_panel_rules (guild_id, inactivity_reminders_enabled, inactivity_reminder_minutes)
  where inactivity_reminders_enabled = true;

drop trigger if exists set_ticket_panel_rules_updated_at on public.ticket_panel_rules;
create trigger set_ticket_panel_rules_updated_at
before update on public.ticket_panel_rules
for each row
execute function public.set_current_timestamp_updated_at();

-- ============================================================
-- ticket_panel_presets
-- ------------------------------------------------------------
-- Reusable server-local presets used when creating/configuring
-- panels. These are guild-scoped, not global, so server owners can
-- customize without affecting other servers.
-- ============================================================
create table if not exists public.ticket_panel_presets (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  preset_key text not null,
  preset_name text not null,

  panel_style text not null default 'buttons',
  default_prompt_title text null,
  default_prompt_description text null,
  default_embed_title text null,
  default_embed_description text null,
  default_button_label text null,
  default_menu_placeholder text null,
  default_rules_json jsonb not null default '{}'::jsonb,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint ticket_panel_presets_unique unique (guild_id, preset_key),
  constraint ticket_panel_presets_preset_key_not_blank check (length(trim(preset_key)) > 0),
  constraint ticket_panel_presets_name_not_blank check (length(trim(preset_name)) > 0),
  constraint ticket_panel_presets_panel_style_check check (panel_style in ('buttons', 'select', 'hybrid', 'modal'))
);

create index if not exists idx_ticket_panel_presets_guild_name
  on public.ticket_panel_presets (guild_id, preset_name);

drop trigger if exists set_ticket_panel_presets_updated_at on public.ticket_panel_presets;
create trigger set_ticket_panel_presets_updated_at
before update on public.ticket_panel_presets
for each row
execute function public.set_current_timestamp_updated_at();

-- ============================================================
-- Comments
-- ============================================================
comment on table public.ticket_panels is 'Guild-scoped public ticket panel configuration.';
comment on table public.ticket_panel_categories is 'Per-panel category bindings/whitelists.';
comment on table public.ticket_panel_rules is 'Per-panel runtime behavior rules: cooldowns, limits, reminders, transcripts.';
comment on table public.ticket_panel_presets is 'Guild-scoped reusable panel templates/presets.';
